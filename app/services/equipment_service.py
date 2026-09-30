"""Equipment issue / return workflow with automatic status sync."""
from .. import database as db
from ..audit import log_activity
from ..calculations import line_total, money

VALID_STATUSES = ["Available", "On Rent", "Sold", "Returned", "Damaged", "Lost", "Inactive"]
RETURN_CONDITIONS = ["Good", "Damaged", "Lost"]


def issue_equipment(
    *,
    equipment_id: str,
    patient_id: str,
    bill_id: str = "",
    issue_date: str | None = None,
    expected_return_date: str = "",
    rent_amount: float = 0,
    deposit: float = 0,
    notes: str = "",
    user: dict | None,
    ip: str = "",
) -> tuple[dict | None, list[str]]:
    eq = db.get_record("equipment", equipment_id)
    if not eq:
        return None, ["Equipment not found."]
    if str(eq.get("status")) != "Available":
        return None, [f"Equipment is not available (current status: {eq.get('status')})."]
    if not patient_id or not db.get_record("patients", patient_id):
        return None, ["Valid patient is required."]
    before = dict(eq)
    db.update_record("equipment", equipment_id, {"status": "On Rent", "updated_at": db.now_iso()})
    txn = {
        "txn_id": db.new_uuid("ETX-"),
        "equipment_id": equipment_id,
        "serial_number": str(eq.get("serial_number", "")),
        "bill_id": bill_id or "",
        "patient_id": patient_id,
        "txn_type": "Issue",
        "issue_date": issue_date or db.today_str(),
        "expected_return_date": expected_return_date or "",
        "return_date": "",
        "condition": "",
        "rent_amount": money(rent_amount),
        "deposit": money(deposit),
        "damage_charge": 0.0,
        "loss_charge": 0.0,
        "refund_amount": 0.0,
        "notes": notes or "",
        "handled_by": str((user or {}).get("username", "")),
        "created_at": db.now_iso(),
    }
    db.append_record("equipment_transactions", txn)
    log_activity(
        user=user, action="ISSUE_EQUIPMENT", entity_type="equipment", entity_id=equipment_id,
        description=f"Issued {eq.get('equipment_name')} ({equipment_id}) to patient {patient_id}",
        before={"status": before.get("status")}, after={"status": "On Rent", "txn": txn}, ip=ip,
    )
    return txn, []


def return_equipment(
    *,
    txn_id: str,
    return_date: str | None = None,
    condition: str = "Good",
    damage_charge: float = 0,
    loss_charge: float = 0,
    notes: str = "",
    user: dict | None,
    ip: str = "",
) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    txn = db.get_record("equipment_transactions", txn_id)
    if not txn or str(txn.get("txn_type")) != "Issue":
        return None, ["Open rental transaction not found."]
    if txn.get("return_date"):
        return None, ["This equipment has already been returned."]
    if condition not in RETURN_CONDITIONS:
        errors.append("Invalid return condition.")
    try:
        damage_charge = float(damage_charge or 0)
    except Exception:
        damage_charge = 0
    try:
        loss_charge = float(loss_charge or 0)
    except Exception:
        loss_charge = 0
    if damage_charge < 0 or loss_charge < 0:
        errors.append("Charges cannot be negative.")
    if errors:
        return None, errors

    deposit = float(txn.get("deposit", 0) or 0)
    deductions = damage_charge + loss_charge
    refundable = max(0.0, money(deposit - deductions))
    extra_due = max(0.0, money(deductions - deposit))

    db.update_record("equipment_transactions", txn_id, {
        "return_date": return_date or db.today_str(),
        "condition": condition,
        "damage_charge": money(damage_charge),
        "loss_charge": money(loss_charge),
        "refund_amount": refundable,
        "notes": ((txn.get("notes") or "") + f" | Return: {notes}" if notes else (txn.get("notes") or "")),
    })
    # Append a Return transaction row for the audit trail.
    return_txn = dict(txn)
    return_txn.update({
        "txn_id": db.new_uuid("ETX-"),
        "txn_type": "Return",
        "return_date": return_date or db.today_str(),
        "condition": condition,
        "damage_charge": money(damage_charge),
        "loss_charge": money(loss_charge),
        "refund_amount": refundable,
        "notes": notes or "",
        "handled_by": str((user or {}).get("username", "")),
        "created_at": db.now_iso(),
    })
    db.append_record("equipment_transactions", return_txn)

    eq = db.get_record("equipment", str(txn.get("equipment_id", "")))
    new_status = {"Good": "Available", "Damaged": "Damaged", "Lost": "Lost"}[condition]
    if eq:
        db.update_record("equipment", str(eq.get("equipment_id")), {"status": new_status, "updated_at": db.now_iso()})
    after = db.get_record("equipment_transactions", txn_id)
    log_activity(
        user=user, action="RETURN_EQUIPMENT", entity_type="equipment_transaction", entity_id=txn_id,
        description=(
            f"Returned {txn.get('equipment_id')} from patient {txn.get('patient_id')} "
            f"({condition}). Refundable: {refundable:.2f}, Extra due: {extra_due:.2f}"
        ),
        before=txn, after={**(after or {}), "extra_due": extra_due}, ip=ip,
    )
    result = dict(after or {})
    result["extra_due"] = extra_due
    return result, []


def open_rentals_for_patient(patient_id: str) -> list[dict]:
    txns = db.find_records("equipment_transactions", patient_id=patient_id, txn_type="Issue")
    return [dict(r) for _, r in txns.iterrows() if not r.get("return_date")]


def open_rentals() -> list[dict]:
    txns = db.read_table("equipment_transactions")
    if txns.empty:
        return []
    mask = (txns["txn_type"].astype(str) == "Issue") & (txns["return_date"].astype(str) == "")
    return txns[mask].to_dict("records")
