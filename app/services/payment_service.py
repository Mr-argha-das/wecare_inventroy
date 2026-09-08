"""Payments, deposits/advances and refunds."""
from .. import database as db
from ..audit import log_activity
from ..calculations import money
from .billing_service import refresh_bill_payment_status

PAYMENT_METHODS = ["Cash", "UPI", "Bank"]


def add_payment(
    *,
    bill_id: str,
    patient_id: str,
    payment_date: str,
    method: str,
    amount: float,
    transaction_id: str = "",
    notes: str = "",
    user: dict | None,
    ip: str = "",
    allow_overpay_as_advance: bool = True,
) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    bill = db.get_record("bills", bill_id)
    if not bill:
        return None, ["Bill not found."]
    if bill.get("cancelled"):
        return None, ["Cannot add payment to a canceled bill."]
    if method not in PAYMENT_METHODS:
        errors.append("Invalid payment method.")
    try:
        amount = float(amount or 0)
    except Exception:
        amount = 0
    if amount <= 0:
        errors.append("Payment amount must be greater than 0.")
    if not payment_date:
        errors.append("Payment date is required.")
    if method in ("UPI", "Bank") and not (transaction_id or "").strip():
        errors.append("Transaction ID is required for UPI/Bank payments.")
    remaining = float(bill.get("remaining_amount", 0) or 0)
    if not errors and amount > remaining and not allow_overpay_as_advance:
        errors.append(f"Payment cannot exceed pending balance of {remaining:.2f}.")
    if errors:
        return None, errors

    payment_id = db.generate_payment_id()
    record = {
        "payment_id": payment_id,
        "bill_id": bill_id,
        "patient_id": patient_id or str(bill.get("patient_id", "")),
        "payment_date": payment_date,
        "method": method,
        "amount": money(amount),
        "transaction_id": transaction_id or "",
        "notes": notes or "",
        "received_by": str((user or {}).get("username", "")),
        "created_at": db.now_iso(),
    }
    db.append_record("payments", record)
    refresh_bill_payment_status(bill_id)
    after = db.get_record("bills", bill_id)
    log_activity(
        user=user, action="CREATE_PAYMENT", entity_type="payment", entity_id=payment_id,
        description=f"Received {amount:.2f} via {method} for bill {bill.get('bill_number')}",
        after={**record, "bill_status": (after or {}).get("status", "")}, ip=ip,
    )

    # Excess over the bill total becomes an Advance deposit (never lost).
    excess = money(amount - remaining) if amount > remaining else 0
    if excess > 0:
        add_deposit(
            patient_id=record["patient_id"], bill_id=bill_id, deposit_date=payment_date,
            amount=excess, method=method, transaction_id=transaction_id,
            purpose="Advance", notes=f"Excess from payment {payment_id} adjusted as advance.",
            user=user, ip=ip,
        )
    return db.get_record("payments", payment_id) or record, []


def add_deposit(
    *,
    patient_id: str,
    bill_id: str = "",
    deposit_date: str | None = None,
    amount: float = 0,
    method: str = "Cash",
    transaction_id: str = "",
    purpose: str = "Advance",
    notes: str = "",
    user: dict | None,
    ip: str = "",
) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    if not patient_id or not db.get_record("patients", patient_id):
        errors.append("Valid patient is required.")
    try:
        amount = float(amount or 0)
    except Exception:
        amount = 0
    if amount <= 0:
        errors.append("Amount must be greater than 0.")
    if method not in PAYMENT_METHODS:
        errors.append("Invalid payment method.")
    if purpose not in ("Advance", "Security Deposit"):
        errors.append("Invalid purpose.")
    if errors:
        return None, errors
    deposit_id = db.generate_deposit_id()
    record = {
        "deposit_id": deposit_id,
        "patient_id": patient_id,
        "bill_id": bill_id or "",
        "deposit_date": deposit_date or db.today_str(),
        "amount": money(amount),
        "method": method,
        "transaction_id": transaction_id or "",
        "purpose": purpose,
        "status": "Held",
        "adjusted_amount": 0.0,
        "refunded_amount": 0.0,
        "notes": notes or "",
        "received_by": str((user or {}).get("username", "")),
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
    }
    db.append_record("deposits", record)
    log_activity(
        user=user, action="CREATE_DEPOSIT", entity_type="deposit", entity_id=deposit_id,
        description=f"Recorded {purpose} of {amount:.2f} for patient {patient_id}",
        after=record, ip=ip,
    )
    return db.get_record("deposits", deposit_id) or record, []


def adjust_deposit_to_bill(deposit_id: str, bill_id: str, amount: float, *, user: dict | None, ip: str = "") -> tuple[dict | None, list[str]]:
    deposit = db.get_record("deposits", deposit_id)
    bill = db.get_record("bills", bill_id)
    if not deposit:
        return None, ["Deposit not found."]
    if not bill or bill.get("cancelled"):
        return None, ["Bill not found or canceled."]
    available = float(deposit.get("amount", 0)) - float(deposit.get("adjusted_amount", 0)) - float(deposit.get("refunded_amount", 0))
    try:
        amount = float(amount or 0)
    except Exception:
        amount = 0
    if amount <= 0:
        return None, ["Adjustment amount must be greater than 0."]
    if amount > available + 0.001:
        return None, [f"Only {available:.2f} is available in this deposit."]
    before = dict(deposit)
    new_adjusted = money(float(deposit.get("adjusted_amount", 0)) + amount)
    total_used = new_adjusted + float(deposit.get("refunded_amount", 0))
    status = "Adjusted" if total_used >= float(deposit.get("amount", 0)) - 0.001 else "Partial"
    db.update_record("deposits", deposit_id, {"adjusted_amount": new_adjusted, "status": status, "updated_at": db.now_iso()})
    # Record the adjustment as a payment against the bill.
    payment_id = db.generate_payment_id()
    db.append_record("payments", {
        "payment_id": payment_id,
        "bill_id": bill_id,
        "patient_id": str(bill.get("patient_id", "")),
        "payment_date": db.today_str(),
        "method": str(deposit.get("method", "Cash")),
        "amount": money(amount),
        "transaction_id": f"ADJ-{deposit_id}",
        "notes": f"Adjusted from {deposit.get('purpose')} {deposit_id}",
        "received_by": str((user or {}).get("username", "")),
        "created_at": db.now_iso(),
    })
    refresh_bill_payment_status(bill_id)
    after = db.get_record("deposits", deposit_id)
    log_activity(
        user=user, action="ADJUST_DEPOSIT", entity_type="deposit", entity_id=deposit_id,
        description=f"Adjusted {amount:.2f} from {deposit_id} to bill {bill.get('bill_number')}",
        before=before, after=after, ip=ip,
    )
    return after, []


def create_refund(
    *,
    bill_id: str = "",
    payment_id: str = "",
    deposit_id: str = "",
    patient_id: str = "",
    refund_date: str | None = None,
    amount: float = 0,
    method: str = "Cash",
    reason: str = "",
    transaction_id: str = "",
    user: dict | None,
    ip: str = "",
) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    try:
        amount = float(amount or 0)
    except Exception:
        amount = 0
    if amount <= 0:
        errors.append("Refund amount must be greater than 0.")
    if method not in PAYMENT_METHODS:
        errors.append("Invalid refund method.")
    if not reason.strip():
        errors.append("Refund reason is required.")
    payment = db.get_record("payments", payment_id) if payment_id else None
    deposit = db.get_record("deposits", deposit_id) if deposit_id else None
    if payment_id and not payment:
        errors.append("Original payment not found.")
    if deposit_id and not deposit:
        errors.append("Deposit not found.")
    if not patient_id:
        patient_id = str((payment or deposit or {}).get("patient_id", ""))
    if not bill_id:
        bill_id = str((payment or deposit or {}).get("bill_id", ""))
    # Refundable = net received on bill (for payment refunds) or held deposit.
    if payment and not deposit:
        bill = db.get_record("bills", str(payment.get("bill_id", "")))
        if bill:
            refundable = float(bill.get("received_amount", 0)) - float(bill.get("refunded_amount", 0))
            if amount > refundable + 0.001:
                errors.append(f"Refund cannot exceed refundable amount {refundable:.2f}.")
    if deposit:
        available = float(deposit.get("amount", 0)) - float(deposit.get("adjusted_amount", 0)) - float(deposit.get("refunded_amount", 0))
        if amount > available + 0.001:
            errors.append(f"Refund cannot exceed available deposit {available:.2f}.")
    if errors:
        return None, errors

    refund_id = db.generate_refund_id()
    record = {
        "refund_id": refund_id,
        "payment_id": payment_id or "",
        "deposit_id": deposit_id or "",
        "bill_id": bill_id or "",
        "patient_id": patient_id,
        "refund_date": refund_date or db.today_str(),
        "amount": money(amount),
        "method": method,
        "reason": reason,
        "transaction_id": transaction_id or "",
        "processed_by": str((user or {}).get("username", "")),
        "created_at": db.now_iso(),
    }
    db.append_record("refunds", record)
    if deposit:
        new_refunded = money(float(deposit.get("refunded_amount", 0)) + amount)
        total_used = new_refunded + float(deposit.get("adjusted_amount", 0))
        status = "Refunded" if total_used >= float(deposit.get("amount", 0)) - 0.001 else "Partial"
        db.update_record("deposits", deposit_id, {"refunded_amount": new_refunded, "status": status, "updated_at": db.now_iso()})
    if bill_id:
        refresh_bill_payment_status(bill_id)
    log_activity(
        user=user, action="REFUND", entity_type="refund", entity_id=refund_id,
        description=f"Refunded {amount:.2f} to patient {patient_id}. Reason: {reason}",
        after=record, ip=ip,
    )
    return db.get_record("refunds", refund_id) or record, []
