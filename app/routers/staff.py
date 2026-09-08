"""Staff accounts + permission management (admin)."""
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..audit import log_activity
from ..dependencies import require_permission, template_context
from ..permissions import ALL_PERMISSIONS, PERMISSION_LABELS, dump_permissions, parse_permissions
from ..security import hash_password, validate_csrf
from ..utils import client_ip, flash, paginate

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get("", response_class=HTMLResponse)
def staff_list(request: Request, page: int = Query(1), search: str = Query(""),
               user: dict = Depends(require_permission("manage_staff"))):
    from ..main import templates
    df = db.read_table("staff")
    if not df.empty:
        df = df.sort_values("username")
        if search:
            s = search.lower()
            df = df[df["username"].astype(str).str.lower().str.contains(s, na=False)
                    | df["full_name"].astype(str).str.lower().str.contains(s, na=False)]
    total = len(df)
    pg = paginate(total, page, 20)
    rows = []
    if total:
        for _, r in df.iloc[pg["start"]:pg["end"]].iterrows():
            d = r.to_dict()
            d.pop("password_hash", None)
            d["perm_list"] = parse_permissions(d.get("permissions"))
            rows.append(d)
    return templates.TemplateResponse(request, "staff/list.html", template_context(request, {
        "active_nav": "staff", "rows": rows, "pg": pg, "search": search,
    }))


@router.get("/new", response_class=HTMLResponse)
def staff_new(request: Request, user: dict = Depends(require_permission("manage_staff"))):
    from ..main import templates
    return templates.TemplateResponse(request, "staff/form.html", template_context(request, {
        "active_nav": "staff", "mode": "new", "staff": {}, "selected": [],
        "permissions": ALL_PERMISSIONS, "labels": PERMISSION_LABELS,
    }))


@router.post("/new")
async def staff_create(request: Request, user: dict = Depends(require_permission("manage_staff"))):
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/staff/new", status_code=303)
    username = str(form.get("username", "")).strip().lower()
    full_name = str(form.get("full_name", "")).strip()
    password = str(form.get("password", ""))
    role = str(form.get("role", "staff"))
    perms = [p for p in form.getlist("permissions") if p in ALL_PERMISSIONS]
    errors = []
    if not username or len(username) < 3:
        errors.append("Username must be at least 3 characters.")
    if not db.find_records("staff", username=username).empty:
        errors.append("Username already exists.")
    if not full_name:
        errors.append("Full name is required.")
    if len(password) < 6:
        errors.append("Password must be at least 6 characters.")
    if role not in ("admin", "staff"):
        errors.append("Invalid role.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url="/staff/new", status_code=303)
    record = {
        "staff_id": db.generate_staff_id(),
        "username": username,
        "full_name": full_name,
        "password_hash": hash_password(password),
        "role": role,
        "permissions": dump_permissions(perms),
        "status": "Active",
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
    }
    db.append_record("staff", record)
    safe = {k: v for k, v in record.items() if k != "password_hash"}
    log_activity(user=user, action="CREATE_STAFF", entity_type="staff", entity_id=record["staff_id"],
                 description=f"Created staff account '{username}' ({role})", after=safe, ip=client_ip(request))
    flash(request, "Staff account created successfully.")
    return RedirectResponse(url="/staff", status_code=303)


@router.get("/{staff_id}/edit", response_class=HTMLResponse)
def staff_edit(request: Request, staff_id: str, user: dict = Depends(require_permission("manage_staff"))):
    from ..main import templates
    staff = db.get_record("staff", staff_id)
    if not staff:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Staff not found")
    staff = dict(staff)
    staff.pop("password_hash", None)
    return templates.TemplateResponse(request, "staff/form.html", template_context(request, {
        "active_nav": "staff", "mode": "edit", "staff": staff,
        "selected": parse_permissions(staff.get("permissions")),
        "permissions": ALL_PERMISSIONS, "labels": PERMISSION_LABELS,
    }))


@router.post("/{staff_id}/edit")
async def staff_update(request: Request, staff_id: str, user: dict = Depends(require_permission("manage_staff"))):
    staff = db.get_record("staff", staff_id)
    if not staff:
        flash(request, "Staff not found.", "error")
        return RedirectResponse(url="/staff", status_code=303)
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/staff/{staff_id}/edit", status_code=303)
    full_name = str(form.get("full_name", "")).strip()
    password = str(form.get("password", ""))
    role = str(form.get("role", staff.get("role", "staff")))
    status = str(form.get("status", staff.get("status", "Active")))
    perms = [p for p in form.getlist("permissions") if p in ALL_PERMISSIONS]
    errors = []
    if not full_name:
        errors.append("Full name is required.")
    if password and len(password) < 6:
        errors.append("New password must be at least 6 characters.")
    if role not in ("admin", "staff"):
        errors.append("Invalid role.")
    if status not in ("Active", "Inactive"):
        errors.append("Invalid status.")
    if str(staff.get("staff_id")) == str(user.get("staff_id")) and (role != "admin" or status != "Active"):
        errors.append("You cannot demote or deactivate your own account.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url=f"/staff/{staff_id}/edit", status_code=303)
    before = {k: v for k, v in staff.items() if k != "password_hash"}
    updates: dict = {"full_name": full_name, "role": role, "status": status,
                     "permissions": dump_permissions(perms), "updated_at": db.now_iso()}
    if password:
        updates["password_hash"] = hash_password(password)
    db.update_record("staff", staff_id, updates)
    after = db.get_record("staff", staff_id) or {}
    after = {k: v for k, v in after.items() if k != "password_hash"}
    log_activity(user=user, action="CHANGE_PERMISSION", entity_type="staff", entity_id=staff_id,
                 description=f"Updated staff '{staff.get('username')}' (role/permissions/status)",
                 before=before, after=after, ip=client_ip(request))
    flash(request, "Staff account updated successfully.")
    return RedirectResponse(url="/staff", status_code=303)


@router.post("/{staff_id}/toggle")
def staff_toggle(request: Request, staff_id: str, csrf_token: str = Form(""),
                 user: dict = Depends(require_permission("manage_staff"))):
    staff = db.get_record("staff", staff_id)
    if not staff:
        flash(request, "Staff not found.", "error")
        return RedirectResponse(url="/staff", status_code=303)
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/staff", status_code=303)
    if str(staff.get("staff_id")) == str(user.get("staff_id")):
        flash(request, "You cannot deactivate your own account.", "error")
        return RedirectResponse(url="/staff", status_code=303)
    new_status = "Inactive" if str(staff.get("status")) == "Active" else "Active"
    db.update_record("staff", staff_id, {"status": new_status, "updated_at": db.now_iso()})
    log_activity(user=user, action="CHANGE_PERMISSION", entity_type="staff", entity_id=staff_id,
                 description=f"Changed staff '{staff.get('username')}' status to {new_status}",
                 before={"status": staff.get("status")}, after={"status": new_status}, ip=client_ip(request))
    flash(request, f"Staff {new_status.lower()} successfully.")
    return RedirectResponse(url="/staff", status_code=303)
