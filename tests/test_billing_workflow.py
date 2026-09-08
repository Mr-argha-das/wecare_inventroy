"""Critical-logic tests: patients, bills, payments, refunds, equipment, permissions."""
import json

from app import database as db
from app.calculations import bill_totals, payment_status
from app.permissions import has_permission
from app.services import billing_service, equipment_service, payment_service


def _patient(name="Test Patient"):
    return db.append_record("patients", {
        "patient_id": db.generate_patient_id(), "patient_name": name, "mobile": "9876543210",
        "address": "Addr", "attendant_name": "Att", "doctor_name": "Doc",
        "created_at": db.now_iso(), "updated_at": db.now_iso(), "status": "Active",
    })


def _service():
    return db.append_record("services", {
        "service_id": db.generate_service_id(), "service_name": "Nursing Care",
        "description": "", "default_rate": 800, "billing_type": "Per Day",
        "status": "Active", "created_at": db.now_iso(), "updated_at": db.now_iso(),
    })


def _equipment():
    return db.append_record("equipment", {
        "equipment_id": db.generate_equipment_id(), "equipment_name": "Wheelchair",
        "serial_number": "SN-1", "description": "", "status": "Available",
        "purchase_cost": 9000, "sale_price": 12000, "daily_rate": 150,
        "weekly_rate": 800, "monthly_rate": 2500,
        "created_at": db.now_iso(), "updated_at": db.now_iso(),
    })


def test_patient_creation(admin_user):
    p = _patient()
    assert p["patient_id"].startswith("WC-P-")
    assert db.get_record("patients", p["patient_id"])["patient_name"] == "Test Patient"


def test_patient_ids_unique(admin_user):
    a = _patient("A")
    b = _patient("B")
    assert a["patient_id"] != b["patient_id"]


def test_bill_number_generation(admin_user):
    p = _patient()
    s = _service()
    n1 = db.generate_bill_number("2026-09-08")
    assert n1.startswith("WC-INV-2026-")
    payload, errors = billing_service.prepare_bill_payload(
        patient_id=p["patient_id"], bill_type="service", bill_date="2026-09-08",
        items=[{"item_type": "service", "ref_id": s["service_id"], "description": "Nursing Care",
                "serial_number": "", "start_date": "2026-09-01", "end_date": "2026-09-05",
                "billing_type": "Per Day", "quantity": 5, "rate": 800, "discount": 0}],
    )
    assert not errors
    b1 = billing_service.create_bill(payload, user=admin_user, ip="test")
    b2 = billing_service.create_bill(payload, user=admin_user, ip="test")
    assert b1["bill_number"] != b2["bill_number"]
    assert b1["bill_number"].startswith("WC-INV-2026-")


def test_service_calculation_and_discount_tax(admin_user):
    totals = bill_totals(
        [{"quantity": 10, "rate": 800, "discount": 0}],
        bill_discount=500, tax_rate=18,
    )
    assert totals["subtotal"] == 8000.0
    assert totals["discount"] == 500.0
    assert totals["tax"] == round((8000 - 500) * 0.18, 2)
    assert totals["grand_total"] == round(7500 + 7500 * 0.18, 2)


def test_equipment_rental_quantity():
    from app.calculations import rental_quantity
    assert rental_quantity("2026-09-01", "2026-09-07", "Daily") == 7
    assert rental_quantity("2026-09-01", "2026-09-07", "Weekly") == 1
    assert rental_quantity("2026-09-01", "2026-09-10", "Weekly") == 2
    assert rental_quantity("2026-09-10", "2026-09-01", "Daily") == 0


def test_partial_and_full_payment(admin_user):
    p = _patient()
    s = _service()
    payload, _ = billing_service.prepare_bill_payload(
        patient_id=p["patient_id"], bill_type="service", bill_date=db.today_str(),
        items=[{"item_type": "service", "ref_id": s["service_id"], "description": "Nursing",
                "serial_number": "", "start_date": "", "end_date": "", "billing_type": "Per Visit",
                "quantity": 10, "rate": 1000, "discount": 0}],
    )
    bill = billing_service.create_bill(payload, user=admin_user, ip="test")
    assert bill["status"] == "Pending"
    pay1, errs = payment_service.add_payment(
        bill_id=bill["bill_id"], patient_id=p["patient_id"], payment_date=db.today_str(),
        method="Cash", amount=6000, user=admin_user, ip="test")
    assert not errs
    b = db.get_record("bills", bill["bill_id"])
    assert b["status"] == "Partial" and b["remaining_amount"] == 4000.0
    pay2, errs = payment_service.add_payment(
        bill_id=bill["bill_id"], patient_id=p["patient_id"], payment_date=db.today_str(),
        method="UPI", amount=4000, transaction_id="TXN1", user=admin_user, ip="test")
    assert not errs
    b = db.get_record("bills", bill["bill_id"])
    assert b["status"] == "Paid" and b["remaining_amount"] == 0.0


def test_payment_status_derivation():
    assert payment_status(10000, 0)[0] == "Pending"
    assert payment_status(10000, 6000)[0] == "Partial"
    assert payment_status(10000, 10000)[0] == "Paid"
    assert payment_status(10000, 6000, cancelled=True)[0] == "Canceled"


def test_refund(admin_user):
    p = _patient()
    s = _service()
    payload, _ = billing_service.prepare_bill_payload(
        patient_id=p["patient_id"], bill_type="service", bill_date=db.today_str(),
        items=[{"item_type": "service", "ref_id": s["service_id"], "description": "N",
                "serial_number": "", "start_date": "", "end_date": "", "billing_type": "Per Visit",
                "quantity": 1, "rate": 1000, "discount": 0}],
    )
    bill = billing_service.create_bill(payload, user=admin_user, ip="test")
    pay, _ = payment_service.add_payment(
        bill_id=bill["bill_id"], patient_id=p["patient_id"], payment_date=db.today_str(),
        method="Cash", amount=1000, user=admin_user, ip="test")
    refund, errs = payment_service.create_refund(
        bill_id=bill["bill_id"], payment_id=pay["payment_id"], patient_id=p["patient_id"],
        amount=400, method="Cash", reason="test", user=admin_user, ip="test")
    assert not errs
    b = db.get_record("bills", bill["bill_id"])
    assert b["refunded_amount"] == 400.0
    assert b["status"] == "Partial"
    # original payment still exists
    assert db.get_record("payments", pay["payment_id"]) is not None
    # over-refund blocked
    _, errs = payment_service.create_refund(
        bill_id=bill["bill_id"], payment_id=pay["payment_id"], patient_id=p["patient_id"],
        amount=9999, method="Cash", reason="x", user=admin_user, ip="test")
    assert errs


def test_bill_cancellation_keeps_history(admin_user):
    p = _patient()
    s = _service()
    payload, _ = billing_service.prepare_bill_payload(
        patient_id=p["patient_id"], bill_type="service", bill_date=db.today_str(),
        items=[{"item_type": "service", "ref_id": s["service_id"], "description": "N",
                "serial_number": "", "start_date": "", "end_date": "", "billing_type": "Per Visit",
                "quantity": 1, "rate": 100, "discount": 0}],
    )
    bill = billing_service.create_bill(payload, user=admin_user, ip="test")
    num = bill["bill_number"]
    updated, errs = billing_service.cancel_bill(bill["bill_id"], "test reason", user=admin_user, ip="test")
    assert not errs
    assert updated["bill_number"] == num  # number retained
    assert updated["status"] == "Canceled"
    assert db.get_record("bills", bill["bill_id"]) is not None  # never deleted
    # canceled excluded from revenue
    from app.services import report_service
    assert report_service.profit_report("2000-01-01", "2099-12-31")["revenue"] == 0.0


def test_bill_duplication(admin_user):
    p = _patient()
    s = _service()
    payload, _ = billing_service.prepare_bill_payload(
        patient_id=p["patient_id"], bill_type="service", bill_date=db.today_str(),
        items=[{"item_type": "service", "ref_id": s["service_id"], "description": "N",
                "serial_number": "", "start_date": "", "end_date": "", "billing_type": "Per Visit",
                "quantity": 2, "rate": 100, "discount": 0}],
    )
    bill = billing_service.create_bill(payload, user=admin_user, ip="test")
    payment_service.add_payment(bill_id=bill["bill_id"], patient_id=p["patient_id"],
                                payment_date=db.today_str(), method="Cash", amount=200,
                                user=admin_user, ip="test")
    dup = billing_service.duplicate_bill_payload(bill["bill_id"])
    assert dup is not None
    payload2, errors = billing_service.prepare_bill_payload(
        patient_id=dup["patient_id"], bill_type=dup["bill_type"], bill_date=dup["bill_date"],
        items=dup["items"], bill_discount=dup["bill_discount"], tax_rate=dup["tax_rate"])
    assert not errors
    bill2 = billing_service.create_bill(payload2, user=admin_user, ip="test")
    assert bill2["bill_number"] != bill["bill_number"]
    assert bill2["received_amount"] == 0  # payments not copied
    assert bill2["status"] == "Pending"


def test_equipment_issue_return_sync(admin_user):
    p = _patient()
    eq = _equipment()
    txn, errs = equipment_service.issue_equipment(
        equipment_id=eq["equipment_id"], patient_id=p["patient_id"], user=admin_user, ip="test")
    assert not errs
    assert db.get_record("equipment", eq["equipment_id"])["status"] == "On Rent"
    # cannot double-issue
    _, errs = equipment_service.issue_equipment(
        equipment_id=eq["equipment_id"], patient_id=p["patient_id"], user=admin_user, ip="test")
    assert errs
    result, errs = equipment_service.return_equipment(
        txn_id=txn["txn_id"], condition="Good", user=admin_user, ip="test")
    assert not errs
    assert db.get_record("equipment", eq["equipment_id"])["status"] == "Available"


def test_equipment_damaged_return(admin_user):
    p = _patient()
    eq = _equipment()
    txn, _ = equipment_service.issue_equipment(
        equipment_id=eq["equipment_id"], patient_id=p["patient_id"], deposit=2000,
        user=admin_user, ip="test")
    result, errs = equipment_service.return_equipment(
        txn_id=txn["txn_id"], condition="Damaged", damage_charge=500, user=admin_user, ip="test")
    assert not errs
    assert db.get_record("equipment", eq["equipment_id"])["status"] == "Damaged"
    assert result["refund_amount"] == 1500.0


def test_permissions():
    admin = {"role": "admin", "permissions": "[]"}
    staff = {"role": "staff", "permissions": json.dumps(["view_dashboard"])}
    assert has_permission(admin, "cancel_bill")
    assert has_permission(staff, "view_dashboard")
    assert not has_permission(staff, "cancel_bill")
    assert not has_permission(None, "view_dashboard")


def test_backdate_rule_enforced_in_router():
    # Router-level rule: bills._check_bill_date blocks non-permission users.
    from app.routers.bills import _check_bill_date
    staff = {"role": "staff", "permissions": "[]"}
    assert _check_bill_date(None, db.today_str(), staff) is None
    assert _check_bill_date(None, "2020-01-01", staff) is not None
    admin = {"role": "admin", "permissions": "[]"}
    # admin (or roles table default) — role lookup uses DB; admin bypasses via role
    assert _check_bill_date(None, "2020-01-01", admin) is None


def test_activity_logging(admin_user):
    p = _patient()
    s = _service()
    payload, _ = billing_service.prepare_bill_payload(
        patient_id=p["patient_id"], bill_type="service", bill_date=db.today_str(),
        items=[{"item_type": "service", "ref_id": s["service_id"], "description": "N",
                "serial_number": "", "start_date": "", "end_date": "", "billing_type": "Per Visit",
                "quantity": 1, "rate": 100, "discount": 0}],
    )
    bill = billing_service.create_bill(payload, user=admin_user, ip="1.2.3.4")
    logs = db.find_records("activity_logs", entity_id=bill["bill_id"])
    assert not logs.empty
    assert "CREATE_BILL" in set(logs["action"].astype(str))
    # edit stores before/after
    billing_service.edit_bill(bill["bill_id"], payload, user=admin_user, ip="test")
    logs = db.find_records("activity_logs", entity_id=bill["bill_id"])
    edits = logs[logs["action"].astype(str) == "EDIT_BILL"]
    assert not edits.empty
    assert edits.iloc[0]["before_data"] != ""
    assert edits.iloc[0]["after_data"] != ""


def test_validation_blocks_bad_items(admin_user):
    p = _patient()
    payload, errors = billing_service.prepare_bill_payload(
        patient_id=p["patient_id"], bill_type="service", bill_date=db.today_str(),
        items=[{"item_type": "service", "ref_id": "", "description": "X",
                "serial_number": "", "start_date": "", "end_date": "",
                "billing_type": "", "quantity": 0, "rate": -5, "discount": 0}],
    )
    assert payload is None and errors


def test_deposit_adjust_and_refund(admin_user):
    p = _patient()
    s = _service()
    dep, errs = payment_service.add_deposit(
        patient_id=p["patient_id"], amount=5000, method="Cash",
        purpose="Advance", user=admin_user, ip="test")
    assert not errs
    payload, _ = billing_service.prepare_bill_payload(
        patient_id=p["patient_id"], bill_type="service", bill_date=db.today_str(),
        items=[{"item_type": "service", "ref_id": s["service_id"], "description": "N",
                "serial_number": "", "start_date": "", "end_date": "", "billing_type": "Per Visit",
                "quantity": 1, "rate": 3000, "discount": 0}],
    )
    bill = billing_service.create_bill(payload, user=admin_user, ip="test")
    adj, errs = payment_service.adjust_deposit_to_bill(dep["deposit_id"], bill["bill_id"], 3000,
                                                       user=admin_user, ip="test")
    assert not errs
    b = db.get_record("bills", bill["bill_id"])
    assert b["status"] == "Paid"
    assert db.get_record("deposits", dep["deposit_id"])["adjusted_amount"] == 3000.0
