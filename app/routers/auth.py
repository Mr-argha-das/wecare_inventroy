"""Login / logout."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..audit import log_activity
from ..dependencies import get_current_user, template_context
from ..security import SESSION_USER_KEY, get_csrf_token, validate_csrf, verify_password
from ..utils import client_ip, flash

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    from ..main import templates
    if get_current_user(request):
        return RedirectResponse(url=next if next.startswith("/") else "/", status_code=303)
    return templates.TemplateResponse(request, "login.html", template_context(request, {"next": next}))


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/auth/login", status_code=303)
    username = username.strip()
    users = db.find_records("staff", username=username)
    user = None
    if not users.empty:
        candidate = users.iloc[0].to_dict()
        if str(candidate.get("status", "")).lower() == "active" and verify_password(password, str(candidate.get("password_hash", ""))):
            user = candidate
    if not user:
        log_activity(user={"username": username}, action="LOGIN_FAILED", entity_type="staff",
                     entity_id=username, description=f"Failed login attempt for '{username}'", ip=client_ip(request))
        flash(request, "Invalid username or password.", "error")
        return RedirectResponse(url="/auth/login", status_code=303)
    request.session[SESSION_USER_KEY] = str(user["staff_id"])
    get_csrf_token(request.session)
    log_activity(user=user, action="LOGIN", entity_type="staff", entity_id=str(user["staff_id"]),
                 description=f"User '{username}' logged in.", ip=client_ip(request))
    dest = next if next.startswith("/") else "/"
    flash(request, f"Welcome, {user.get('full_name') or username}!")
    return RedirectResponse(url=dest, status_code=303)


@router.get("/logout")
@router.post("/logout")
def logout(request: Request):
    user = get_current_user(request)
    if user:
        log_activity(user=user, action="LOGOUT", entity_type="staff", entity_id=str(user["staff_id"]),
                     description=f"User '{user.get('username')}' logged out.", ip=client_ip(request))
    request.session.clear()
    resp = RedirectResponse(url="/auth/login", status_code=303)
    return resp
