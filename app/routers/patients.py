"""Patient / customer management."""
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..audit import log_activity
from ..dependencies import require_permission, template_context
from ..security import validate_csrf
from ..services import report_service
from ..utils import client_ip, flash, normalize_mobile, paginate, valid_mobile

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_class=HTMLResponse)
def patient_list(
    request: Request,
    page: int = Query(1),
    search: str = Query(""),
    status: str = Query(""),
    user: dict = Depends(require_permission("view_patients")),
):
    from ..main import templates
    df = db.read_table("patients")
    if not df.empty:
        df = df.sort_values("created_at", ascending=False)
        if search:
            s = search.lower()
            mask = (
                df["patient_name"].astype(str).str.lower().str.contains(s, na=False)
                | df["mobile"].astype(str).str.contains(s, na=False)
                | df["patient_id"].astype(str).str.lower().str.contains(s, na=False)
                | df["doctor_name"].astype(str).str.lower().str.contains(s, na=False)
            )
            df = df[mask]
        if status:
            df = df[df["status"].astype(str) == status]
    total = len(df)
    pg = paginate(total, page, 20)
    rows = df.iloc[pg["start"]:pg["end"]].to_dict("records") if total else []
    # pending per patient
    pending_map: dict[str, float] = {}
    for r in report_service.pending_payments():
        pending_map[r["patient_id"]] = pending_map.get(r["patient_id"], 0.0) + r["pending"]
    return templates.TemplateResponse(request, "patients/list.html", template_context(request, {
        "active_nav": "patients", "rows": rows, "pg": pg, "search": search, "status": status,
        "pending_map": pending_map,
    }))


@router.get("/new", response_class=HTMLResponse)
def patient_new(request: Request, user: dict = Depends(require_permission("create_patient"))):
    from ..main import templates
    return templates.TemplateResponse(request, "patients/form.html", template_context(request, {
        "active_nav": "patients", "mode": "new", "patient": {},
    }))


@router.post("/new")
def patient_create(
    request: Request,
    patient_name: str = Form(""),
    mobile: str = Form(""),
    address: str = Form(""),
    attendant_name: str = Form(""),
    doctor_name: str = Form(""),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("create_patient")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/patients/new", status_code=303)
    patient_name = patient_name.strip()
    mobile_n = normalize_mobile(mobile)
    errors = []
    if not patient_name:
        errors.append("Patient name is required.")
    if not valid_mobile(mobile_n):
        errors.append("Enter a valid 10-digit mobile number.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url="/patients/new", status_code=303)
    record = {
        "patient_id": db.generate_patient_id(),
        "patient_name": patient_name,
        "mobile": mobile_n,
        "address": address.strip(),
        "attendant_name": attendant_name.strip(),
        "doctor_name": doctor_name.strip(),
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
        "status": "Active",
    }
    db.append_record("patients", record)
    log_activity(user=user, action="CREATE_PATIENT", entity_type="patient", entity_id=record["patient_id"],
                 description=f"Created patient {patient_name} ({record['patient_id']})",
                 after=record, ip=client_ip(request))
    flash(request, "Patient created successfully.")
    return RedirectResponse(url=f"/patients/{record['patient_id']}", status_code=303)


@router.get("/{patient_id}", response_class=HTMLResponse)
def patient_detail(request: Request, patient_id: str, user: dict = Depends(require_permission("view_patients"))):
    from ..main import templates
    patient = db.get_record("patients", patient_id)
    if not patient:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Patient not found")
    bills = db.find_records("bills", patient_id=patient_id).sort_values("bill_date", ascending=False).to_dict("records")
    payments = db.find_records("payments", patient_id=patient_id).sort_values("payment_date", ascending=False).to_dict("records")
    deposits = db.find_records("deposits", patient_id=patient_id).sort_values("deposit_date", ascending=False).to_dict("records")
    refunds = db.find_records("refunds", patient_id=patient_id).sort_values("refund_date", ascending=False).to_dict("records")
    services = db.find_records("service_records", patient_id=patient_id).to_dict("records")
    txns = db.find_records("equipment_transactions", patient_id=patient_id).sort_values("issue_date", ascending=False).to_dict("records")
    quotations = db.find_records("quotations", patient_id=patient_id).to_dict("records")
    logs = db.find_records("activity_logs", entity_id=patient_id).to_dict("records")
    bill_logs_df = db.read_table("activity_logs")
    bill_ids = {b["bill_id"] for b in bills}
    extra_logs = []
    if not bill_logs_df.empty and bill_ids:
        extra_logs = bill_logs_df[bill_logs_df["entity_id"].astype(str).isin(bill_ids)].to_dict("records")
    logs = sorted(logs + extra_logs, key=lambda r: str(r.get("timestamp", "")), reverse=True)[:50]
    totals = {
        "billed": round(sum(float(b.get("grand_total", 0)) for b in bills if not b.get("cancelled")), 2),
        "received": round(sum(float(b.get("received_amount", 0)) - float(b.get("refunded_amount", 0)) for b in bills if not b.get("cancelled")), 2),
        "pending": round(sum(float(b.get("remaining_amount", 0)) for b in bills if not b.get("cancelled")), 2),
    }
    return templates.TemplateResponse(request, "patients/detail.html", template_context(request, {
        "active_nav": "patients", "patient": patient, "bills": bills, "payments": payments,
        "deposits": deposits, "refunds": refunds, "services": services, "txns": txns,
        "quotations": quotations, "logs": logs, "totals": totals,
    }))


@router.get("/{patient_id}/edit", response_class=HTMLResponse)
def patient_edit(request: Request, patient_id: str, user: dict = Depends(require_permission("edit_patient"))):
    from ..main import templates
    patient = db.get_record("patients", patient_id)
    if not patient:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Patient not found")
    return templates.TemplateResponse(request, "patients/form.html", template_context(request, {
        "active_nav": "patients", "mode": "edit", "patient": patient,
    }))


@router.post("/{patient_id}/edit")
def patient_update(
    request: Request,
    patient_id: str,
    patient_name: str = Form(""),
    mobile: str = Form(""),
    address: str = Form(""),
    attendant_name: str = Form(""),
    doctor_name: str = Form(""),
    status: str = Form("Active"),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("edit_patient")),
):
    patient = db.get_record("patients", patient_id)
    if not patient:
        flash(request, "Patient not found.", "error")
        return RedirectResponse(url="/patients", status_code=303)
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/patients/{patient_id}/edit", status_code=303)
    patient_name = patient_name.strip()
    mobile_n = normalize_mobile(mobile)
    errors = []
    if not patient_name:
        errors.append("Patient name is required.")
    if not valid_mobile(mobile_n):
        errors.append("Enter a valid 10-digit mobile number.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url=f"/patients/{patient_id}/edit", status_code=303)
    before = dict(patient)
    db.update_record("patients", patient_id, {
        "patient_name": patient_name, "mobile": mobile_n, "address": address.strip(),
        "attendant_name": attendant_name.strip(), "doctor_name": doctor_name.strip(),
        "status": status if status in ("Active", "Inactive") else "Active",
        "updated_at": db.now_iso(),
    })
    after = db.get_record("patients", patient_id)
    log_activity(user=user, action="EDIT_PATIENT", entity_type="patient", entity_id=patient_id,
                 description=f"Edited patient {patient_name} ({patient_id})",
                 before=before, after=after, ip=client_ip(request))
    flash(request, "Patient updated successfully.")
    return RedirectResponse(url=f"/patients/{patient_id}", status_code=303)
