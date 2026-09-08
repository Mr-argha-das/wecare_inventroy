"""Application configuration loaded from environment variables."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "wecare_session")
SESSION_MAX_AGE_SECONDS = int(os.environ.get("SESSION_MAX_AGE_SECONDS", "43200"))
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")

DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DOCUMENTS_DIR = Path(os.environ.get("DOCUMENTS_DIR", str(BASE_DIR / "documents")))
PDF_DIR = DOCUMENTS_DIR / "pdf"
GENERATED_DIR = DOCUMENTS_DIR / "generated"
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(BASE_DIR / "backups")))
LOGS_DIR = Path(os.environ.get("LOGS_DIR", str(BASE_DIR / "logs")))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "app" / "static" / "uploads")))
MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "5"))

DEFAULT_ADMIN_USERNAME = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "Admin@123")

GST_DEFAULT_RATE = float(os.environ.get("GST_DEFAULT_RATE", "0"))
CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "₹")

ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

APP_NAME = "WE CARE HOME HEALTHCARE"
APP_SHORT = "WE CARE"

for _d in (DATA_DIR, PDF_DIR, GENERATED_DIR, BACKUP_DIR, LOGS_DIR, UPLOAD_DIR, DATA_DIR / "backups"):
    _d.mkdir(parents=True, exist_ok=True)
