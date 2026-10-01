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

COMPANY_KEYS = ["brand_name", "address", "mobile", "email", "website", "gstin", "gst_rate",
                "brand_color", "invoice_prefix",
                "bank_name", "account_name", "account_number", "ifsc", "branch",
                "bank_details", "upi_details", "terms", "declaration", "footer_note",
                "signature_name", "logo", "signature_image", "upi_qr", "footer_image"]
# Keys that are managed by file upload, never by the text form.
IMAGE_KEYS = {"logo": "logo", "signature_image": "signature", "upi_qr": "upiqr",
              "footer_image": "footerlogo"}

# Invoice design (Settings -> Invoice Design): text + colour + on/off switches.
INVOICE_TEXT_KEYS = ["invoice_title", "header_note", "footer_text"]
INVOICE_COLOR_KEYS = ["invoice_color", "link_color"]
INVOICE_FLAG_KEYS = ["show_po_date", "show_time", "show_page_number", "show_qr", "show_signature"]


def _looks_like_image(content: bytes) -> bool:
    """Verify the upload really is a PNG / JPEG / WEBP.

    Pillow is used when installed; otherwise the file signature is checked, so
    uploading a logo / QR never depends on an optional dependency.
    """
    try:
        from PIL import Image  # type: ignore
        import io as _io
        Image.open(_io.BytesIO(content)).verify()
        return True
    except ImportError:
        pass
    except Exception:
        return False
    head = content[:12]
    return (head.startswith(b"\x89PNG\r\n\x1a\n")
            or head.startswith(b"\xff\xd8\xff")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def _valid_hex_color(value: str) -> bool:
    s = value.strip().lstrip("#")
    return len(s) in (3, 6) and all(ch in "0123456789abcdefABCDEF" for ch in s)


@router.get("", response_class=HTMLResponse)
def settings_home(request: Request, user: dict = Depends(require_permission("manage_settings"))):
    from ..main import templates
    company = db.get_all_settings("company_settings")
    backups = sorted([p.name for p in Path(BACKUP_DIR).iterdir() if p.is_dir()], reverse=True)[:20]
    bills = db.read_table("bills")
    sample_bill_id = ""
    if not bills.empty:
        sample_bill_id = str(bills.sort_values("created_at").iloc[-1]["bill_id"])
    return templates.TemplateResponse(request, "settings/home.html", template_context(request, {
        "active_nav": "settings", "company": company, "backups": backups,
        "sample_bill_id": sample_bill_id,
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
        if key in IMAGE_KEYS or key not in form:
            continue        # only fields actually present in this form are touched
        updates[key] = str(form.get(key, "") or "").strip()
    color = updates.get("brand_color", "")
    if color:
        if not _valid_hex_color(color):
            flash(request, "Brand colour must be a hex value like #174478.", "error")
            return RedirectResponse(url="/settings", status_code=303)
        updates["brand_color"] = "#" + color.strip().lstrip("#").lower()
    prefix = updates.get("invoice_prefix", "")
    if prefix:
        cleaned = "".join(ch for ch in prefix.upper() if ch.isalnum() or ch in "-_").strip("-_")
        if not cleaned or len(cleaned) > 12:
            flash(request, "Invoice prefix must be 1-12 letters/digits (e.g. WC-INV).", "error")
            return RedirectResponse(url="/settings", status_code=303)
        updates["invoice_prefix"] = cleaned
    if "gst_rate" in updates:
        try:
            rate = float(updates.get("gst_rate") or 0)
            if rate < 0 or rate > 100:
                raise ValueError
            updates["gst_rate"] = str(rate)
        except ValueError:
            flash(request, "GST rate must be between 0 and 100.", "error")
            return RedirectResponse(url="/settings", status_code=303)
    if "brand_name" in updates and not updates["brand_name"]:
        flash(request, "Brand name is required.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    for key, val in updates.items():
        db.set_setting("company_settings", key, val)
    after = db.get_all_settings("company_settings")
    log_activity(user=user, action="CHANGE_SETTINGS", entity_type="settings", entity_id="company",
                 description="Updated company/brand settings", before=before, after=after, ip=client_ip(request))
    flash(request, "Settings updated successfully.")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/invoice-design")
async def settings_invoice_design(request: Request,
                                  user: dict = Depends(require_permission("manage_settings"))):
    """Invoice heading, footer line, colours and the on/off switches."""
    form = await request.form()
    if not validate_csrf(request.session, form.get("csrf_token")):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    before = db.get_all_settings("company_settings")
    updates: dict = {}
    for key in INVOICE_TEXT_KEYS:
        updates[key] = str(form.get(key, "") or "").strip()
    for key in INVOICE_COLOR_KEYS:
        value = str(form.get(key, "") or "").strip()
        if value:
            if not _valid_hex_color(value):
                flash(request, "Colours must be hex values like #008d09.", "error")
                return RedirectResponse(url="/settings", status_code=303)
            value = "#" + value.lstrip("#").lower()
        updates[key] = value
    for key in INVOICE_FLAG_KEYS:
        updates[key] = "1" if form.get(key) else "0"
    for key, val in updates.items():
        db.set_setting("company_settings", key, val)
    log_activity(user=user, action="CHANGE_SETTINGS", entity_type="settings", entity_id="invoice_design",
                 description="Updated invoice design settings", before=before,
                 after=db.get_all_settings("company_settings"), ip=client_ip(request))
    flash(request, "Invoice design updated successfully.")
    return RedirectResponse(url="/settings", status_code=303)


LABELS = {"logo": "Logo", "signature_image": "Signature image", "upi_qr": "UPI QR code",
          "footer_image": "Footer image"}


async def _save_brand_image(request: Request, user: dict, key: str, upload: UploadFile | None):
    """Shared validation + storage for logo / signature / UPI QR uploads."""
    if key not in IMAGE_KEYS:
        flash(request, "Unknown image type.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    label = LABELS.get(key, key)
    if upload is None or not upload.filename:
        flash(request, f"Please choose a {label.lower()} file.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    ext = Path(upload.filename).suffix.lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        flash(request, f"{label} must be PNG, JPG, JPEG or WEBP.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    content = await upload.read()
    if len(content) == 0:
        flash(request, "Empty file.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        flash(request, f"{label} must be smaller than {MAX_UPLOAD_MB} MB.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    if not _looks_like_image(content):
        flash(request, "Invalid image file.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    stem = IMAGE_KEYS[key]
    filename = stem + ext
    for old in Path(UPLOAD_DIR).glob(stem + ".*"):
        try:
            old.unlink()
        except OSError:
            pass
    (Path(UPLOAD_DIR) / filename).write_bytes(content)
    before = db.get_setting("company_settings", key, "")
    db.set_setting("company_settings", key, filename)
    log_activity(user=user, action="CHANGE_SETTINGS", entity_type="settings", entity_id=key,
                 description=f"Uploaded new company {label.lower()}", before={key: before},
                 after={key: filename}, ip=client_ip(request))
    flash(request, f"{label} uploaded successfully.")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/logo")
async def settings_logo(request: Request, logo: UploadFile | None = File(None),
                        csrf_token: str = Form(""), user: dict = Depends(require_permission("manage_settings"))):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    return await _save_brand_image(request, user, "logo", logo)


@router.post("/image/{key}")
async def settings_image(request: Request, key: str, image: UploadFile | None = File(None),
                         csrf_token: str = Form(""), user: dict = Depends(require_permission("manage_settings"))):
    """Upload signature image / UPI QR (and logo) used on invoices and PDFs."""
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    return await _save_brand_image(request, user, key, image)


@router.post("/image/{key}/remove")
async def settings_image_remove(request: Request, key: str, csrf_token: str = Form(""),
                                user: dict = Depends(require_permission("manage_settings"))):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    if key not in IMAGE_KEYS:
        flash(request, "Unknown image type.", "error")
        return RedirectResponse(url="/settings", status_code=303)
    before = db.get_setting("company_settings", key, "")
    for old in Path(UPLOAD_DIR).glob(IMAGE_KEYS[key] + ".*"):
        try:
            old.unlink()
        except OSError:
            pass
    db.set_setting("company_settings", key, "")
    log_activity(user=user, action="CHANGE_SETTINGS", entity_type="settings", entity_id=key,
                 description=f"Removed company {LABELS.get(key, key).lower()}", before={key: before},
                 after={key: ""}, ip=client_ip(request))
    flash(request, f"{LABELS.get(key, key)} removed.")
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
