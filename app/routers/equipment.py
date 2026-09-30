"""Equipment master + issue/return workflow."""
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..audit import log_activity
from ..dependencies import require_permission, template_context
from ..security import validate_csrf
from ..services import equipment_service
from ..utils import client_ip, flash, paginate

router = APIRouter(prefix="/equipment", tags=["equipment"])

STATUSES = ["Available", "On Rent", "Sold", "Returned", "Damaged", "Lost", "Inactive"]


@router.get("", response_class=HTMLResponse)
def equipment_list(
    request: Request, page: int = Query(1), search: str = Query(""), status: str = Query(""),
    user: dict = Depends(require_permission("view_equipment")),
):
    from ..main import templates
    df = db.read_table("equipment")
    counts: dict[str, int] = {s: 0 for s in STATUSES}
    if not df.empty:
        for s in df["status"].astype(str):
            counts[s] = counts.get(s, 0) + 1
        df = df.sort_values("equipment_name")
        if search:
            s = search.lower()
            mask = (
                df["equipment_name"].astype(str).str.lower().str.contains(s, na=False)
                | df["serial_number"].astype(str).str.lower().str.contains(s, na=False)
                | df["equipment_id"].astype(str).str.lower().str.contains(s, na=False)
            )
            # patient search: find equipment currently issued to matching patients
            patients = db.read_table("patients")
            if not patients.empty:
                hit_pids = set(patients[patients["patient_name"].astype(str).str.lower().str.contains(s, na=False)]["patient_id"].astype(str))
                txns = db.read_table("equipment_transactions")
                if hit_pids and not txns.empty:
                    open_tx = txns[(txns["txn_type"].astype(str) == "Issue") & (txns["return_date"].astype(str) == "")]
                    eq_ids = set(open_tx[open_tx["patient_id"].astype(str).isin(hit_pids)]["equipment_id"].astype(str))
                    mask |= df["equipment_id"].astype(str).isin(eq_ids)
            df = df[mask]
        if status:
            df = df[df["status"].astype(str) == status]
    total = len(df)
    pg = paginate(total, page, 20)
    rows = df.iloc[pg["start"]:pg["end"]].to_dict("records") if total else []
    # current holder for on-rent items
    holders: dict[str, str] = {}
    txns = db.read_table("equipment_transactions")
    patients = db.read_table("patients")
    pnames = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    if not txns.empty:
        open_tx = txns[(txns["txn_type"].astype(str) == "Issue") & (txns["return_date"].astype(str) == "")]
        for _, t in open_tx.iterrows():
            holders[str(t["equipment_id"])] = pnames.get(str(t["patient_id"]), str(t["patient_id"]))
    return templates.TemplateResponse(request, "equipment/list.html", template_context(request, {
        "active_nav": "equipment", "rows": rows, "pg": pg, "search": search, "status": status,
        "statuses": STATUSES, "counts": counts, "total": len(db.read_table("equipment")),
        "holders": holders,
    }))


@router.get("/new", response_class=HTMLResponse)
def equipment_new(request: Request, user: dict = Depends(require_permission("manage_equipment"))):
    from ..main import templates
    return templates.TemplateResponse(request, "equipment/form.html", template_context(request, {
        "active_nav": "equipment", "mode": "new", "equipment": {}, "statuses": STATUSES,
    }))


def _parse_money(value: str) -> float:
    try:
        return float(value or 0)
    except Exception:
        return -1


@router.post("/new")
def equipment_create(
    request: Request,
    equipment_name: str = Form(""),
    serial_number: str = Form(""),
    description: str = Form(""),
    purchase_cost: str = Form("0"),
    sale_price: str = Form("0"),
    daily_rate: str = Form("0"),
    weekly_rate: str = Form("0"),
    monthly_rate: str = Form("0"),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("manage_equipment")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/equipment/new", status_code=303)
    equipment_name = equipment_name.strip()
    serial_number = serial_number.strip()
    costs = {k: _parse_money(v) for k, v in {
        "purchase_cost": purchase_cost, "sale_price": sale_price, "daily_rate": daily_rate,
        "weekly_rate": weekly_rate, "monthly_rate": monthly_rate}.items()}
    errors = []
    if not equipment_name:
        errors.append("Equipment name is required.")
    if any(v < 0 for v in costs.values()):
        errors.append("Rates and costs cannot be negative.")
    if serial_number and not db.find_records("equipment", serial_number=serial_number).empty:
        errors.append("Serial number already exists.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url="/equipment/new", status_code=303)
    record = {
        "equipment_id": db.generate_equipment_id(),
        "equipment_name": equipment_name,
        "serial_number": serial_number,
        "description": description.strip(),
        "status": "Available",
        **costs,
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
    }
    db.append_record("equipment", record)
    log_activity(user=user, action="CREATE_EQUIPMENT", entity_type="equipment", entity_id=record["equipment_id"],
                 description=f"Created equipment {equipment_name}", after=record, ip=client_ip(request))
    flash(request, "Equipment created successfully.")
    return RedirectResponse(url=f"/equipment/{record['equipment_id']}", status_code=303)


@router.get("/{equipment_id}", response_class=HTMLResponse)
def equipment_detail(request: Request, equipment_id: str, user: dict = Depends(require_permission("view_equipment"))):
    from ..main import templates
    eq = db.get_record("equipment", equipment_id)
    if not eq:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Equipment not found")
    txns = db.find_records("equipment_transactions", equipment_id=equipment_id).sort_values("created_at", ascending=False).to_dict("records")
    patients = db.read_table("patients")
    pnames = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    open_txn = next((t for t in txns if t.get("txn_type") == "Issue" and not t.get("return_date")), None)
    all_patients = db.read_table("patients")
    all_patients = all_patients[all_patients["status"].astype(str) == "Active"].sort_values("patient_name").to_dict("records") if not all_patients.empty else []
    return templates.TemplateResponse(request, "equipment/detail.html", template_context(request, {
        "active_nav": "equipment", "equipment": eq, "txns": txns, "pnames": pnames,
        "open_txn": open_txn, "patients": all_patients,
    }))


@router.get("/{equipment_id}/edit", response_class=HTMLResponse)
def equipment_edit(request: Request, equipment_id: str, user: dict = Depends(require_permission("manage_equipment"))):
    from ..main import templates
    eq = db.get_record("equipment", equipment_id)
    if not eq:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Equipment not found")
    return templates.TemplateResponse(request, "equipment/form.html", template_context(request, {
        "active_nav": "equipment", "mode": "edit", "equipment": eq, "statuses": STATUSES,
    }))


@router.post("/{equipment_id}/edit")
def equipment_update(
    request: Request, equipment_id: str,
    equipment_name: str = Form(""),
    serial_number: str = Form(""),
    description: str = Form(""),
    purchase_cost: str = Form("0"),
    sale_price: str = Form("0"),
    daily_rate: str = Form("0"),
    weekly_rate: str = Form("0"),
    monthly_rate: str = Form("0"),
    status: str = Form("Available"),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("manage_equipment")),
):
    eq = db.get_record("equipment", equipment_id)
    if not eq:
        flash(request, "Equipment not found.", "error")
        return RedirectResponse(url="/equipment", status_code=303)
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/equipment/{equipment_id}/edit", status_code=303)
    equipment_name = equipment_name.strip()
    serial_number = serial_number.strip()
    costs = {k: _parse_money(v) for k, v in {
        "purchase_cost": purchase_cost, "sale_price": sale_price, "daily_rate": daily_rate,
        "weekly_rate": weekly_rate, "monthly_rate": monthly_rate}.items()}
    errors = []
    if not equipment_name:
        errors.append("Equipment name is required.")
    if any(v < 0 for v in costs.values()):
        errors.append("Rates and costs cannot be negative.")
    if status not in STATUSES:
        errors.append("Invalid status.")
    dup = db.find_records("equipment", serial_number=serial_number)
    if serial_number and not dup.empty and str(dup.iloc[0]["equipment_id"]) != equipment_id:
        errors.append("Serial number already exists.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url=f"/equipment/{equipment_id}/edit", status_code=303)
    before = dict(eq)
    db.update_record("equipment", equipment_id, {
        "equipment_name": equipment_name, "serial_number": serial_number,
        "description": description.strip(), "status": status, **costs, "updated_at": db.now_iso(),
    })
    log_activity(user=user, action="EDIT_EQUIPMENT", entity_type="equipment", entity_id=equipment_id,
                 description=f"Edited equipment {equipment_name}", before=before,
                 after=db.get_record("equipment", equipment_id), ip=client_ip(request))
    flash(request, "Equipment updated successfully.")
    return RedirectResponse(url=f"/equipment/{equipment_id}", status_code=303)


@router.post("/{equipment_id}/issue")
def equipment_issue(
    request: Request, equipment_id: str,
    patient_id: str = Form(""),
    issue_date: str = Form(""),
    expected_return_date: str = Form(""),
    rent_amount: str = Form("0"),
    deposit: str = Form("0"),
    notes: str = Form(""),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("manage_equipment")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/equipment/{equipment_id}", status_code=303)
    try:
        rent = float(rent_amount or 0)
    except Exception:
        rent = 0
    try:
        dep = float(deposit or 0)
    except Exception:
        dep = 0
    if rent < 0 or dep < 0:
        flash(request, "Rent and deposit cannot be negative.", "error")
        return RedirectResponse(url=f"/equipment/{equipment_id}", status_code=303)
    txn, errors = equipment_service.issue_equipment(
        equipment_id=equipment_id, patient_id=patient_id, issue_date=issue_date or db.today_str(),
        expected_return_date=expected_return_date, rent_amount=rent, deposit=dep,
        notes=notes, user=user, ip=client_ip(request),
    )
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url=f"/equipment/{equipment_id}", status_code=303)
    # Record security deposit as a deposit entry when provided.
    if dep > 0:
        from ..services import payment_service
        payment_service.add_deposit(
            patient_id=patient_id, deposit_date=issue_date or db.today_str(), amount=dep,
            method="Cash", purpose="Security Deposit",
            notes=f"Security deposit for {equipment_id} (txn {txn['txn_id'][:8]})",
            user=user, ip=client_ip(request),
        )
    flash(request, "Equipment issued successfully.")
    return RedirectResponse(url=f"/equipment/{equipment_id}", status_code=303)


@router.get("/transactions/{txn_id}/return", response_class=HTMLResponse)
def equipment_return_form(request: Request, txn_id: str, user: dict = Depends(require_permission("manage_equipment"))):
    from ..main import templates
    txn = db.get_record("equipment_transactions", txn_id)
    if not txn or txn.get("txn_type") != "Issue" or txn.get("return_date"):
        flash(request, "Open rental transaction not found.", "error")
        return RedirectResponse(url="/equipment", status_code=303)
    eq = db.get_record("equipment", str(txn.get("equipment_id", ""))) or {}
    patient = db.get_record("patients", str(txn.get("patient_id", ""))) or {}
    return templates.TemplateResponse(request, "equipment/return.html", template_context(request, {
        "active_nav": "equipment", "txn": txn, "equipment": eq, "patient": patient,
        "conditions": equipment_service.RETURN_CONDITIONS,
    }))


@router.post("/transactions/{txn_id}/return")
def equipment_return_submit(
    request: Request, txn_id: str,
    return_date: str = Form(""),
    condition: str = Form("Good"),
    damage_charge: str = Form("0"),
    loss_charge: str = Form("0"),
    notes: str = Form(""),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("manage_equipment")),
):
    txn = db.get_record("equipment_transactions", txn_id)
    eq_id = str((txn or {}).get("equipment_id", ""))
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/equipment/transactions/{txn_id}/return", status_code=303)
    try:
        dmg = float(damage_charge or 0)
    except Exception:
        dmg = 0
    try:
        loss = float(loss_charge or 0)
    except Exception:
        loss = 0
    result, errors = equipment_service.return_equipment(
        txn_id=txn_id, return_date=return_date or db.today_str(), condition=condition,
        damage_charge=dmg, loss_charge=loss, notes=notes, user=user, ip=client_ip(request),
    )
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url=f"/equipment/transactions/{txn_id}/return", status_code=303)
    flash(request, "Equipment returned successfully.")
    return RedirectResponse(url=f"/equipment/{eq_id}", status_code=303)
