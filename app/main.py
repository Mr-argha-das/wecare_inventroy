"""WE CARE HOME HEALTHCARE — Billing & Management Software (FastAPI entrypoint)."""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import config, database as db
from .permissions import ALL_PERMISSIONS, DEFAULT_STAFF_PERMISSIONS, dump_permissions
from .security import hash_password
from .utils import amount_in_words, format_date, format_datetime, format_inr, format_qty

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOGS_DIR / "application.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("wecare")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="We Care Home Healthcare", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie=config.SESSION_COOKIE_NAME,
    max_age=config.SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=False,
)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["inr"] = format_inr
templates.env.filters["ddmmyyyy"] = format_date
templates.env.filters["dt"] = format_datetime
templates.env.filters["words"] = amount_in_words
templates.env.filters["qty"] = format_qty
templates.env.globals["inr"] = format_inr
templates.env.globals["ddmmyyyy"] = format_date
templates.env.globals["amount_in_words"] = amount_in_words
templates.env.globals["app_name"] = config.APP_NAME


# ---------------------------------------------------------------------------
# First-run initialization
# ---------------------------------------------------------------------------
def init_defaults() -> None:
    db.ensure_all()
    # Default roles
    if db.get_record("roles", "role-admin") is None:
        db.append_record("roles", {"role_id": "role-admin", "role_name": "admin",
                                   "permissions": dump_permissions(ALL_PERMISSIONS),
                                   "updated_at": db.now_iso()})
    if db.get_record("roles", "role-staff") is None:
        db.append_record("roles", {"role_id": "role-staff", "role_name": "staff",
                                   "permissions": dump_permissions(DEFAULT_STAFF_PERMISSIONS),
                                   "updated_at": db.now_iso()})
    # Default admin account
    if db.find_records("staff", username=config.DEFAULT_ADMIN_USERNAME).empty and db.read_table("staff").empty:
        db.append_record("staff", {
            "staff_id": db.generate_staff_id(),
            "username": config.DEFAULT_ADMIN_USERNAME,
            "full_name": "Administrator",
            "password_hash": hash_password(config.DEFAULT_ADMIN_PASSWORD),
            "role": "admin",
            "permissions": dump_permissions(ALL_PERMISSIONS),
            "status": "Active",
            "created_at": db.now_iso(),
            "updated_at": db.now_iso(),
        })
        logger.info("Created default admin account '%s'", config.DEFAULT_ADMIN_USERNAME)
    # Default company settings
    defaults = {
        "brand_name": "WE CARE HOME HEALTHCARE",
        "address": "",
        "mobile": "",
        "email": "",
        "gstin": "",
        "gst_rate": str(config.GST_DEFAULT_RATE),
        "bank_details": "",
        "upi_details": "",
        "terms": ("1. Payment is due within the agreed credit period.\n"
                  "2. Equipment on rent must be returned in good condition; damage/loss charges apply.\n"
                  "3. Security deposits are refundable after adjustment of dues.\n"
                  "4. Disputes are subject to local jurisdiction."),
        "signature_name": "Authorized Signatory",
        "logo": "",
    }
    for key, val in defaults.items():
        if db.get_record("company_settings", key) is None:
            db.set_setting("company_settings", key, val)
    if db.get_record("document_settings", "template") is None:
        db.set_setting("document_settings", "template", "A")


@app.on_event("startup")
def _startup() -> None:
    init_defaults()


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from .routers import auth, bills, dashboard, documents, equipment, patients, payments, quotations, reports, services, settings, staff  # noqa: E402

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(patients.router)
app.include_router(services.router)
app.include_router(equipment.router)
app.include_router(bills.router)
app.include_router(payments.router)
app.include_router(quotations.router)
app.include_router(reports.router)
app.include_router(documents.router)
app.include_router(staff.router)
app.include_router(settings.router)


# ---------------------------------------------------------------------------
# Error handling (never leak tracebacks to users)
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    from .dependencies import template_context
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "errors/404.html", template_context(request, {"detail": exc.detail}), status_code=404)
    if exc.status_code == 403:
        return templates.TemplateResponse(request, "errors/403.html", template_context(request, {"detail": exc.detail}), status_code=403)
    logger.warning("HTTP %s on %s: %s", exc.status_code, request.url.path, exc.detail)
    return templates.TemplateResponse(request, "errors/500.html", template_context(request, {}), status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from .dependencies import template_context
    logger.exception("Unhandled error on %s", request.url.path)
    return templates.TemplateResponse(request, "errors/500.html", template_context(request, {}), status_code=500)
