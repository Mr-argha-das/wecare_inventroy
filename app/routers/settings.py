"""Company / billing / GST / document / backup settings (admin)."""
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..audit import log_activity
from ..config import ALLOWED_LOGO_EXTENSIONS, BACKUP_DIR, DATA_DIR, MAX_UPLOAD_MB, TIMEZONE, UPLOAD_DIR
from ..dependencies import require_permission, template_context
from ..security import validate_csrf
from ..utils import client_ip, flash, safe_filename

router = APIRouter(prefix="/settings", tags=["settings"])

COMPANY_KEYS = ["brand_name", "address", "mobile", "email", "gstin", "gst_rate",
                "bank_details", "upi_details", "terms", "signature_name", "logo"]
DOC_KEYS = ["template"]


@router.get("", response_class=HTMLResponse)
def settings_home(request: Request, user: dict = Depends(require_permission("manage_settings"))):
    from ..main import templates
    company = db.get_all_settings("company_settings")
    docs = db.get_all_settings("document_settings")
    backups = sorted([p.name for p in Path(BACKUP_DIR).iterdir() if p.is_dir()], reverse=True)[:20]
    return templates.TemplateResponse(request, "settings/home.html", template_context(request, {
        "active_nav": "settings", "company": company, "docs": docs, "backups": backups,
    }))


@router.post("/company")
async def settings_company(request: Request, user: dict = Depends(require_permission("manage_settings"))):
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    before = db.get_all_settings("company_settings")
    updates: dict = {}
    for key in COMPANY_KEYS:
        if key == "logo":
            continue
        updates[key] = str(form.get(key, "") or "").strip()
    try:
        rate = float(updates.get("gst_rate") or 0)
        if rate < 0 or rate > 100:
            raise ValueError
        updates["gst_rate"] = str(rate)
    except ValueError:
        flash(request, "GST rate must be between 0 and 100.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    if not updates.get("brand_name"):
        flash(request, "Brand name is required.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    for key, val in updates.items():
        db.set_setting("company_settings", key, val)
    after = db.get_all_settings("company_settings")
    log_activity(user=user, action="CHANGE_SETTINGS", entity_type="settings", entity_id="company",
                 description="Updated company/brand settings", before=before, after=after, ip=client_ip(request))
    flash(request, "Settings updated successfully.")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/documents")
async def settings_documents(request: Request, user: dict = Depends(require_permission("manage_settings"))):
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    template = str(form.get("template", "A")).upper()
    if template not in ("A", "B", "C"):
        template = "A"
    before = db.get_all_settings("document_settings")
    db.set_setting("document_settings", "template", template)
    log_activity(user=user, action="CHANGE_SETTINGS", entity_type="settings", entity_id="documents",
                 description=f"Changed invoice template to Template {template}",
                 before=before, after=db.get_all_settings("document_settings"), ip=client_ip(request))
    flash(request, "Document template updated successfully.")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/logo")
async def settings_logo(request: Request, logo: UploadFile | None = File(None),
                        csrf_token: str = Form(""), user: dict = Depends(require_permission("manage_settings"))):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    if logo is None or not logo.filename:
        flash(request, "Please choose a logo file.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    ext = Path(logo.filename).suffix.lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        flash(request, "Logo must be PNG, JPG, JPEG or WEBP.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    content = await logo.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        flash(request, f"Logo must be smaller than {MAX_UPLOAD_MB} MB.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    if len(content) == 0:
        flash(request, "Empty file.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    # Validate it is a real image.
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(content))
        img.verify()
    except Exception:
        flash(request, "Invalid image file.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    filename = "logo" + ext
    dest = Path(UPLOAD_DIR) / filename
    # Remove older logo variants.
    for old in Path(UPLOAD_DIR).glob("logo.*"):
        try:
            old.unlink()
        except OSError:
            pass
    dest.write_bytes(content)
    before = db.get_setting("company_settings", "logo", "")
    db.set_setting("company_settings", "logo", filename)
    log_activity(user=user, action="CHANGE_SETTINGS", entity_type="settings", entity_id="logo",
                 description="Uploaded new company logo", before={"logo": before},
                 after={"logo": filename}, ip=client_ip(request))
    flash(request, "Logo uploaded successfully.")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/backup")
def settings_backup(request: Request, csrf_token: str = Form(""),
                    user: dict = Depends(require_permission("manage_settings"))):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    stamp = datetime.now(ZoneInfo(TIMEZONE)).strftime("backup_%Y_%m_%d_%H%M%S")
    dest = Path(BACKUP_DIR) / stamp
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for feather in Path(DATA_DIR).glob("*.feather"):
            shutil.copy2(feather, dest / feather.name)
        uploads_src = Path(UPLOAD_DIR)
        if uploads_src.exists():
            shutil.copytree(uploads_src, dest / "uploads", dirs_exist_ok=True)
    except Exception as exc:
        flash(request, f"Backup failed: {exc}", "error")
        return RedirectResponse(url="/settings", status_code=303)
    log_activity(user=user, action="BACKUP", entity_type="backup", entity_id=stamp,
                 description=f"Created backup {stamp}", ip=client_ip(request))
    flash(request, f"Backup {stamp} created successfully.")
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    from ..main import templates
    from ..dependencies import get_current_user
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    return templates.TemplateResponse(request, "settings/profile.html", template_context(request, {
        "active_nav": "", "profile": user,
    }))


@router.post("/profile/password")
def profile_password(request: Request, current_password: str = Form(""), new_password: str = Form(""),
                     confirm_password: str = Form(""), csrf_token: str = Form("")):
    from ..main import templates  # noqa
    from ..dependencies import get_current_user
    from ..security import hash_password, verify_password
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings/profile", status_code=303)
    if not verify_password(current_password, str(user.get("password_hash", ""))):
        flash(request, "Current password is incorrect.", "error")
        return RedirectResponse(url="/settings/profile", status_code=303)
    if len(new_password) < 6:
        flash(request, "New password must be at least 6 characters.", "error")
        return RedirectResponse(url="/settings/profile", status_code=303)
    if new_password != confirm_password:
        flash(request, "New passwords do not match.", "error")
        return RedirectResponse(url="/settings/profile", status_code=303)
    db.update_record("staff", str(user["staff_id"]), {"password_hash": hash_password(new_password), "updated_at": db.now_iso()})
    log_activity(user=user, action="CHANGE_PASSWORD", entity_type="staff", entity_id=str(user["staff_id"]),
                 description=f"User '{user.get('username')}' changed their password.", ip=client_ip(request))
    flash(request, "Password changed successfully.")
    return RedirectResponse(url="/settings/profile", status_code=303)
