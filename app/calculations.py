"""Centralized billing calculation engine.

The backend is the final source of truth for every amount. All money math
uses :class:`decimal.Decimal` and is rounded to 2 places (half-up).
"""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

TWOPLACES = Decimal("0.01")


def D(value: object) -> Decimal:
    try:
        if value in (None, ""):
            return Decimal("0")
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def money(value: object) -> float:
    return float(D(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP))


def line_total(quantity: object, rate: object, discount: object = 0) -> float:
    total = D(quantity) * D(rate) - D(discount)
    if total < 0:
        total = Decimal("0")
    return float(total.quantize(TWOPLACES, rounding=ROUND_HALF_UP))


def bill_totals(
    items: list[dict],
    *,
    bill_discount: object = 0,
    tax_rate: object = 0,
    deposit: object = 0,
    damage_charges: object = 0,
    loss_charges: object = 0,
    other_charges: object = 0,
) -> dict:
    """Compute authoritative totals for a bill.

    items: list of dicts with quantity, rate, discount.
    deposit is a refundable security deposit and is NOT part of the payable
    total; it is tracked separately (but shown on the invoice).
    """
    subtotal = Decimal("0")
    for item in items:
        subtotal += D(item.get("quantity")) * D(item.get("rate")) - D(item.get("discount", 0))
    if subtotal < 0:
        subtotal = Decimal("0")
    discount = D(bill_discount)
    if discount < 0:
        discount = Decimal("0")
    if discount > subtotal:
        discount = subtotal
    net = subtotal - discount
    rate = D(tax_rate)
    if rate < 0:
        rate = Decimal("0")
    tax = (net * rate / Decimal("100")).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    damages = D(damage_charges)
    losses = D(loss_charges)
    others = D(other_charges)
    grand = (net + tax + damages + losses + others).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

    def f(v: Decimal) -> float:
        return float(v.quantize(TWOPLACES, rounding=ROUND_HALF_UP))

    return {
        "subtotal": f(subtotal),
        "discount": f(discount),
        "tax_rate": float(rate),
        "tax": f(tax),
        "deposit": f(D(deposit)),
        "damage_charges": f(damages if damages > 0 else Decimal("0")),
        "loss_charges": f(losses if losses > 0 else Decimal("0")),
        "other_charges": f(others if others > 0 else Decimal("0")),
        "grand_total": f(grand),
    }


def payment_status(grand_total: object, received: object, refunded: object = 0, cancelled: bool = False) -> tuple[str, float, float]:
    """Return (status, net_received, remaining)."""
    if cancelled:
        return "Canceled", money(received), money(D(grand_total) - D(received) + D(refunded))
    net_received = D(received) - D(refunded)
    if net_received < 0:
        net_received = Decimal("0")
    total = D(grand_total)
    remaining = (total - net_received).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    if remaining < 0:
        remaining = Decimal("0")
    if total <= 0:
        status = "Paid"
    elif net_received <= 0:
        status = "Pending"
    elif remaining <= 0:
        status = "Paid"
    else:
        status = "Partial"
    return status, float(net_received.quantize(TWOPLACES, rounding=ROUND_HALF_UP)), float(remaining)


def rental_quantity(start: str, end: str, period: str) -> int:
    """Number of rental units between two YYYY-MM-DD dates (inclusive start)."""
    try:
        s = datetime.strptime(start, "%Y-%m-%d").date()
        e = datetime.strptime(end, "%Y-%m-%d").date()
    except Exception:
        return 0
    days = (e - s).days + 1
    if days < 1:
        return 0
    p = (period or "Daily").lower()
    if p.startswith("month"):
        return max(1, -(-days // 30))
    if p.startswith("week"):
        return max(1, -(-days // 7))
    return days


def service_quantity(start: str, end: str, billing_type: str, manual_qty: object = None) -> float:
    bt = (billing_type or "").lower()
    if bt.startswith("per day"):
        return float(rental_quantity(start, end, "Daily"))
    if manual_qty not in (None, ""):
        try:
            return float(manual_qty)
        except Exception:
            return 0.0
    return 0.0


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:10] if "%H" not in fmt else str(value), fmt).date()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return None
