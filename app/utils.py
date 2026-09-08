"""Shared helpers: formatting, validation, pagination, flash messages."""
import re
from datetime import datetime
from urllib.parse import quote

from .calculations import parse_date
from .config import CURRENCY_SYMBOL

MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def format_inr(amount: object) -> str:
    try:
        value = float(amount or 0)
    except Exception:
        value = 0.0
    neg = value < 0
    value = abs(value)
    s = f"{value:,.2f}"
    # Indian digit grouping: 1,00,000.00
    head, _, tail = s.partition(".")
    head = head.replace(",", "")
    if len(head) > 3:
        last3 = head[-3:]
        rest = head[:-3]
        groups = []
        while rest:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        head = ",".join(groups) + "," + last3
    return f"{'-' if neg else ''}{CURRENCY_SYMBOL}{head}.{tail}"


def format_date(value: object) -> str:
    if not value:
        return "-"
    d = parse_date(str(value))
    if not d:
        return str(value)
    return d.strftime("%d/%m/%Y")


def format_datetime(value: object) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.strftime("%d/%m/%Y %I:%M %p")
    except Exception:
        d = parse_date(str(value))
        return d.strftime("%d/%m/%Y") if d else str(value)


def valid_mobile(mobile: str) -> bool:
    digits = re.sub(r"\D", "", str(mobile or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return bool(MOBILE_RE.match(digits))


def normalize_mobile(mobile: str) -> str:
    digits = re.sub(r"\D", "", str(mobile or ""))
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


def paginate(total: int, page: int, per_page: int) -> dict:
    per_page = max(1, min(100, per_page or 20))
    pages = max(1, -(-max(0, total) // per_page))
    page = max(1, min(page or 1, pages))
    return {
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "total": total,
        "start": (page - 1) * per_page,
        "end": min(total, page * per_page),
        "has_prev": page > 1,
        "has_next": page < pages,
        "page_numbers": list(range(max(1, page - 2), min(pages, page + 2) + 1)),
    }


def flash(request, message: str, category: str = "success") -> None:
    request.session.setdefault("_flashes", []).append({"message": message, "category": category})


def pop_flashes(request) -> list[dict]:
    flashes = request.session.pop("_flashes", [])
    return flashes if isinstance(flashes, list) else []


def whatsapp_link(phone: str, message: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}?text={quote(message)}"


def safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "file")).strip("._") or "file"
    return base[:120]


def jsonable(obj):
    """Convert pandas/numpy scalars in nested structures to plain Python types."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    try:
        import numpy as _np
        if isinstance(obj, _np.integer):
            return int(obj)
        if isinstance(obj, _np.floating):
            return float(obj)
        if isinstance(obj, _np.bool_):
            return bool(obj)
        if isinstance(obj, _np.ndarray):
            return [jsonable(v) for v in obj.tolist()]
    except ImportError:
        pass
    try:
        import pandas as _pd
        if isinstance(obj, _pd.Timestamp):
            return obj.isoformat()
        if obj is getattr(_pd, "NaT", None):
            return ""
    except ImportError:
        pass
    if isinstance(obj, float) and obj != obj:  # NaN
        return 0.0
    return obj


def client_ip(request) -> str:
    try:
        if request.client:
            return request.client.host
    except Exception:
        pass
    return ""
