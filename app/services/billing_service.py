"""Billing domain logic: create / edit / cancel / duplicate bills.

All totals are computed server-side via app.calculations (final authority).
Bills are NEVER deleted; cancellation flags them instead.

GST support:
- GST Applicable: Yes / No
- GST Type: CGST + SGST / IGST
- GST Rate
- Taxable Amount
- CGST
- SGST
- IGST

Security deposit is refundable and is tracked separately.
It is NOT included in grand_total.

Unit Basis:
- Qty and Days are both optional; calculation is driven by whichever
  basis was selected (unit_type / charge_mode).
"""

from .. import database as db
from ..audit import log_activity
from ..calculations import (
    bill_totals,
    line_total,
    money,
    payment_status,
    resolve_units,
    validate_item_units,
)


# ---------------------------------------------------------------------------
# ITEM VALIDATION
# ---------------------------------------------------------------------------

def _validate_items(items: list[dict]) -> list[str]:
    """Validate service/equipment line items."""

    errors: list[str] = []

    for i, item in enumerate(items, start=1):

        # Qty aur Days dono optional hain — kam se kam ek value honi chahiye
        unit_error = validate_item_units(item)
        if unit_error:
            errors.append(f"Item {i}: {unit_error}")

        resolved = resolve_units(item)
        units = float(resolved["units"])

        try:
            rate = float(item.get("rate", 0) or 0)
        except Exception:
            rate = -1.0

        try:
            disc = float(item.get("discount", 0) or 0)
        except Exception:
            disc = 0.0

        if rate < 0:
            errors.append(
                f"Item {i}: rate cannot be negative."
            )

        if disc < 0:
            errors.append(
                f"Item {i}: discount cannot be negative."
            )

        if disc > units * max(rate, 0):
            errors.append(
                f"Item {i}: discount cannot exceed the line amount."
            )

        start = item.get("start_date") or ""
        end = item.get("end_date") or ""

        if start and end and end < start:
            errors.append(
                f"Item {i}: end date cannot be before start date."
            )

    return errors


# ---------------------------------------------------------------------------
# BOOLEAN / GST HELPERS
# ---------------------------------------------------------------------------

def _bool_value(value: object) -> bool:
    """Safely convert form-style values to boolean."""

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    value = str(value or "").strip().lower()

    return value in {
        "yes",
        "true",
        "1",
        "y",
        "on",
    }


def _gst_type(value: object) -> str:
    """Normalize GST type."""

    value = str(value or "CGST_SGST").strip().upper()

    if value in {
        "CGST+SGST",
        "CGST + SGST",
        "CGST/SGST",
        "CGST_SGST",
    }:
        return "CGST_SGST"

    if value == "IGST":
        return "IGST"

    return "CGST_SGST"


def _non_negative(value: object) -> float:
    """Convert numeric value to non-negative float."""

    try:
        value = float(value or 0)
    except Exception:
        value = 0.0

    return max(0.0, value)


# ---------------------------------------------------------------------------
# PREPARE BILL PAYLOAD
# ---------------------------------------------------------------------------

def prepare_bill_payload(
    *,
    patient_id: str,
    bill_type: str,
    bill_date: str,
    items: list[dict],
    bill_discount: float = 0,

    # New GST fields
    gst_applicable: object = False,
    gst_type: object = "CGST_SGST",
    gst_rate: float = 0,

    # Legacy field kept for compatibility
    tax_rate: float = 0,

    deposit: float = 0,
    damage_charges: float = 0,
    loss_charges: float = 0,
    other_charges: float = 0,
    notes: str = "",
    quotation_ref: str = "",
) -> tuple[dict | None, list[str]]:
    """Validate + compute authoritative bill payload."""

    errors: list[str] = []

    # -----------------------------------------------------------------------
    # BASIC VALIDATION
    # -----------------------------------------------------------------------

    if not patient_id:
        errors.append("Patient is required.")

    elif not db.get_record("patients", patient_id):
        errors.append(
            "Selected patient does not exist."
        )

    if bill_type not in (
        "service",
        "equipment",
        "combined",
    ):
        errors.append("Invalid bill type.")

    if not bill_date:
        errors.append("Bill date is required.")

    if not items:
        errors.append(
            "Add at least one service or equipment item."
        )

    errors.extend(
        _validate_items(items)
    )

    # -----------------------------------------------------------------------
    # NORMALIZE GST VALUES
    # -----------------------------------------------------------------------

    gst_enabled = _bool_value(
        gst_applicable
    )

    normalized_gst_type = _gst_type(
        gst_type
    )

    try:
        normalized_gst_rate = float(
            gst_rate or 0
        )
    except Exception:
        normalized_gst_rate = 0.0

    normalized_gst_rate = max(
        0.0,
        normalized_gst_rate,
    )

    if normalized_gst_rate > 100:
        errors.append(
            "GST rate cannot be greater than 100%."
        )

    # If GST is not applicable, GST rate must be zero.
    if not gst_enabled:
        normalized_gst_rate = 0.0

    # -----------------------------------------------------------------------
    # BACKWARD COMPATIBILITY
    # -----------------------------------------------------------------------

    if gst_enabled and normalized_gst_rate == 0:

        try:
            legacy_rate = float(
                tax_rate or 0
            )
        except Exception:
            legacy_rate = 0.0

        if legacy_rate > 0:
            normalized_gst_rate = legacy_rate

    # -----------------------------------------------------------------------
    # SERVER-SIDE TOTAL CALCULATION
    # -----------------------------------------------------------------------

    totals = bill_totals(
        items,
        bill_discount=_non_negative(
            bill_discount
        ),

        gst_applicable=gst_enabled,
        gst_type=normalized_gst_type,
        gst_rate=normalized_gst_rate,

        # Legacy compatibility
        tax_rate=_non_negative(
            tax_rate
        ),

        deposit=_non_negative(
            deposit
        ),
        damage_charges=_non_negative(
            damage_charges
        ),
        loss_charges=_non_negative(
            loss_charges
        ),
        other_charges=_non_negative(
            other_charges
        ),
    )

    # -----------------------------------------------------------------------
    # RETURN VALIDATION ERRORS BEFORE SAVING
    # -----------------------------------------------------------------------

    if errors:
        return None, errors

    # -----------------------------------------------------------------------
    # COMPUTED ITEMS
    # -----------------------------------------------------------------------

    computed_items: list[dict] = []

    for item in items:

        resolved = resolve_units(item)

        unit_type = resolved["unit_type"]
        qty = float(resolved["quantity"])
        days = float(resolved["days"])
        units = float(resolved["units"])

        try:
            rate = float(
                item.get("rate", 0) or 0
            )
        except Exception:
            rate = 0.0

        try:
            disc = float(
                item.get("discount", 0) or 0
            )
        except Exception:
            disc = 0.0

        computed_items.append(
            {
                "item_type": item.get(
                    "item_type",
                    "service",
                ),

                "ref_id": item.get(
                    "ref_id",
                    "",
                ),

                "description": item.get(
                    "description",
                    "",
                ),

                "serial_number": item.get(
                    "serial_number",
                    "",
                ),

                "start_date": item.get(
                    "start_date",
                    "",
                ),

                "end_date": item.get(
                    "end_date",
                    "",
                ),

                "billing_type": item.get(
                    "billing_type",
                    "",
                ),

                # Qty / Days basis fields
                "unit_type": unit_type,
                "charge_mode": unit_type,
                "quantity": qty,
                "days": days,
                "units": units,

                "rate": rate,

                "discount": disc,

                # Authoritative line amount
                "amount": line_total(
                    qty,
                    rate,
                    disc,
                    days=days,
                    unit_type=unit_type,
                ),
            }
        )

    # -----------------------------------------------------------------------
    # FINAL PAYLOAD
    # -----------------------------------------------------------------------

    payload = {
        "patient_id": patient_id,

        "bill_type": bill_type,

        "bill_date": bill_date,

        # All authoritative totals
        **totals,

        "notes": notes or "",

        "quotation_ref": quotation_ref or "",

        "items": computed_items,
    }

    return payload, []


# ---------------------------------------------------------------------------
# CREATE BILL
# ---------------------------------------------------------------------------

def create_bill(
    payload: dict,
    *,
    user: dict | None,
    ip: str = "",
) -> dict:
    """Create a new bill."""

    bill_number = db.generate_bill_number(
        payload["bill_date"]
    )

    while db.find_records(
        "bills",
        bill_number=bill_number,
    ).shape[0]:
        bill_number = db.generate_bill_number(
            payload["bill_date"]
        )

    bill_id = db.new_uuid("BILL-")

    status, net_received, remaining = payment_status(
        payload["grand_total"],
        0,
        0,
        False,
    )

    record = {
        "bill_id": bill_id,

        "bill_number": bill_number,

        "patient_id": payload["patient_id"],

        "bill_type": payload["bill_type"],

        "bill_date": payload["bill_date"],

        "subtotal": payload["subtotal"],

        "discount": payload["discount"],

        "taxable_amount": payload.get(
            "taxable_amount",
            0,
        ),

        "gst_applicable": payload.get(
            "gst_applicable",
            False,
        ),

        "gst_type": payload.get(
            "gst_type",
            "CGST_SGST",
        ),

        "gst_rate": payload.get(
            "gst_rate",
            0,
        ),

        "cgst": payload.get(
            "cgst",
            0,
        ),

        "sgst": payload.get(
            "sgst",
            0,
        ),

        "igst": payload.get(
            "igst",
            0,
        ),

        "tax_rate": payload.get(
            "tax_rate",
            payload.get("gst_rate", 0),
        ),

        "tax": payload.get(
            "tax",
            0,
        ),

        "deposit": payload.get(
            "deposit",
            0,
        ),

        "damage_charges": payload.get(
            "damage_charges",
            0,
        ),

        "loss_charges": payload.get(
            "loss_charges",
            0,
        ),

        "other_charges": payload.get(
            "other_charges",
            0,
        ),

        "grand_total": payload[
            "grand_total"
        ],

        "received_amount": 0.0,

        "refunded_amount": 0.0,

        "remaining_amount": remaining,

        "status": status,

        "cancelled": False,

        "cancelled_at": "",

        "cancelled_by": "",

        "cancel_reason": "",

        "quotation_ref": payload.get(
            "quotation_ref",
            "",
        ),

        "notes": payload.get(
            "notes",
            "",
        ),

        "created_by": str(
            (user or {}).get(
                "username",
                "",
            )
        ),

        "created_at": db.now_iso(),

        "updated_at": db.now_iso(),
    }

    # Save bill.
    db.append_record(
        "bills",
        record,
    )

    # Save bill items.
    for item in payload["items"]:

        db.append_record(
            "bill_items",
            {
                "item_id": db.new_uuid(
                    "ITEM-"
                ),

                "bill_id": bill_id,

                **item,
            },
        )

    # Sync service records.
    _sync_service_records(
        bill_id,
        payload,
    )

    # Sync equipment issue records.
    _sync_equipment_issue(
        bill_id,
        payload,
        user=user,
    )

    # Audit log.
    log_activity(
        user=user,

        action="CREATE_BILL",

        entity_type="bill",

        entity_id=bill_id,

        description=(
            f"Created bill {bill_number} "
            f"for patient {payload['patient_id']}"
        ),

        after={
            **record,
            "items": payload["items"],
        },

        ip=ip,
    )

    return (
        db.get_record(
            "bills",
            bill_id,
        )
        or record
    )


# ---------------------------------------------------------------------------
# EDIT BILL
# ---------------------------------------------------------------------------

def edit_bill(
    bill_id: str,
    payload: dict,
    *,
    user: dict | None,
    ip: str = "",
) -> tuple[dict | None, list[str]]:
    """Edit an existing bill."""

    existing = db.get_record(
        "bills",
        bill_id,
    )

    if not existing:
        return None, ["Bill not found."]

    if existing.get("cancelled"):
        return None, [
            "Canceled bills cannot be edited."
        ]

    before = dict(existing)

    before_items = db.find_records(
        "bill_items",
        bill_id=bill_id,
    ).to_dict("records")

    received = float(
        existing.get(
            "received_amount",
            0,
        )
        or 0
    )

    refunded = float(
        existing.get(
            "refunded_amount",
            0,
        )
        or 0
    )

    status, net_received, remaining = payment_status(
        payload["grand_total"],
        received,
        refunded,
        False,
    )

    updates = {
        "patient_id": payload[
            "patient_id"
        ],

        "bill_type": payload[
            "bill_type"
        ],

        "bill_date": payload[
            "bill_date"
        ],

        "subtotal": payload[
            "subtotal"
        ],

        "discount": payload[
            "discount"
        ],

        "taxable_amount": payload.get(
            "taxable_amount",
            0,
        ),

        "gst_applicable": payload.get(
            "gst_applicable",
            False,
        ),

        "gst_type": payload.get(
            "gst_type",
            "CGST_SGST",
        ),

        "gst_rate": payload.get(
            "gst_rate",
            0,
        ),

        "cgst": payload.get(
            "cgst",
            0,
        ),

        "sgst": payload.get(
            "sgst",
            0,
        ),

        "igst": payload.get(
            "igst",
            0,
        ),

        "tax_rate": payload.get(
            "tax_rate",
            payload.get("gst_rate", 0),
        ),

        "tax": payload.get(
            "tax",
            0,
        ),

        "deposit": payload.get(
            "deposit",
            0,
        ),

        "damage_charges": payload.get(
            "damage_charges",
            0,
        ),

        "loss_charges": payload.get(
            "loss_charges",
            0,
        ),

        "other_charges": payload.get(
            "other_charges",
            0,
        ),

        "grand_total": payload[
            "grand_total"
        ],

        "remaining_amount": remaining,

        "status": status,

        "notes": payload.get(
            "notes",
            "",
        ),

        "updated_at": db.now_iso(),
    }

    db.update_record(
        "bills",
        bill_id,
        updates,
    )

    # Replace existing items.
    db.delete_where(
        "bill_items",
        bill_id=bill_id,
    )

    for item in payload["items"]:

        db.append_record(
            "bill_items",
            {
                "item_id": db.new_uuid(
                    "ITEM-"
                ),

                "bill_id": bill_id,

                **item,
            },
        )

    # Sync service records.
    _sync_service_records(
        bill_id,
        payload,
    )

    # Sync equipment.
    _sync_equipment_issue(
        bill_id,
        payload,
        user=user,
    )

    after = (
        db.get_record(
            "bills",
            bill_id,
        )
        or {}
    )

    # Audit.
    log_activity(
        user=user,

        action="EDIT_BILL",

        entity_type="bill",

        entity_id=bill_id,

        description=(
            f"Edited bill "
            f"{existing.get('bill_number')}"
        ),

        before={
            **before,
            "items": before_items,
        },

        after={
            **after,
            "items": payload["items"],
        },

        ip=ip,
    )

    return after, []


# ---------------------------------------------------------------------------
# CANCEL BILL
# ---------------------------------------------------------------------------

def cancel_bill(
    bill_id: str,
    reason: str,
    *,
    user: dict | None,
    ip: str = "",
) -> tuple[dict | None, list[str]]:
    """Cancel a bill without deleting it."""

    existing = db.get_record(
        "bills",
        bill_id,
    )

    if not existing:
        return None, [
            "Bill not found."
        ]

    if existing.get("cancelled"):
        return None, [
            "Bill is already canceled."
        ]

    before = dict(existing)

    updates = {
        "cancelled": True,

        "status": "Canceled",

        "cancelled_at": db.now_iso(),

        "cancelled_by": str(
            (user or {}).get(
                "username",
                "",
            )
        ),

        "cancel_reason": reason or "",

        "updated_at": db.now_iso(),
    }

    db.update_record(
        "bills",
        bill_id,
        updates,
    )

    after = (
        db.get_record(
            "bills",
            bill_id,
        )
        or {}
    )

    log_activity(
        user=user,

        action="CANCEL_BILL",

        entity_type="bill",

        entity_id=bill_id,

        description=(
            f"Canceled bill "
            f"{existing.get('bill_number')}. "
            f"Reason: {reason or '-'}"
        ),

        before=before,

        after=after,

        ip=ip,
    )

    return after, []


# ---------------------------------------------------------------------------
# DUPLICATE BILL
# ---------------------------------------------------------------------------

def duplicate_bill_payload(
    bill_id: str,
) -> dict | None:
    """Prepare data for creating a new bill from an old bill.

    Payment history is intentionally NOT copied.
    """

    existing = db.get_record(
        "bills",
        bill_id,
    )

    if not existing:
        return None

    items = db.find_records(
        "bill_items",
        bill_id=bill_id,
    ).to_dict("records")

    return {
        "patient_id": existing.get(
            "patient_id",
            "",
        ),

        "bill_type": existing.get(
            "bill_type",
            "service",
        ),

        "bill_date": db.today_str(),

        "items": [
            {
                "item_type": it.get(
                    "item_type",
                    "service",
                ),

                "ref_id": it.get(
                    "ref_id",
                    "",
                ),

                "description": it.get(
                    "description",
                    "",
                ),

                "serial_number": it.get(
                    "serial_number",
                    "",
                ),

                "start_date": it.get(
                    "start_date",
                    "",
                ),

                "end_date": it.get(
                    "end_date",
                    "",
                ),

                "billing_type": it.get(
                    "billing_type",
                    "",
                ),

                # Preserves Qty vs Days on duplicate
                "unit_type": it.get(
                    "unit_type",
                    "qty",
                ),

                "charge_mode": it.get(
                    "charge_mode",
                    it.get("unit_type", "qty"),
                ),

                "quantity": it.get(
                    "quantity",
                    0,
                ),

                "days": it.get(
                    "days",
                    0,
                ),

                "rate": it.get(
                    "rate",
                    0,
                ),

                "discount": it.get(
                    "discount",
                    0,
                ),
            }

            for it in items
        ],

        "bill_discount": existing.get(
            "discount",
            0,
        ),

        "gst_applicable": existing.get(
            "gst_applicable",
            False,
        ),

        "gst_type": existing.get(
            "gst_type",
            "CGST_SGST",
        ),

        "gst_rate": existing.get(
            "gst_rate",
            existing.get("tax_rate", 0),
        ),

        "tax_rate": existing.get(
            "tax_rate",
            existing.get("gst_rate", 0),
        ),

        "deposit": existing.get(
            "deposit",
            0,
        ),

        "damage_charges": 0,

        "loss_charges": 0,

        "other_charges": existing.get(
            "other_charges",
            0,
        ),

        "notes": existing.get(
            "notes",
            "",
        ),
    }


# ---------------------------------------------------------------------------
# REFRESH PAYMENT STATUS
# ---------------------------------------------------------------------------

def refresh_bill_payment_status(
    bill_id: str,
) -> dict | None:
    """Recalculate received/refunded/remaining amounts."""

    bill = db.get_record(
        "bills",
        bill_id,
    )

    if not bill:
        return None

    payments = db.find_records(
        "payments",
        bill_id=bill_id,
    )

    refunds = db.find_records(
        "refunds",
        bill_id=bill_id,
    )

    received = (
        float(payments["amount"].sum())
        if not payments.empty
        else 0.0
    )

    refunded = (
        float(refunds["amount"].sum())
        if not refunds.empty
        else 0.0
    )

    status, net_received, remaining = payment_status(
        bill.get("grand_total", 0),
        received,
        refunded,
        bool(
            bill.get(
                "cancelled"
            )
        ),
    )

    db.update_record(
        "bills",
        bill_id,
        {
            "received_amount": money(
                received
            ),

            "refunded_amount": money(
                refunded
            ),

            "remaining_amount": remaining,

            "status": status,

            "updated_at": db.now_iso(),
        },
    )

    return db.get_record(
        "bills",
        bill_id,
    )


# ---------------------------------------------------------------------------
# GET FULL BILL
# ---------------------------------------------------------------------------

def get_bill_full(
    bill_id: str,
) -> dict | None:
    """Return bill + items + payments + refunds + patient."""

    bill = db.get_record(
        "bills",
        bill_id,
    )

    if not bill:
        return None

    items = db.find_records(
        "bill_items",
        bill_id=bill_id,
    ).to_dict("records")

    payments = db.find_records(
        "payments",
        bill_id=bill_id,
    ).to_dict("records")

    refunds = db.find_records(
        "refunds",
        bill_id=bill_id,
    ).to_dict("records")

    patient = db.get_record(
        "patients",
        str(
            bill.get(
                "patient_id",
                "",
            )
        ),
    ) or {}

    return {
        "bill": bill,
        "items": items,
        "payments": payments,
        "refunds": refunds,
        "patient": patient,
    }


# ---------------------------------------------------------------------------
# SERVICE RECORD SYNC
# ---------------------------------------------------------------------------

def _sync_service_records(
    bill_id: str,
    payload: dict,
) -> None:
    """Rebuild service records linked to this bill."""

    db.delete_where(
        "service_records",
        bill_id=bill_id,
    )

    for item in payload.get(
        "items",
        [],
    ):

        if item.get(
            "item_type"
        ) != "service":
            continue

        db.append_record(
            "service_records",
            {
                "record_id": db.new_uuid(
                    "SRV-"
                ),

                "bill_id": bill_id,

                "patient_id": payload.get(
                    "patient_id",
                    "",
                ),

                "service_id": item.get(
                    "ref_id",
                    "",
                ),

                "service_name": item.get(
                    "description",
                    "",
                ),

                "start_date": item.get(
                    "start_date",
                    "",
                ),

                "end_date": item.get(
                    "end_date",
                    "",
                ),

                "billing_type": item.get(
                    "billing_type",
                    "",
                ),

                "quantity": item.get(
                    "quantity",
                    0,
                ),

                "days": item.get(
                    "days",
                    0,
                ),

                "unit_type": item.get(
                    "unit_type",
                    "qty",
                ),

                "rate": item.get(
                    "rate",
                    0,
                ),

                "amount": item.get(
                    "amount",
                    0,
                ),

                "status": "Active",

                "notes": "",

                "created_at": db.now_iso(),
            },
        )


# ---------------------------------------------------------------------------
# EQUIPMENT SYNC
# ---------------------------------------------------------------------------

def _sync_equipment_issue(
    bill_id: str,
    payload: dict,
    *,
    user: dict | None,
) -> None:
    """Mark billed equipment as On Rent / Sold.

    Creates equipment issue/sale transactions.
    """

    existing_txns = db.find_records(
        "equipment_transactions",
        bill_id=bill_id,
    )

    for _, txn in existing_txns.iterrows():

        if str(
            txn.get("txn_type")
        ) == "Issue":

            db.delete_record(
                "equipment_transactions",
                str(
                    txn.get(
                        "txn_id"
                    )
                ),
            )

    for item in payload.get(
        "items",
        [],
    ):

        if item.get(
            "item_type"
        ) != "equipment":
            continue

        eq = db.get_record(
            "equipment",
            str(
                item.get(
                    "ref_id",
                    "",
                )
            ),
        )

        billing_type = str(
            item.get(
                "billing_type",
                "",
            )
            or ""
        )

        if not eq:
            continue

        is_sale = (
            billing_type.lower()
            == "sale"
        )

        new_status = (
            "Sold"
            if is_sale
            else "On Rent"
        )

        if (
            str(
                eq.get("status")
            )
            == "Available"
            or str(
                eq.get("status")
            )
            == new_status
        ):

            db.update_record(
                "equipment",
                str(
                    eq.get(
                        "equipment_id"
                    )
                ),
                {
                    "status": new_status,
                    "updated_at": db.now_iso(),
                },
            )

        db.append_record(
            "equipment_transactions",
            {
                "txn_id": db.new_uuid(
                    "ETX-"
                ),

                "equipment_id": str(
                    eq.get(
                        "equipment_id"
                    )
                ),

                "serial_number": (
                    item.get(
                        "serial_number",
                        "",
                    )
                    or str(
                        eq.get(
                            "serial_number",
                            "",
                        )
                    )
                ),

                "bill_id": bill_id,

                "patient_id": payload.get(
                    "patient_id",
                    "",
                ),

                "txn_type": (
                    "Sale"
                    if is_sale
                    else "Issue"
                ),

                "issue_date": payload.get(
                    "bill_date",
                    "",
                ),

                "expected_return_date": item.get(
                    "end_date",
                    "",
                ),

                "return_date": "",

                "condition": "",

                "rent_amount": item.get(
                    "amount",
                    0,
                ),

                "deposit": 0,

                "damage_charge": 0,

                "loss_charge": 0,

                "refund_amount": 0,

                "notes": "",

                "handled_by": str(
                    (user or {}).get(
                        "username",
                        "",
                    )
                ),

                "created_at": db.now_iso(),
            },
        )