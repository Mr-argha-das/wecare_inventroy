"""Feather-file database layer.

All persistent data lives in Apache Feather files under ``data/`` and is
accessed with pandas / pyarrow. Writes are crash-safe:

    write temporary file -> validate -> atomic replace

plus per-table locks so concurrent requests cannot corrupt files.
"""
import json
import logging
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .config import DATA_DIR, TIMEZONE

logger = logging.getLogger("wecare.database")

TZ = ZoneInfo(TIMEZONE)

# ---------------------------------------------------------------------------
# Schemas: table name -> ordered {column: default}
# ---------------------------------------------------------------------------
SCHEMAS: dict[str, dict[str, object]] = {
    "patients": {
        "patient_id": "",
        "patient_name": "",
        "mobile": "",
        "address": "",
        "attendant_name": "",
        "doctor_name": "",
        "created_at": "",
        "updated_at": "",
        "status": "Active",
    },
    "services": {
        "service_id": "",
        "service_name": "",
        "description": "",
        "default_rate": 0.0,
        "billing_type": "Per Day",
        "status": "Active",
        "created_at": "",
        "updated_at": "",
    },
    "equipment": {
        "equipment_id": "",
        "equipment_name": "",
        "serial_number": "",
        "description": "",
        "status": "Available",
        "purchase_cost": 0.0,
        "sale_price": 0.0,
        "daily_rate": 0.0,
        "weekly_rate": 0.0,
        "monthly_rate": 0.0,
        "created_at": "",
        "updated_at": "",
    },
    "bills": {
        "bill_id": "",
        "bill_number": "",
        "patient_id": "",
        "bill_type": "service",  # service | equipment | combined
        "bill_date": "",
        "subtotal": 0.0,
        "discount": 0.0,
        # ---- GST fields (NEW) ----
        "taxable_amount": 0.0,
        "gst_applicable": False,
        "gst_type": "CGST_SGST",  # CGST_SGST | IGST
        "gst_rate": 0.0,
        "cgst": 0.0,
        "sgst": 0.0,
        "igst": 0.0,
        # ---- legacy tax fields ----
        "tax_rate": 0.0,
        "tax": 0.0,
        "deposit": 0.0,
        "damage_charges": 0.0,
        "loss_charges": 0.0,
        "other_charges": 0.0,
        "grand_total": 0.0,
        "received_amount": 0.0,
        "refunded_amount": 0.0,
        "remaining_amount": 0.0,
        "status": "Pending",  # Paid | Partial | Pending | Canceled
        "cancelled": False,
        "cancelled_at": "",
        "cancelled_by": "",
        "cancel_reason": "",
        "quotation_ref": "",
        "notes": "",
        "created_by": "",
        "created_at": "",
        "updated_at": "",
    },
    "bill_items": {
        "item_id": "",
        "bill_id": "",
        "item_type": "service",  # service | equipment
        "ref_id": "",
        "description": "",
        "serial_number": "",
        "start_date": "",
        "end_date": "",
        "billing_type": "",
        # ---- Qty / Days basis (NEW) ----
        "unit_type": "qty",  # qty | days
        "charge_mode": "qty",
        "quantity": 0.0,
        "days": 0.0,
        "units": 0.0,
        "rate": 0.0,
        "discount": 0.0,
        "amount": 0.0,
    },
    "payments": {
        "payment_id": "",
        "bill_id": "",
        "patient_id": "",
        "payment_date": "",
        "method": "Cash",  # Cash | UPI | Bank
        "amount": 0.0,
        "transaction_id": "",
        "notes": "",
        "received_by": "",
        "created_at": "",
    },
    "deposits": {
        "deposit_id": "",
        "patient_id": "",
        "bill_id": "",
        "deposit_date": "",
        "amount": 0.0,
        "method": "Cash",
        "transaction_id": "",
        "purpose": "Advance",  # Advance | Security Deposit
        "status": "Held",  # Held | Adjusted | Refunded | Partial
        "adjusted_amount": 0.0,
        "refunded_amount": 0.0,
        "notes": "",
        "received_by": "",
        "created_at": "",
        "updated_at": "",
    },
    "refunds": {
        "refund_id": "",
        "payment_id": "",
        "deposit_id": "",
        "bill_id": "",
        "patient_id": "",
        "refund_date": "",
        "amount": 0.0,
        "method": "Cash",
        "reason": "",
        "transaction_id": "",
        "processed_by": "",
        "created_at": "",
    },
    "staff": {
        "staff_id": "",
        "username": "",
        "full_name": "",
        "password_hash": "",
        "role": "staff",  # admin | staff
        "permissions": "[]",  # JSON list; empty + staff role => role defaults
        "status": "Active",
        "created_at": "",
        "updated_at": "",
    },
    "roles": {
        "role_id": "",
        "role_name": "",
        "permissions": "[]",  # JSON list
        "updated_at": "",
    },
    "activity_logs": {
        "log_id": "",
        "timestamp": "",
        "username": "",
        "role": "",
        "action": "",
        "entity_type": "",
        "entity_id": "",
        "description": "",
        "before_data": "",
        "after_data": "",
        "ip": "",
    },
    "company_settings": {
        "key": "",
        "value": "",
        "updated_at": "",
    },
    "quotations": {
        "quotation_id": "",
        "quotation_number": "",
        "quotation_date": "",
        "patient_id": "",
        "customer_name": "",
        "valid_until": "",
        "subtotal": 0.0,
        "discount": 0.0,
        "tax_rate": 0.0,
        "tax": 0.0,
        "grand_total": 0.0,
        "terms": "",
        "items_json": "[]",
        "status": "Draft",  # Draft | Sent | Converted | Cancelled
        "converted_bill_number": "",
        "notes": "",
        "created_by": "",
        "created_at": "",
        "updated_at": "",
    },
    "equipment_transactions": {
        "txn_id": "",
        "equipment_id": "",
        "serial_number": "",
        "bill_id": "",
        "patient_id": "",
        "txn_type": "Issue",  # Issue | Return | Sale
        "issue_date": "",
        "expected_return_date": "",
        "return_date": "",
        "condition": "",
        "rent_amount": 0.0,
        "deposit": 0.0,
        "damage_charge": 0.0,
        "loss_charge": 0.0,
        "refund_amount": 0.0,
        "notes": "",
        "handled_by": "",
        "created_at": "",
    },
    "service_records": {
        "record_id": "",
        "bill_id": "",
        "patient_id": "",
        "service_id": "",
        "service_name": "",
        "start_date": "",
        "end_date": "",
        "billing_type": "",
        # ---- Qty / Days basis (NEW) ----
        "unit_type": "qty",
        "quantity": 0.0,
        "days": 0.0,
        "rate": 0.0,
        "amount": 0.0,
        "status": "Active",
        "notes": "",
        "created_at": "",
    },
    "document_settings": {
        "key": "",
        "value": "",
        "updated_at": "",
    },
}

# Primary key column per table (used by get/update helpers).
PRIMARY_KEYS = {
    "patients": "patient_id",
    "services": "service_id",
    "equipment": "equipment_id",
    "bills": "bill_id",
    "bill_items": "item_id",
    "payments": "payment_id",
    "deposits": "deposit_id",
    "refunds": "refund_id",
    "staff": "staff_id",
    "roles": "role_id",
    "activity_logs": "log_id",
    "company_settings": "key",
    "quotations": "quotation_id",
    "equipment_transactions": "txn_id",
    "service_records": "record_id",
    "document_settings": "key",
}

_locks: dict[str, threading.RLock] = {name: threading.RLock() for name in SCHEMAS}
_locks_lock = threading.Lock()


def _table_lock(name: str) -> threading.RLock:
    with _locks_lock:
        return _locks.setdefault(name, threading.RLock())


def table_path(name: str) -> Path:
    return Path(DATA_DIR) / f"{name}.feather"


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def empty_frame(name: str) -> pd.DataFrame:
    schema = SCHEMAS[name]
    return pd.DataFrame({col: pd.Series(dtype=_dtype_for(default)) for col, default in schema.items()})


def _dtype_for(default: object) -> str:
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, float):
        return "float64"
    if isinstance(default, int):
        return "int64"
    return "object"


def _normalize_frame(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Ensure all schema columns exist with sane dtypes; drop unknown ones."""
    schema = SCHEMAS[name]
    df = df.copy()
    for col, default in schema.items():
        if col not in df.columns:
            df[col] = default
    df = df[[c for c in schema.keys()]]
    for col, default in schema.items():
        try:
            if isinstance(default, bool):
                df[col] = df[col].astype(bool)
            elif isinstance(default, float):
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)
            elif isinstance(default, int):
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
            else:
                df[col] = df[col].fillna("").astype(str)
                # FIX: only blank this column's cell, NOT the whole row.
                df.loc[df[col].isin({"nan", "None", "NaT"}), col] = ""
        except Exception:
            pass
    return df.reset_index(drop=True)


def read_table(name: str) -> pd.DataFrame:
    """Read a table; auto-create the Feather file with the correct schema."""
    if name not in SCHEMAS:
        raise ValueError(f"Unknown table: {name}")
    path = table_path(name)
    with _table_lock(name):
        if not path.exists():
            df = empty_frame(name)
            _write_frame_atomic(name, df)
            return df
        try:
            df = pd.read_feather(path)
        except Exception as exc:  # corrupted or half-written file
            logger.exception("Failed reading %s: %s", path, exc)
            backup = path.with_suffix(f".corrupt-{uuid.uuid4().hex[:8]}.feather")
            try:
                path.rename(backup)
            except OSError:
                pass
            df = empty_frame(name)
            _write_frame_atomic(name, df)
            return df
    return _normalize_frame(name, df)


def _write_frame_atomic(name: str, df: pd.DataFrame) -> None:
    path = table_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}.feather")
    df.reset_index(drop=True).to_feather(tmp)
    # validate the temp file before replacing the original
    pd.read_feather(tmp)
    os.replace(tmp, path)


def write_table(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Validate + atomically persist a whole table (transaction-like)."""
    if name not in SCHEMAS:
        raise ValueError(f"Unknown table: {name}")
    df = _normalize_frame(name, df)
    with _table_lock(name):
        _write_frame_atomic(name, df)
    return df


def append_record(name: str, record: dict) -> dict:
    """Append one record (dict) and return the stored row as a dict."""
    with _table_lock(name):
        df = read_table(name)
        schema = SCHEMAS[name]
        row = {col: record.get(col, default) for col, default in schema.items()}
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df = _normalize_frame(name, df)
        _write_frame_atomic(name, df)
        return df.iloc[-1].to_dict()


def get_record(name: str, record_id: str) -> dict | None:
    pk = PRIMARY_KEYS[name]
    df = read_table(name)
    if df.empty:
        return None
    hit = df[df[pk].astype(str) == str(record_id)]
    if hit.empty:
        return None
    return hit.iloc[0].to_dict()


def find_records(name: str, **filters) -> pd.DataFrame:
    df = read_table(name)
    for col, val in filters.items():
        if col in df.columns:
            df = df[df[col].astype(str) == str(val)]
    return df.reset_index(drop=True)


def update_record(name: str, record_id: str, data: dict) -> dict | None:
    """Update one record by primary key; returns the updated row or None."""
    pk = PRIMARY_KEYS[name]
    with _table_lock(name):
        df = read_table(name)
        mask = df[pk].astype(str) == str(record_id)
        if not mask.any():
            return None
        for col, val in data.items():
            if col in df.columns and col != pk:
                df.loc[mask, col] = val
        if "updated_at" in df.columns and "updated_at" not in data:
            df.loc[mask, "updated_at"] = now_iso()
        df = _normalize_frame(name, df)
        _write_frame_atomic(name, df)
        return df[mask].iloc[0].to_dict()


def delete_record(name: str, record_id: str) -> bool:
    """Generic delete. NOTE: bills must use cancel_bill(), never this."""
    pk = PRIMARY_KEYS[name]
    with _table_lock(name):
        df = read_table(name)
        mask = df[pk].astype(str) == str(record_id)
        if not mask.any():
            return False
        df = df[~mask].reset_index(drop=True)
        _write_frame_atomic(name, df)
        return True


def delete_where(name: str, **filters) -> int:
    with _table_lock(name):
        df = read_table(name)
        if df.empty:
            return 0
        mask = pd.Series([True] * len(df))
        for col, val in filters.items():
            if col in df.columns:
                mask &= df[col].astype(str) == str(val)
        removed = int(mask.sum())
        df = df[~mask].reset_index(drop=True)
        _write_frame_atomic(name, df)
        return removed


def ensure_all() -> None:
    for name in SCHEMAS:
        read_table(name)


# ---------------------------------------------------------------------------
# Unique number generation (never reused, never recycled)
# ---------------------------------------------------------------------------
def _next_sequence(name: str, column: str, prefix: str) -> int:
    df = read_table(name)
    best = 0
    if not df.empty and column in df.columns:
        for val in df[column].astype(str):
            if val.startswith(prefix):
                tail = val[len(prefix):]
                digits = "".join(ch for ch in tail if ch.isdigit())
                try:
                    best = max(best, int(digits[-6:]) if digits else 0)
                except ValueError:
                    continue
    # also account for row count so sequences keep growing monotonically
    return max(best + 1, len(df) + 1)


def generate_patient_id() -> str:
    return f"WC-P-{_next_sequence('patients', 'patient_id', 'WC-P-'):06d}"


def generate_bill_number(bill_date: str | None = None) -> str:
    year = (bill_date or today_str())[:4]
    prefix = f"WC-INV-{year}-"
    return f"{prefix}{_next_sequence('bills', 'bill_number', prefix):06d}"


def generate_payment_id() -> str:
    return f"WC-PAY-{_next_sequence('payments', 'payment_id', 'WC-PAY-'):06d}"


def generate_deposit_id() -> str:
    return f"WC-DEP-{_next_sequence('deposits', 'deposit_id', 'WC-DEP-'):06d}"


def generate_refund_id() -> str:
    return f"WC-RFD-{_next_sequence('refunds', 'refund_id', 'WC-RFD-'):06d}"


def generate_equipment_id() -> str:
    return f"WC-EQ-{_next_sequence('equipment', 'equipment_id', 'WC-EQ-'):06d}"


def generate_service_id() -> str:
    return f"WC-S-{_next_sequence('services', 'service_id', 'WC-S-'):06d}"


def generate_quotation_number() -> str:
    year = today_str()[:4]
    prefix = f"WC-QTN-{year}-"
    return f"{prefix}{_next_sequence('quotations', 'quotation_number', prefix):06d}"


def generate_staff_id() -> str:
    return f"WC-ST-{_next_sequence('staff', 'staff_id', 'WC-ST-'):06d}"


def new_uuid(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Key-value helpers for company_settings / document_settings
# ---------------------------------------------------------------------------
def get_setting(table: str, key: str, default: str = "") -> str:
    rec = get_record(table, key)
    if not rec:
        return default
    return str(rec.get("value", default))


def set_setting(table: str, key: str, value: str) -> None:
    existing = get_record(table, key)
    if existing is None:
        append_record(table, {"key": key, "value": value, "updated_at": now_iso()})
    else:
        update_record(table, key, {"value": value, "updated_at": now_iso()})


def get_all_settings(table: str) -> dict:
    df = read_table(table)
    out: dict[str, str] = {}
    for _, row in df.iterrows():
        out[str(row["key"])] = str(row["value"])
    return out


@contextmanager
def backup_table(name: str):
    """Yield the table; on exception inside, nothing is written (callers only
    persist via write/append/update helpers, so this is a documentation aid for
    transaction-like usage)."""
    yield read_table(name)