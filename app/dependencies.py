"""FastAPI dependencies: current user, permission guards, template context."""
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import database as db
from .permissions import effective_permissions, has_permission, parse_permissions
from .security import SESSION_USER_KEY, get_csrf_token
from .utils import pop_flashes


def get_current_user(request: Request) -> dict | None:
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    user = db.get_record("staff", str(user_id))
    if not user or str(user.get("status", "")).lower() != "active":
        request.session.pop(SESSION_USER_KEY, None)
        return None
    return user


def role_default_permissions(role: str) -> list[str]:
    from .permissions import DEFAULT_STAFF_PERMISSIONS, ALL_PERMISSIONS
    if str(role).lower() == "admin":
        return list(ALL_PERMISSIONS)
    rec = db.get_record("roles", f"role-{str(role).lower()}")
    if rec:
        perms = parse_permissions(rec.get("permissions"))
        if perms:
            return perms
    return list(DEFAULT_STAFF_PERMISSIONS)


def user_permissions(user: dict | None) -> set[str]:
    if not user:
        return set()
    return effective_permissions(user, role_default_permissions(str(user.get("role", "staff"))))


def require_login(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login?next=" + request.url.path})
    return user


def require_permission(permission: str):
    def guard(request: Request) -> dict:
        user = require_login(request)
        if not has_permission(user, permission, role_default_permissions(str(user.get("role", "staff")))):
            raise HTTPException(status_code=403, detail="You do not have permission to access this page.")
        return user

    return guard


def template_context(request: Request, extra: dict | None = None) -> dict:
    user = get_current_user(request)
    perms = user_permissions(user) if user else set()
    ctx = {
        "request": request,
        "current_user": user,
        "permissions": perms,
        "can": lambda p: p in perms,
        "flashes": pop_flashes(request),
        "csrf_token": get_csrf_token(request.session),
        "active_nav": "",
    }
    if extra:
        ctx.update(extra)
    return ctx


def redirect_with_message(url: str, message: str, category: str = "success", request: Request | None = None) -> RedirectResponse:
    if request is not None:
        from .utils import flash
        flash(request, message, category)
    return RedirectResponse(url=url, status_code=303)
