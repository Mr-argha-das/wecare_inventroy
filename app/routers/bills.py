"""Billing: service / equipment / combined bills, payments, cancel/duplicate."""
import json

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..audit import log_activity
from ..calculations import money, rental_quantity
from ..dependencies import get_current_user, require_permission, role_default_permissions, template_context
from ..permissions import has_permission
from ..security import validate_csrf
from ..services import billing_service, payment_service
from ..services.document_service import company_profile
from ..utils import client_ip, flash, jsonable, paginate, whatsapp_link

router = APIRouter(prefix="/bills", tags=["bills"])


def _bill_form_data(user: dict) -> dict:
    patients = db.read_table("patients")
    patients = patients[patients["status"].astype(str) == "Active"].sort_values("patient_name").to_dict("records") if not patients.empty else []
    services = db.read_table("services")
    services = services[services["status"].astype(str) == "Active"].sort_values("service_name").to_dict("records") if not services.empty else []
    equipment = db.read_table("equipment").sort_values("equipment_name").to_dict("records")
    company = company_profile()
    try:
        default_tax = float(company.get("gst_rate") or 0)
    except Exception:
        default_tax = 0.0
    can_backdate = has_permission(user, "allow_backdate_billing", role_default_permissions(str(user.get("role", "staff"))))
    return {
        "patients": patients, "services": services, "equipment": equipment,
        "default_tax": default_tax, "can_backdate": can_backdate, "today": db.today_str(),
    }


def _parse_items(raw: str) -> list[dict]:
    try:
        items = json.loads(raw or "[]")
    except Exception:
        return []
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        item_type = str(it.get("item_type", "service"))
        if item_type not in ("service", "equipment"):
            continue
        billing_type = str(it.get("billing_type", ""))
        start = str(it.get("start_date", "") or "")
        end = str(it.get("end_date", "") or "")
        try:
            qty = float(it.get("quantity", 0) or 0)
        except Exception:
            qty = 0
        # Auto quantity for Per Day service items when dates given.
        if item_type == "service" and billing_type.lower().startswith("per day") and start and end:
            auto = rental_quantity(start, end, "Daily")
            if auto:
                qty = float(auto)
        if item_type == "equipment" and billing_type.lower() in ("daily", "weekly", "monthly") and start and end:
            auto = rental_quantity(start, end, billing_type)
            if auto:
                qty = float(auto)
        try:
            rate = float(it.get("rate", 0) or 0)
        except Exception:
            rate = 0
        try:
            disc = float(it.get("discount", 0) or 0)
        except Exception:
            disc = 0
        out.append({
            "item_type": item_type,
            "ref_id": str(it.get("ref_id", "") or ""),
            "description": str(it.get("description", "") or "").strip(),
            "serial_number": str(it.get("serial_number", "") or ""),
            "start_date": start, "end_date": end,
            "billing_type": billing_type,
            "quantity": qty, "rate": rate, "discount": disc,
        })
    return [i for i in out if i["description"]]


def _num(value: str, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return default


def _mark_quotation_converted(quotation_ref: str, bill_number: str, user: dict | None, ip: str) -> None:
    if not quotation_ref:
        return
    hits = db.find_records("quotations", quotation_number=quotation_ref)
    if hits.empty:
        return
    q = hits.iloc[0].to_dict()
    if str(q.get("status")) == "Converted":
        return
    db.update_record("quotations", str(q["quotation_id"]), {
        "status": "Converted", "converted_bill_number": bill_number, "updated_at": db.now_iso(),
    })
    log_activity(user=user, action="CONVERT_QUOTATION", entity_type="quotation",
                 entity_id=str(q["quotation_id"]),
                 description=f"Converted quotation {quotation_ref} to bill {bill_number}",
                 before={"status": q.get("status")},
                 after={"status": "Converted", "bill": bill_number}, ip=ip)


@router.get("", response_class=HTMLResponse)
def bill_list(
    request: Request,
    page: int = Query(1),
    search: str = Query(""),
    status: str = Query(""),
    bill_type: str = Query(""),
    start: str = Query(""),
    end: str = Query(""),
    user: dict = Depends(require_permission("create_bill")),
):
    # Bill list is part of billing workflow; require create_bill OR view_payments
    from ..main import templates
    df = db.read_table("bills")
    patients = db.read_table("patients")
    pnames = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    pmobiles = {str(r["patient_id"]): str(r["mobile"]) for _, r in patients.iterrows()} if not patients.empty else {}
    if not df.empty:
        df = df.sort_values("bill_date", ascending=False)
        if search:
            s = search.lower()
            df = df[
                df["bill_number"].astype(str).str.lower().str.contains(s, na=False)
                | df["patient_id"].astype(str).str.lower().str.contains(s, na=False)
                | df["patient_id"].astype(str).map(lambda pid: pnames.get(str(pid), "").lower()).str.contains(s, na=False)
                | df["patient_id"].astype(str).map(lambda pid: pmobiles.get(str(pid), "")).str.contains(s, na=False)
            ]
        if status:
            df = df[df["status"].astype(str) == status]
        if bill_type:
            df = df[df["bill_type"].astype(str) == bill_type]
        if start:
            df = df[df["bill_date"].astype(str) >= start]
        if end:
            df = df[df["bill_date"].astype(str) <= end]
    total = len(df)
    pg = paginate(total, page, 20)
    rows = []
    if total:
        for _, r in df.iloc[pg["start"]:pg["end"]].iterrows():
            d = r.to_dict()
            d["patient_name"] = pnames.get(str(d["patient_id"]), "-")
            rows.append(d)
    totals = {
        "grand": round(float(df["grand_total"].sum()) if total else 0.0, 2),
        "received": round(float((df["received_amount"] - df["refunded_amount"]).sum()) if total else 0.0, 2),
        "pending": round(float(df["remaining_amount"].sum()) if total else 0.0, 2),
    }
    return templates.TemplateResponse(request, "bills/list.html", template_context(request, {
        "active_nav": "bills", "rows": rows, "pg": pg, "search": search, "status": status,
        "bill_type": bill_type, "start": start, "end": end, "totals": totals,
    }))


@router.get("/new", response_class=HTMLResponse)
def bill_new(request: Request, user: dict = Depends(require_permission("create_bill"))):
    from ..main import templates
    return templates.TemplateResponse(request, "bills/form.html", template_context(request, {
        "active_nav": "bills", "mode": "new", "bill": {}, "prefill_items": [],
        "prefill": {}, **_bill_form_data(user),
    }))


def _check_bill_date(request, bill_date: str, user: dict) -> str | None:
    """Enforce back-date permission. Returns error message or None."""
    if bill_date and bill_date != db.today_str():
        if bill_date > db.today_str():
            return "Bill date cannot be in the future."
        if not has_permission(user, "allow_backdate_billing", role_default_permissions(str(user.get("role", "staff")))):
            return "You do not have permission for back-date billing. Please use today's date."
    return None


@router.post("/new")
async def bill_create(request: Request, user: dict = Depends(require_permission("create_bill"))):
    from ..main import templates
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/bills/new", status_code=303)
    action = str(form.get("action", "save"))
    bill_date = str(form.get("bill_date", "") or db.today_str())
    date_err = _check_bill_date(request, bill_date, user)
    payload, errors = billing_service.prepare_bill_payload(
        patient_id=str(form.get("patient_id", "")),
        bill_type=str(form.get("bill_type", "service")),
        bill_date=bill_date,
        items=_parse_items(str(form.get("items_json", "[]"))),
        bill_discount=_num(str(form.get("bill_discount", "0"))),
        tax_rate=_num(str(form.get("tax_rate", "0"))),
        deposit=_num(str(form.get("deposit", "0"))),
        damage_charges=_num(str(form.get("damage_charges", "0"))),
        loss_charges=_num(str(form.get("loss_charges", "0"))),
        other_charges=_num(str(form.get("other_charges", "0"))),
        notes=str(form.get("notes", "")),
        quotation_ref=str(form.get("quotation_ref", "")),
    )
    # Equipment availability validation for new items.
    if payload:
        for it in payload["items"]:
            if it["item_type"] == "equipment" and it["ref_id"]:
                eq = db.get_record("equipment", it["ref_id"])
                if not eq:
                    errors.append(f"Equipment '{it['description']}' no longer exists.")
                elif str(eq.get("status")) not in ("Available",) and str(it.get("billing_type", "")).lower() != "sale":
                    # Allow if already On Rent only when previously linked to this flow — new bills require Available.
                    errors.append(f"Equipment '{it['description']}' is not available (status: {eq.get('status')}).")
    if date_err:
        errors.insert(0, date_err)
    if errors:
        for e in errors:
            flash(request, e, "error")
        return templates.TemplateResponse(request, "bills/form.html", template_context(request, {
            "active_nav": "bills", "mode": "new", "bill": dict(form),
            "prefill_items": _parse_items(str(form.get("items_json", "[]"))),
            "prefill": dict(form), **_bill_form_data(user),
        }), status_code=422)
    assert payload is not None
    if action == "preview":
        patient = db.get_record("patients", payload["patient_id"]) or {}
        return templates.TemplateResponse(request, "bills/preview.html", template_context(request, {
            "active_nav": "bills", "mode": "new", "payload": payload, "patient": patient,
            "payload_json": json.dumps({**payload, "bill_date": bill_date}),
            "company": company_profile(), "bill_id": "",
            "duplicate_of": str(form.get("duplicate_of", "")),
            "initial_payment": {
                "amount": _num(str(form.get("pay_amount", "0"))),
                "method": str(form.get("pay_method", "Cash")),
                "transaction_id": str(form.get("pay_transaction_id", "")),
                "date": str(form.get("pay_date", "") or bill_date),
            },
        }))
    # Save
    backdated = bill_date != db.today_str()
    bill = billing_service.create_bill(payload, user=user, ip=client_ip(request))
    _mark_quotation_converted(payload.get("quotation_ref", ""), bill["bill_number"], user, client_ip(request))
    if backdated:
        log_activity(user=user, action="BACKDATE_BILL", entity_type="bill", entity_id=bill["bill_id"],
                     description=f"Created back-dated bill {bill['bill_number']} for {bill_date}",
                     after={"bill_date": bill_date}, ip=client_ip(request))
    dup_of = str(form.get("duplicate_of", ""))
    if dup_of:
        orig = db.get_record("bills", dup_of)
        log_activity(user=user, action="DUPLICATE_BILL", entity_type="bill", entity_id=bill["bill_id"],
                     description=f"Duplicated bill {(orig or {}).get('bill_number', dup_of)} as {bill['bill_number']}",
                     before={"source_bill_id": dup_of}, after={"bill_number": bill["bill_number"]}, ip=client_ip(request))
    # Optional immediate payment.
    pay_amount = _num(str(form.get("pay_amount", "0")))
    if pay_amount > 0:
        payment_service.add_payment(
            bill_id=bill["bill_id"], patient_id=bill["patient_id"],
            payment_date=str(form.get("pay_date", "") or bill_date),
            method=str(form.get("pay_method", "Cash")),
            amount=pay_amount, transaction_id=str(form.get("pay_transaction_id", "")),
            notes="Payment recorded with bill creation.", user=user, ip=client_ip(request),
        )
    flash(request, f"Bill {bill['bill_number']} created successfully.")
    return RedirectResponse(url=f"/bills/{bill['bill_id']}", status_code=303)


@router.post("/new/confirm")
async def bill_confirm(request: Request, user: dict = Depends(require_permission("create_bill"))):
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/bills/new", status_code=303)
    try:
        data = json.loads(str(form.get("payload_json", "{}")))
    except Exception:
        flash(request, "Invalid preview data. Please rebuild the bill.", "error")
        return RedirectResponse(url="/bills/new", status_code=303)
    bill_date = str(data.get("bill_date", "") or db.today_str())
    date_err = _check_bill_date(request, bill_date, user)
    payload, errors = billing_service.prepare_bill_payload(
        patient_id=str(data.get("patient_id", "")), bill_type=str(data.get("bill_type", "service")),
        bill_date=bill_date, items=data.get("items", []),
        bill_discount=_num(str(data.get("discount", 0))), tax_rate=_num(str(data.get("tax_rate", 0))),
        deposit=_num(str(data.get("deposit", 0))), damage_charges=_num(str(data.get("damage_charges", 0))),
        loss_charges=_num(str(data.get("loss_charges", 0))), other_charges=_num(str(data.get("other_charges", 0))),
        notes=str(data.get("notes", "")), quotation_ref=str(data.get("quotation_ref", "")),
    )
    if date_err:
        errors.insert(0, date_err)
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url="/bills/new", status_code=303)
    assert payload is not None
    bill = billing_service.create_bill(payload, user=user, ip=client_ip(request))
    _mark_quotation_converted(payload.get("quotation_ref", ""), bill["bill_number"], user, client_ip(request))
    if bill_date != db.today_str():
        log_activity(user=user, action="BACKDATE_BILL", entity_type="bill", entity_id=bill["bill_id"],
                     description=f"Created back-dated bill {bill['bill_number']} for {bill_date}",
                     after={"bill_date": bill_date}, ip=client_ip(request))
    dup_of = str(form.get("duplicate_of", ""))
    if dup_of:
        orig = db.get_record("bills", dup_of)
        log_activity(user=user, action="DUPLICATE_BILL", entity_type="bill", entity_id=bill["bill_id"],
                     description=f"Duplicated bill {(orig or {}).get('bill_number', dup_of)} as {bill['bill_number']}",
                     before={"source_bill_id": dup_of}, after={"bill_number": bill["bill_number"]}, ip=client_ip(request))
    init = {}
    try:
        init = json.loads(str(form.get("initial_payment_json", "{}")))
    except Exception:
        init = {}
    if _num(str(init.get("amount", 0))) > 0:
        payment_service.add_payment(
            bill_id=bill["bill_id"], patient_id=bill["patient_id"],
            payment_date=str(init.get("date", "") or bill_date),
            method=str(init.get("method", "Cash")), amount=_num(str(init.get("amount", 0))),
            transaction_id=str(init.get("transaction_id", "")),
            notes="Payment recorded with bill creation.", user=user, ip=client_ip(request),
        )
    flash(request, f"Bill {bill['bill_number']} created successfully.")
    return RedirectResponse(url=f"/bills/{bill['bill_id']}", status_code=303)


@router.get("/{bill_id}", response_class=HTMLResponse)
def bill_detail(request: Request, bill_id: str, user: dict = Depends(require_permission("create_bill"))):
    from ..main import templates
    full = billing_service.get_bill_full(bill_id)
    if not full:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Bill not found")
    logs = db.find_records("activity_logs", entity_id=bill_id).to_dict("records")
    logs = sorted(logs, key=lambda r: str(r.get("timestamp", "")), reverse=True)
    deposits = db.find_records("deposits", bill_id=bill_id).to_dict("records")
    deposits += [d for d in db.find_records("deposits", patient_id=str(full["bill"].get("patient_id", ""))).to_dict("records")
                 if d not in deposits]
    return templates.TemplateResponse(request, "bills/detail.html", template_context(request, {
        "active_nav": "bills", **full, "logs": logs, "deposits": deposits, "today": db.today_str(),
    }))


@router.get("/{bill_id}/edit", response_class=HTMLResponse)
def bill_edit(request: Request, bill_id: str, user: dict = Depends(require_permission("edit_bill"))):
    from ..main import templates
    full = billing_service.get_bill_full(bill_id)
    if not full:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Bill not found")
    if full["bill"].get("cancelled"):
        flash(request, "Canceled bills cannot be edited.", "error")
        return RedirectResponse(url=f"/bills/{bill_id}", status_code=303)
    prefill = {
        "patient_id": full["bill"].get("patient_id", ""), "bill_type": full["bill"].get("bill_type", "service"),
        "bill_date": full["bill"].get("bill_date", ""), "bill_discount": full["bill"].get("discount", 0),
        "tax_rate": full["bill"].get("tax_rate", 0), "deposit": full["bill"].get("deposit", 0),
        "damage_charges": full["bill"].get("damage_charges", 0), "loss_charges": full["bill"].get("loss_charges", 0),
        "other_charges": full["bill"].get("other_charges", 0), "notes": full["bill"].get("notes", ""),
    }
    return templates.TemplateResponse(request, "bills/form.html", template_context(request, {
        "active_nav": "bills", "mode": "edit", "bill": full["bill"],
        "prefill_items": jsonable(full["items"]), "prefill": prefill, **_bill_form_data(user),
    }))


@router.post("/{bill_id}/edit")
async def bill_update(request: Request, bill_id: str, user: dict = Depends(require_permission("edit_bill"))):
    from ..main import templates
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/bills/{bill_id}/edit", status_code=303)
    bill_date = str(form.get("bill_date", "") or db.today_str())
    date_err = _check_bill_date(request, bill_date, user)
    payload, errors = billing_service.prepare_bill_payload(
        patient_id=str(form.get("patient_id", "")),
        bill_type=str(form.get("bill_type", "service")),
        bill_date=bill_date,
        items=_parse_items(str(form.get("items_json", "[]"))),
        bill_discount=_num(str(form.get("bill_discount", "0"))),
        tax_rate=_num(str(form.get("tax_rate", "0"))),
        deposit=_num(str(form.get("deposit", "0"))),
        damage_charges=_num(str(form.get("damage_charges", "0"))),
        loss_charges=_num(str(form.get("loss_charges", "0"))),
        other_charges=_num(str(form.get("other_charges", "0"))),
        notes=str(form.get("notes", "")),
    )
    if date_err:
        errors.insert(0, date_err)
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url=f"/bills/{bill_id}/edit", status_code=303)
    assert payload is not None
    updated, errs = billing_service.edit_bill(bill_id, payload, user=user, ip=client_ip(request))
    if errs:
        for e in errs:
            flash(request, e, "error")
        return RedirectResponse(url=f"/bills/{bill_id}/edit", status_code=303)
    flash(request, "Bill updated successfully.")
    return RedirectResponse(url=f"/bills/{bill_id}", status_code=303)


@router.post("/{bill_id}/cancel")
def bill_cancel(
    request: Request, bill_id: str,
    cancel_reason: str = Form(""),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("cancel_bill")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/bills/{bill_id}", status_code=303)
    updated, errs = billing_service.cancel_bill(bill_id, cancel_reason.strip(), user=user, ip=client_ip(request))
    if errs:
        for e in errs:
            flash(request, e, "error")
    else:
        flash(request, "Bill canceled successfully.")
    return RedirectResponse(url=f"/bills/{bill_id}", status_code=303)


@router.get("/{bill_id}/duplicate", response_class=HTMLResponse)
def bill_duplicate(request: Request, bill_id: str, user: dict = Depends(require_permission("duplicate_bill"))):
    from ..main import templates
    data = billing_service.duplicate_bill_payload(bill_id)
    if not data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Bill not found")
    prefill = {
        "patient_id": data["patient_id"], "bill_type": data["bill_type"], "bill_date": data["bill_date"],
        "bill_discount": data["bill_discount"], "tax_rate": data["tax_rate"], "deposit": data["deposit"],
        "damage_charges": 0, "loss_charges": 0, "other_charges": data["other_charges"],
        "notes": data["notes"], "duplicate_of": bill_id,
    }
    flash(request, "Duplicating bill: a new bill number will be generated on save. Payment history is not copied.")
    return templates.TemplateResponse(request, "bills/form.html", template_context(request, {
        "active_nav": "bills", "mode": "new", "bill": prefill,
        "prefill_items": data["items"], "prefill": prefill,
        "duplicate_of": bill_id, **_bill_form_data(user),
    }))


@router.post("/{bill_id}/payment")
def bill_add_payment(
    request: Request, bill_id: str,
    payment_date: str = Form(""),
    method: str = Form("Cash"),
    amount: str = Form("0"),
    transaction_id: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("create_payment")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/bills/{bill_id}", status_code=303)
    bill = db.get_record("bills", bill_id)
    if not bill:
        flash(request, "Bill not found.", "error")
        return RedirectResponse(url="/bills", status_code=303)
    payment, errors = payment_service.add_payment(
        bill_id=bill_id, patient_id=str(bill.get("patient_id", "")),
        payment_date=payment_date or db.today_str(), method=method,
        amount=_num(amount), transaction_id=transaction_id.strip(),
        notes=notes.strip(), user=user, ip=client_ip(request),
    )
    if errors:
        for e in errors:
            flash(request, e, "error")
    else:
        flash(request, "Payment recorded successfully.")
    return RedirectResponse(url=f"/bills/{bill_id}", status_code=303)


@router.get("/{bill_id}/whatsapp", response_class=HTMLResponse)
def bill_whatsapp(request: Request, bill_id: str, user: dict = Depends(require_permission("share_whatsapp"))):
    from ..main import templates
    full = billing_service.get_bill_full(bill_id)
    if not full:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Bill not found")
    bill, patient = full["bill"], full["patient"]
    company = company_profile()
    net_received = float(bill.get("received_amount", 0)) - float(bill.get("refunded_amount", 0))
    message = (
        f"*{company.get('brand_name', 'WE CARE HOME HEALTHCARE')}*\n"
        f"Bill: {bill.get('bill_number')}\n"
        f"Date: {bill.get('bill_date')}\n"
        f"Patient: {patient.get('patient_name', '')}\n"
        f"Bill Amount: Rs. {float(bill.get('grand_total', 0)):.2f}\n"
        f"Received: Rs. {net_received:.2f}\n"
        f"Pending: Rs. {float(bill.get('remaining_amount', 0)):.2f}\n"
        f"Status: {bill.get('status')}\n"
        f"Document ref: {bill.get('bill_number')} (print/PDF available at office)\n"
        f"Contact: {company.get('mobile', '')}"
    )
    link = whatsapp_link(str(patient.get("mobile", "")), message)
    log_activity(user=user, action="SHARE_WHATSAPP", entity_type="bill", entity_id=bill_id,
                 description=f"Prepared WhatsApp share for bill {bill.get('bill_number')}", ip=client_ip(request))
    return templates.TemplateResponse(request, "bills/whatsapp.html", template_context(request, {
        "active_nav": "bills", **full, "message": message, "wa_link": link,
    }))
