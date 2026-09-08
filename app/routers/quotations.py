"""Quotation / estimate system with convert-to-bill."""
import json

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..audit import log_activity
from ..calculations import bill_totals, line_total
from ..dependencies import require_permission, template_context
from ..security import validate_csrf
from ..services import billing_service
from ..services.document_service import company_profile
from ..utils import client_ip, flash, jsonable, paginate

router = APIRouter(prefix="/quotations", tags=["quotations"])


def _form_data() -> dict:
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
    return {"patients": jsonable(patients), "services": jsonable(services),
            "equipment": jsonable(equipment), "default_tax": default_tax, "today": db.today_str()}


def _parse_items(raw: str) -> list[dict]:
    try:
        items = json.loads(raw or "[]")
    except Exception:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        try:
            qty = float(it.get("quantity", 0) or 0)
        except Exception:
            qty = 0
        try:
            rate = float(it.get("rate", 0) or 0)
        except Exception:
            rate = 0
        try:
            disc = float(it.get("discount", 0) or 0)
        except Exception:
            disc = 0
        desc = str(it.get("description", "") or "").strip()
        if not desc:
            continue
        out.append({
            "item_type": str(it.get("item_type", "service")),
            "ref_id": str(it.get("ref_id", "") or ""),
            "description": desc,
            "quantity": qty, "rate": rate, "discount": disc,
            "amount": line_total(qty, rate, disc),
        })
    return out


@router.get("", response_class=HTMLResponse)
def quotation_list(request: Request, page: int = Query(1), search: str = Query(""), status: str = Query(""),
                   user: dict = Depends(require_permission("manage_quotations"))):
    from ..main import templates
    df = db.read_table("quotations")
    if not df.empty:
        df = df.sort_values("quotation_date", ascending=False)
        if search:
            s = search.lower()
            df = df[df["quotation_number"].astype(str).str.lower().str.contains(s, na=False)
                    | df["customer_name"].astype(str).str.lower().str.contains(s, na=False)]
        if status:
            df = df[df["status"].astype(str) == status]
    total = len(df)
    pg = paginate(total, page, 20)
    rows = df.iloc[pg["start"]:pg["end"]].to_dict("records") if total else []
    return templates.TemplateResponse(request, "quotations/list.html", template_context(request, {
        "active_nav": "quotations", "rows": rows, "pg": pg, "search": search, "status": status,
    }))


@router.get("/new", response_class=HTMLResponse)
def quotation_new(request: Request, user: dict = Depends(require_permission("manage_quotations"))):
    from ..main import templates
    return templates.TemplateResponse(request, "quotations/form.html", template_context(request, {
        "active_nav": "quotations", "mode": "new", "quotation": {}, "prefill_items": [],
        "prefill": {}, **_form_data(),
    }))


def _validate(quotation_date: str, items: list[dict], discount: float) -> list[str]:
    errors = []
    if not quotation_date:
        errors.append("Quotation date is required.")
    if not items:
        errors.append("Add at least one item.")
    for i, it in enumerate(items, start=1):
        if it["quantity"] <= 0:
            errors.append(f"Item {i}: quantity must be greater than 0.")
        if it["rate"] < 0:
            errors.append(f"Item {i}: rate cannot be negative.")
        if it["discount"] < 0 or it["discount"] > it["quantity"] * it["rate"]:
            errors.append(f"Item {i}: invalid discount.")
    if discount < 0:
        errors.append("Discount cannot be negative.")
    return errors


@router.post("/new")
async def quotation_create(request: Request, user: dict = Depends(require_permission("manage_quotations"))):
    from ..main import templates
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/quotations/new", status_code=303)
    items = _parse_items(str(form.get("items_json", "[]")))
    try:
        discount = float(form.get("discount", 0) or 0)
    except Exception:
        discount = 0
    try:
        tax_rate = float(form.get("tax_rate", 0) or 0)
    except Exception:
        tax_rate = 0
    errors = _validate(str(form.get("quotation_date", "")), items, discount)
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url="/quotations/new", status_code=303)
    totals = bill_totals(items, bill_discount=discount, tax_rate=tax_rate)
    patient_id = str(form.get("patient_id", ""))
    patient = db.get_record("patients", patient_id) if patient_id else None
    customer_name = str(form.get("customer_name", "") or (patient or {}).get("patient_name", ""))
    record = {
        "quotation_id": db.new_uuid("QTN-"),
        "quotation_number": db.generate_quotation_number(),
        "quotation_date": str(form.get("quotation_date", "") or db.today_str()),
        "patient_id": patient_id,
        "customer_name": customer_name,
        "valid_until": str(form.get("valid_until", "")),
        "subtotal": totals["subtotal"], "discount": totals["discount"],
        "tax_rate": totals["tax_rate"], "tax": totals["tax"],
        "grand_total": totals["grand_total"],
        "terms": str(form.get("terms", "")),
        "items_json": json.dumps(items),
        "status": "Draft",
        "converted_bill_number": "",
        "notes": str(form.get("notes", "")),
        "created_by": str(user.get("username", "")),
        "created_at": db.now_iso(), "updated_at": db.now_iso(),
    }
    db.append_record("quotations", record)
    log_activity(user=user, action="CREATE_QUOTATION", entity_type="quotation", entity_id=record["quotation_id"],
                 description=f"Created quotation {record['quotation_number']}", after=record, ip=client_ip(request))
    flash(request, f"Quotation {record['quotation_number']} created successfully.")
    return RedirectResponse(url=f"/quotations/{record['quotation_id']}", status_code=303)


@router.get("/{quotation_id}", response_class=HTMLResponse)
def quotation_detail(request: Request, quotation_id: str, user: dict = Depends(require_permission("manage_quotations"))):
    from ..main import templates
    q = db.get_record("quotations", quotation_id)
    if not q:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Quotation not found")
    try:
        items = json.loads(q.get("items_json", "[]"))
    except Exception:
        items = []
    patient = db.get_record("patients", str(q.get("patient_id", ""))) or {}
    return templates.TemplateResponse(request, "quotations/detail.html", template_context(request, {
        "active_nav": "quotations", "quotation": q, "items": items, "patient": patient,
    }))


@router.get("/{quotation_id}/edit", response_class=HTMLResponse)
def quotation_edit(request: Request, quotation_id: str, user: dict = Depends(require_permission("manage_quotations"))):
    from ..main import templates
    q = db.get_record("quotations", quotation_id)
    if not q:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Quotation not found")
    if q.get("status") == "Converted":
        flash(request, "Converted quotations cannot be edited.", "error")
        return RedirectResponse(url=f"/quotations/{quotation_id}", status_code=303)
    try:
        items = json.loads(q.get("items_json", "[]"))
    except Exception:
        items = []
    return templates.TemplateResponse(request, "quotations/form.html", template_context(request, {
        "active_nav": "quotations", "mode": "edit", "quotation": q, "prefill_items": items,
        "prefill": q, **_form_data(),
    }))


@router.post("/{quotation_id}/edit")
async def quotation_update(request: Request, quotation_id: str, user: dict = Depends(require_permission("manage_quotations"))):
    q = db.get_record("quotations", quotation_id)
    if not q:
        flash(request, "Quotation not found.", "error")
        return RedirectResponse(url="/quotations", status_code=303)
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/quotations/{quotation_id}/edit", status_code=303)
    items = _parse_items(str(form.get("items_json", "[]")))
    try:
        discount = float(form.get("discount", 0) or 0)
    except Exception:
        discount = 0
    try:
        tax_rate = float(form.get("tax_rate", 0) or 0)
    except Exception:
        tax_rate = 0
    errors = _validate(str(form.get("quotation_date", "")), items, discount)
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url=f"/quotations/{quotation_id}/edit", status_code=303)
    totals = bill_totals(items, bill_discount=discount, tax_rate=tax_rate)
    patient_id = str(form.get("patient_id", ""))
    patient = db.get_record("patients", patient_id) if patient_id else None
    before = dict(q)
    db.update_record("quotations", quotation_id, {
        "quotation_date": str(form.get("quotation_date", "") or db.today_str()),
        "patient_id": patient_id,
        "customer_name": str(form.get("customer_name", "") or (patient or {}).get("patient_name", "")),
        "valid_until": str(form.get("valid_until", "")),
        "subtotal": totals["subtotal"], "discount": totals["discount"],
        "tax_rate": totals["tax_rate"], "tax": totals["tax"],
        "grand_total": totals["grand_total"],
        "terms": str(form.get("terms", "")), "items_json": json.dumps(items),
        "status": str(form.get("status", q.get("status", "Draft"))),
        "notes": str(form.get("notes", "")), "updated_at": db.now_iso(),
    })
    log_activity(user=user, action="EDIT_QUOTATION", entity_type="quotation", entity_id=quotation_id,
                 description=f"Edited quotation {q.get('quotation_number')}", before=before,
                 after=db.get_record("quotations", quotation_id), ip=client_ip(request))
    flash(request, "Quotation updated successfully.")
    return RedirectResponse(url=f"/quotations/{quotation_id}", status_code=303)


@router.post("/{quotation_id}/duplicate")
def quotation_duplicate(request: Request, quotation_id: str, csrf_token: str = Form(""),
                        user: dict = Depends(require_permission("manage_quotations"))):
    q = db.get_record("quotations", quotation_id)
    if not q:
        flash(request, "Quotation not found.", "error")
        return RedirectResponse(url="/quotations", status_code=303)
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/quotations/{quotation_id}", status_code=303)
    record = dict(q)
    record["quotation_id"] = db.new_uuid("QTN-")
    record["quotation_number"] = db.generate_quotation_number()
    record["quotation_date"] = db.today_str()
    record["status"] = "Draft"
    record["converted_bill_number"] = ""
    record["created_by"] = str(user.get("username", ""))
    record["created_at"] = db.now_iso()
    record["updated_at"] = db.now_iso()
    db.append_record("quotations", record)
    log_activity(user=user, action="DUPLICATE_QUOTATION", entity_type="quotation", entity_id=record["quotation_id"],
                 description=f"Duplicated quotation {q.get('quotation_number')} as {record['quotation_number']}",
                 before={"source": q.get("quotation_number")}, after={"number": record["quotation_number"]},
                 ip=client_ip(request))
    flash(request, f"Quotation duplicated as {record['quotation_number']}.")
    return RedirectResponse(url=f"/quotations/{record['quotation_id']}", status_code=303)


@router.get("/{quotation_id}/convert", response_class=HTMLResponse)
def quotation_convert_form(request: Request, quotation_id: str, user: dict = Depends(require_permission("manage_quotations"))):
    from ..main import templates
    from ..permissions import has_permission as _has
    from ..dependencies import role_default_permissions as _rdp
    if not _has(user, "create_bill", _rdp(str(user.get("role", "staff")))):
        flash(request, "You do not have permission to create bills.", "error")
        return RedirectResponse(url=f"/quotations/{quotation_id}", status_code=303)
    q = db.get_record("quotations", quotation_id)
    if not q:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Quotation not found")
    if q.get("status") == "Converted":
        flash(request, "Quotation already converted.", "error")
        return RedirectResponse(url=f"/quotations/{quotation_id}", status_code=303)
    try:
        items = json.loads(q.get("items_json", "[]"))
    except Exception:
        items = []
    # Build bill items from quotation items.
    bill_items = []
    for it in items:
        bill_items.append({
            "item_type": it.get("item_type", "service"), "ref_id": it.get("ref_id", ""),
            "description": it.get("description", ""), "serial_number": "",
            "start_date": "", "end_date": "", "billing_type": "",
            "quantity": it.get("quantity", 0), "rate": it.get("rate", 0),
            "discount": it.get("discount", 0),
        })
    has_service = any(i["item_type"] == "service" for i in bill_items)
    has_equipment = any(i["item_type"] == "equipment" for i in bill_items)
    bill_type = "combined" if (has_service and has_equipment) else ("equipment" if has_equipment else "service")
    from .bills import _bill_form_data
    prefill = {
        "patient_id": q.get("patient_id", ""), "bill_type": bill_type, "bill_date": db.today_str(),
        "bill_discount": q.get("discount", 0), "tax_rate": q.get("tax_rate", 0),
        "deposit": 0, "damage_charges": 0, "loss_charges": 0, "other_charges": 0,
        "notes": f"Converted from quotation {q.get('quotation_number')}",
        "quotation_ref": q.get("quotation_number", ""),
    }
    flash(request, f"Converting quotation {q.get('quotation_number')} to a bill. Review and save.")
    return templates.TemplateResponse(request, "bills/form.html", template_context(request, {
        "active_nav": "bills", "mode": "new", "bill": prefill, "prefill_items": bill_items,
        "prefill": prefill, "convert_quotation_id": quotation_id, **_bill_form_data(user),
    }))


@router.post("/{quotation_id}/convert")
def quotation_convert_mark(request: Request, quotation_id: str,
                           bill_number: str = Form(""), csrf_token: str = Form(""),
                           user: dict = Depends(require_permission("manage_quotations"))):
    """Mark a quotation converted (called after the bill is saved from the convert form)."""
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired.", "error")
        return RedirectResponse(url=f"/quotations/{quotation_id}", status_code=303)
    q = db.get_record("quotations", quotation_id)
    if not q:
        flash(request, "Quotation not found.", "error")
        return RedirectResponse(url="/quotations", status_code=303)
    db.update_record("quotations", quotation_id, {
        "status": "Converted", "converted_bill_number": bill_number, "updated_at": db.now_iso(),
    })
    log_activity(user=user, action="CONVERT_QUOTATION", entity_type="quotation", entity_id=quotation_id,
                 description=f"Converted quotation {q.get('quotation_number')} to bill {bill_number}",
                 before={"status": q.get("status")}, after={"status": "Converted", "bill": bill_number},
                 ip=client_ip(request))
    flash(request, f"Quotation converted to bill {bill_number}.")
    return RedirectResponse(url=f"/quotations/{quotation_id}", status_code=303)
