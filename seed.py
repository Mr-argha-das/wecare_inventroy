"""Development seed script — creates clearly-marked DEMO data.

Usage:
    python seed.py

Never run this in production; it inserts sample patients, services,
equipment, staff and bills for testing the complete workflow.
"""
from app import database as db
from app.main import init_defaults
from app.security import hash_password
from app.services import billing_service, equipment_service, payment_service

DEMO_TAG = "[DEMO]"


def main() -> None:
    init_defaults()

    # --- Services ---------------------------------------------------------
    services = [
        ("Nursing Care", "Trained nurse home visit / day care", 800, "Per Day"),
        ("Patient Attendant", "Full-day patient attendant", 600, "Per Day"),
        ("Physiotherapy", "Physiotherapy session at home", 500, "Per Visit"),
        ("Doctor Visit", "General physician home visit", 700, "Per Visit"),
        ("Home Care", "Hourly home care assistance", 150, "Per Hour"),
    ]
    service_ids: dict[str, str] = {}
    for name, desc, rate, btype in services:
        existing = db.find_records("services", service_name=name)
        if not existing.empty:
            service_ids[name] = str(existing.iloc[0]["service_id"])
            continue
        rec = db.append_record("services", {
            "service_id": db.generate_service_id(), "service_name": name,
            "description": f"{DEMO_TAG} {desc}", "default_rate": rate,
            "billing_type": btype, "status": "Active",
            "created_at": db.now_iso(), "updated_at": db.now_iso(),
        })
        service_ids[name] = rec["service_id"]
    print(f"Services ready: {len(service_ids)}")

    # --- Equipment --------------------------------------------------------
    equipment = [
        ("Wheelchair", "DEMO-WC-001", 9000, 12000, 150, 800, 2500),
        ("Oxygen Concentrator", "DEMO-OX-001", 45000, 55000, 400, 2200, 7000),
        ("Hospital Bed", "DEMO-HB-001", 25000, 32000, 300, 1700, 5500),
    ]
    for name, serial, cost, sale, daily, weekly, monthly in equipment:
        if not db.find_records("equipment", serial_number=serial).empty:
            continue
        db.append_record("equipment", {
            "equipment_id": db.generate_equipment_id(), "equipment_name": name,
            "serial_number": serial, "description": f"{DEMO_TAG} sample equipment",
            "status": "Available", "purchase_cost": cost, "sale_price": sale,
            "daily_rate": daily, "weekly_rate": weekly, "monthly_rate": monthly,
            "created_at": db.now_iso(), "updated_at": db.now_iso(),
        })
    print("Equipment ready")

    # --- Staff ------------------------------------------------------------
    if db.find_records("staff", username="demo_staff").empty:
        db.append_record("staff", {
            "staff_id": db.generate_staff_id(), "username": "demo_staff",
            "full_name": f"{DEMO_TAG} Demo Staff",
            "password_hash": hash_password("Staff@123"), "role": "staff",
            "permissions": "[]", "status": "Active",
            "created_at": db.now_iso(), "updated_at": db.now_iso(),
        })
        print("Demo staff created: demo_staff / Staff@123")
    else:
        print("Demo staff already exists")

    # --- Patients ---------------------------------------------------------
    patients = [
        ("Ramesh Kumar", "9876543210", "12 MG Road, Kolkata", "Suresh Kumar", "Dr. A. Sen"),
        ("Meena Devi", "9876543211", "45 Park Street, Kolkata", "Ravi Devi", "Dr. B. Roy"),
    ]
    patient_ids = []
    for name, mobile, address, attendant, doctor in patients:
        existing = db.find_records("patients", mobile=mobile)
        if not existing.empty:
            patient_ids.append(str(existing.iloc[0]["patient_id"]))
            continue
        rec = db.append_record("patients", {
            "patient_id": db.generate_patient_id(), "patient_name": f"{DEMO_TAG} {name}",
            "mobile": mobile, "address": address, "attendant_name": attendant,
            "doctor_name": doctor, "created_at": db.now_iso(),
            "updated_at": db.now_iso(), "status": "Active",
        })
        patient_ids.append(rec["patient_id"])
    print(f"Patients ready: {len(patient_ids)}")

    admin = db.find_records("staff", username="admin")
    admin_user = admin.iloc[0].to_dict() if not admin.empty else {"username": "seed", "role": "admin"}

    # --- Sample service bill ----------------------------------------------
    if db.read_table("bills").empty:
        payload, errors = billing_service.prepare_bill_payload(
            patient_id=patient_ids[0], bill_type="service", bill_date=db.today_str(),
            items=[{
                "item_type": "service", "ref_id": service_ids["Nursing Care"],
                "description": "Nursing Care", "serial_number": "",
                "start_date": db.today_str(), "end_date": db.today_str(),
                "billing_type": "Per Day", "quantity": 5, "rate": 800, "discount": 0,
            }],
            bill_discount=200, tax_rate=0,
        )
        assert not errors, errors
        bill = billing_service.create_bill(payload, user=admin_user, ip="127.0.0.1")
        payment_service.add_payment(
            bill_id=bill["bill_id"], patient_id=patient_ids[0], payment_date=db.today_str(),
            method="UPI", amount=2000, transaction_id="DEMO-TXN-1",
            notes="Demo part payment", user=admin_user, ip="127.0.0.1",
        )
        print(f"Sample bill created: {bill['bill_number']} (partial payment recorded)")
    else:
        print("Bills already exist; skipping sample bill")

    print("Seed complete. All sample rows are marked [DEMO].")


if __name__ == "__main__":
    main()
