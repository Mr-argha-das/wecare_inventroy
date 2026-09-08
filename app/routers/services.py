"""Service master management."""
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..audit import log_activity
from ..dependencies import require_permission, template_context
from ..security import validate_csrf
from ..utils import client_ip, flash, paginate

router = APIRouter(prefix="/services", tags=["services"])

BILLING_TYPES = ["Per Day", "Per Visit", "Per Hour"]


@router.get("", response_class=HTMLResponse)
def service_list(
    request: Request, page: int = Query(1), search: str = Query(""), status: str = Query(""),
    user: dict = Depends(require_permission("view_services")),
):
    from ..main import templates
    df = db.read_table("services")
    if not df.empty:
        df = df.sort_values("service_name")
        if search:
            s = search.lower()
            df = df[df["service_name"].astype(str).str.lower().str.contains(s, na=False)
                    | df["service_id"].astype(str).str.lower().str.contains(s, na=False)]
        if status:
            df = df[df["status"].astype(str) == status]
    total = len(df)
    pg = paginate(total, page, 20)
    rows = df.iloc[pg["start"]:pg["end"]].to_dict("records") if total else []
    return templates.TemplateResponse(request, "services/list.html", template_context(request, {
        "active_nav": "services", "rows": rows, "pg": pg, "search": search, "status": status,
    }))


@router.get("/new", response_class=HTMLResponse)
def service_new(request: Request, user: dict = Depends(require_permission("manage_services"))):
    from ..main import templates
    return templates.TemplateResponse(request, "services/form.html", template_context(request, {
        "active_nav": "services", "mode": "new", "service": {}, "billing_types": BILLING_TYPES,
    }))


@router.post("/new")
def service_create(
    request: Request,
    service_name: str = Form(""),
    description: str = Form(""),
    default_rate: str = Form("0"),
    billing_type: str = Form("Per Day"),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("manage_services")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/services/new", status_code=303)
    service_name = service_name.strip()
    try:
        rate = float(default_rate or 0)
    except Exception:
        rate = -1
    errors = []
    if not service_name:
        errors.append("Service name is required.")
    if rate < 0:
        errors.append("Default rate cannot be negative.")
    if billing_type not in BILLING_TYPES:
        errors.append("Invalid billing type.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url="/services/new", status_code=303)
    record = {
        "service_id": db.generate_service_id(),
        "service_name": service_name,
        "description": description.strip(),
        "default_rate": rate,
        "billing_type": billing_type,
        "status": "Active",
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
    }
    db.append_record("services", record)
    log_activity(user=user, action="CREATE_SERVICE", entity_type="service", entity_id=record["service_id"],
                 description=f"Created service {service_name}", after=record, ip=client_ip(request))
    flash(request, "Service created successfully.")
    return RedirectResponse(url="/services", status_code=303)


@router.get("/{service_id}/edit", response_class=HTMLResponse)
def service_edit(request: Request, service_id: str, user: dict = Depends(require_permission("manage_services"))):
    from ..main import templates
    service = db.get_record("services", service_id)
    if not service:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Service not found")
    return templates.TemplateResponse(request, "services/form.html", template_context(request, {
        "active_nav": "services", "mode": "edit", "service": service, "billing_types": BILLING_TYPES,
    }))


@router.post("/{service_id}/edit")
def service_update(
    request: Request, service_id: str,
    service_name: str = Form(""),
    description: str = Form(""),
    default_rate: str = Form("0"),
    billing_type: str = Form("Per Day"),
    status: str = Form("Active"),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("manage_services")),
):
    service = db.get_record("services", service_id)
    if not service:
        flash(request, "Service not found.", "error")
        return RedirectResponse(url="/services", status_code=303)
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/services/{service_id}/edit", status_code=303)
    service_name = service_name.strip()
    try:
        rate = float(default_rate or 0)
    except Exception:
        rate = -1
    errors = []
    if not service_name:
        errors.append("Service name is required.")
    if rate < 0:
        errors.append("Default rate cannot be negative.")
    if billing_type not in BILLING_TYPES:
        errors.append("Invalid billing type.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url=f"/services/{service_id}/edit", status_code=303)
    before = dict(service)
    db.update_record("services", service_id, {
        "service_name": service_name, "description": description.strip(),
        "default_rate": rate, "billing_type": billing_type,
        "status": status if status in ("Active", "Inactive") else "Active",
        "updated_at": db.now_iso(),
    })
    log_activity(user=user, action="EDIT_SERVICE", entity_type="service", entity_id=service_id,
                 description=f"Edited service {service_name}", before=before,
                 after=db.get_record("services", service_id), ip=client_ip(request))
    flash(request, "Service updated successfully.")
    return RedirectResponse(url="/services", status_code=303)


@router.post("/{service_id}/toggle")
def service_toggle(request: Request, service_id: str, csrf_token: str = Form(""),
                   user: dict = Depends(require_permission("manage_services"))):
    service = db.get_record("services", service_id)
    if not service:
        flash(request, "Service not found.", "error")
        return RedirectResponse(url="/services", status_code=303)
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/services", status_code=303)
    new_status = "Inactive" if str(service.get("status")) == "Active" else "Active"
    db.update_record("services", service_id, {"status": new_status, "updated_at": db.now_iso()})
    log_activity(user=user, action="EDIT_SERVICE", entity_type="service", entity_id=service_id,
                 description=f"Changed service {service.get('service_name')} status to {new_status}",
                 before={"status": service.get("status")}, after={"status": new_status}, ip=client_ip(request))
    flash(request, f"Service {new_status.lower()} successfully.")
    return RedirectResponse(url="/services", status_code=303)
