"""Centralized billing calculation engine.

The backend is the final source of truth for every billing amount.

All monetary calculations use Decimal and are rounded to 2 decimal places
using ROUND_HALF_UP.

GST:
- GST can be enabled/disabled at bill level.
- When GST is disabled, GST amount is zero.
- When GST type is CGST_SGST, GST is split equally between CGST and SGST.
- When GST type is IGST, the complete GST amount is treated as IGST.
- Security deposit is refundable and is NOT included in grand_total.
"""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP


TWOPLACES = Decimal("0.01")


# ---------------------------------------------------------------------------
# BASIC MONEY HELPERS
# ---------------------------------------------------------------------------
def parse_date(value):
    """Parse a date value into a date object."""
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def rental_quantity(start_date, end_date, billing_type):
    """Calculate rental/service quantity from start and end dates."""

    start = parse_date(start_date)
    end = parse_date(end_date)

    if not start or not end or end < start:
        return 0

    days = (end - start).days + 1

    billing = str(billing_type or "").strip().lower()

    if billing in ("daily", "per day"):
        return days

    if billing == "weekly":
        return (days + 6) // 7

    if billing == "monthly":
        return (days + 29) // 30

    return days


def D(value: object) -> Decimal:
    """Safely convert a value to Decimal."""
    try:
        if value in (None, ""):
            return Decimal("0")

        return Decimal(str(value))

    except Exception:
        return Decimal("0")


def money(value: object) -> float:
    """Convert a value to a 2-decimal-place float."""
    return float(
        D(value).quantize(
            TWOPLACES,
            rounding=ROUND_HALF_UP,
        )
    )


def _non_negative(value: object) -> Decimal:
    """Return a non-negative Decimal."""
    value = D(value)

    if value < 0:
        return Decimal("0")

    return value


def _rounded(value: object) -> Decimal:
    """Round Decimal value to 2 decimal places."""
    return D(value).quantize(
        TWOPLACES,
        rounding=ROUND_HALF_UP,
    )


# ---------------------------------------------------------------------------
# UNIT BASIS (QTY / DAYS)
# ---------------------------------------------------------------------------

UNIT_QTY = "qty"
UNIT_DAYS = "days"
UNIT_BOTH = "both"

_QTY_ALIASES = {
    "qty", "quantity", "unit", "units", "nos", "no", "piece", "pieces", "fixed",
}

_DAYS_ALIASES = {
    "day", "days", "daily", "per day", "per_day", "perday",
    "duration", "rental", "date", "dates",
}

_BOTH_ALIASES = {
    "both", "qty_days", "qty*days", "qty x days", "qtydays", "multiply",
}


def normalize_unit_type(value: object):
    """Dropdown value ko qty / days / both me convert karta hai."""
    if value in (None, ""):
        return None

    text = str(value).strip().lower().replace("-", "_")

    if text in _QTY_ALIASES:
        return UNIT_QTY

    if text in _DAYS_ALIASES:
        return UNIT_DAYS

    if text in _BOTH_ALIASES:
        return UNIT_BOTH

    return None


def resolve_units(item: dict) -> dict:
    """Item ke liye billable units nikalta hai (qty ya days ke hisaab se).

    Agar unit_type saaf-saaf diya gaya hai (qty / days / both), to sirf wahi
    field use hoti hai. Doosri field se chupchap fallback NAHI hota, taaki
    galat / khaali value par validation error aaye, galat bill na bane.

    Fallback sirf tab hota hai jab unit_type diya hi nahi gaya (purana data).
    """

    item = item or {}

    qty_raw = item.get("quantity", item.get("qty"))

    days_raw = item.get(
        "days",
        item.get("no_of_days", item.get("num_days")),
    )

    # Days manually nahi bhara -> dates se nikal lo
    if days_raw in (None, ""):
        derived = rental_quantity(
            item.get("start_date"),
            item.get("end_date"),
            item.get("billing_type") or "daily",
        )

        if derived:
            days_raw = derived

    quantity = _non_negative(qty_raw)
    days = _non_negative(days_raw)

    unit_type = normalize_unit_type(
        item.get("unit_type")
        or item.get("basis")
        or item.get("charge_basis")
    )

    explicit = unit_type is not None

    # Frontend ne basis nahi bheja -> khud samajh lo
    if unit_type is None:
        if days > 0 and quantity <= 0:
            unit_type = UNIT_DAYS
        else:
            unit_type = UNIT_QTY

    if unit_type == UNIT_DAYS:
        units = days
        if not explicit and units <= 0 and quantity > 0:
            units = quantity

    elif unit_type == UNIT_BOTH:
        q = quantity if quantity > 0 else Decimal("1")
        d = days if days > 0 else Decimal("1")
        units = q * d

    else:
        units = quantity
        if not explicit and units <= 0 and days > 0:
            units = days

    return {
        "unit_type": unit_type,
        "quantity": quantity,
        "days": days,
        "units": _non_negative(units),
    }


def validate_item_units(item: dict):
    """Selected basis (Qty ya Days) ki value 0 se badi honi chahiye."""
    resolved = resolve_units(item)

    if resolved["units"] <= 0:
        return "Either Qty or Days is required."

    return None


# ---------------------------------------------------------------------------
# LINE ITEM CALCULATION
# ---------------------------------------------------------------------------

def line_total(
    quantity: object = 0,
    rate: object = 0,
    discount: object = 0,
    *,
    days: object = None,
    unit_type: object = None,
) -> float:
    """Ek invoice line ka total.

    units x rate - discount

    units = quantity   (unit_type = "qty")
          = days       (unit_type = "days")
          = qty * days (unit_type = "both")
    """

    resolved = resolve_units(
        {
            "quantity": quantity,
            "days": days,
            "unit_type": unit_type,
        }
    )

    units = resolved["units"]
    item_rate = _non_negative(rate)
    item_discount = _non_negative(discount)

    total = (units * item_rate) - item_discount

    if total < 0:
        total = Decimal("0")

    return float(total.quantize(TWOPLACES, rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# BILL TOTAL CALCULATION
# ---------------------------------------------------------------------------

def bill_totals(
    items: list[dict],
    *,
    bill_discount: object = 0,

    # New GST fields
    gst_applicable: object = False,
    gst_type: object = "CGST_SGST",
    gst_rate: object = 0,

    # Old field kept for backward compatibility
    tax_rate: object = 0,

    # Other charges
    deposit: object = 0,
    damage_charges: object = 0,
    loss_charges: object = 0,
    other_charges: object = 0,
) -> dict:
    """Calculate authoritative totals for a bill.

    Parameters
    ----------
    items:
        List of billing items. Each item should contain:
            quantity / days / unit_type
            rate
            discount

    bill_discount:
        Overall bill-level discount.

    gst_applicable:
        Whether GST should be applied.
        Accepted: True / False, "true" / "false", "yes" / "no", 1 / 0

    gst_type:
        "CGST_SGST" or "IGST"

    gst_rate:
        GST percentage.

    tax_rate:
        Legacy field retained so old callers do not immediately break.
        If gst_rate is zero and tax_rate is supplied, tax_rate will be used.

    deposit:
        Refundable security deposit.
        IMPORTANT: tracked separately and NOT included in grand_total.

    damage_charges / loss_charges / other_charges:
        Extra billable charges.

    Returns
    -------
    dict
        Authoritative billing totals.
    """

    # -----------------------------------------------------------------------
    # 1. SUBTOTAL
    # -----------------------------------------------------------------------

    subtotal = Decimal("0")

    for item in items or []:
        resolved = resolve_units(item)

        units = resolved["units"]
        rate = _non_negative(item.get("rate", 0))
        item_discount = _non_negative(item.get("discount", 0))

        item_amount = (units * rate) - item_discount

        if item_amount < 0:
            item_amount = Decimal("0")

        subtotal += _rounded(item_amount)

    subtotal = _rounded(subtotal)

    # -----------------------------------------------------------------------
    # 2. BILL DISCOUNT
    # -----------------------------------------------------------------------

    discount = _non_negative(bill_discount)

    # Discount cannot be greater than subtotal.
    if discount > subtotal:
        discount = subtotal

    discount = _rounded(discount)

    # -----------------------------------------------------------------------
    # 3. TAXABLE AMOUNT
    # -----------------------------------------------------------------------

    taxable_amount = subtotal - discount

    if taxable_amount < 0:
        taxable_amount = Decimal("0")

    taxable_amount = _rounded(taxable_amount)

    # -----------------------------------------------------------------------
    # 4. GST APPLICABILITY
    # -----------------------------------------------------------------------

    if isinstance(gst_applicable, str):
        gst_enabled = gst_applicable.strip().lower() in {
            "yes",
            "true",
            "1",
            "y",
        }
    else:
        gst_enabled = bool(gst_applicable)

    # -----------------------------------------------------------------------
    # 5. GST TYPE
    # -----------------------------------------------------------------------

    gst_type_normalized = str(
        gst_type or "CGST_SGST"
    ).strip().upper()

    if gst_type_normalized in {
        "CGST+SGST",
        "CGST + SGST",
        "CGST/SGST",
        "CGST_SGST",
    }:
        gst_type_normalized = "CGST_SGST"

    elif gst_type_normalized == "IGST":
        gst_type_normalized = "IGST"

    else:
        # Safe default
        gst_type_normalized = "CGST_SGST"

    # -----------------------------------------------------------------------
    # 6. GST RATE
    # -----------------------------------------------------------------------

    current_gst_rate = _non_negative(gst_rate)

    # Backward compatibility: gst_rate na mile to purana tax_rate use karo.
    if current_gst_rate == 0:
        legacy_tax_rate = _non_negative(tax_rate)

        if legacy_tax_rate > 0:
            current_gst_rate = legacy_tax_rate

    # GST percentage 100% se upar nahi ja sakta.
    if current_gst_rate > Decimal("100"):
        current_gst_rate = Decimal("100")

    current_gst_rate = _rounded(current_gst_rate)

    # If GST is not applicable, force GST rate to zero.
    if not gst_enabled:
        current_gst_rate = Decimal("0")

    # -----------------------------------------------------------------------
    # 7. GST CALCULATION
    # -----------------------------------------------------------------------

    cgst = Decimal("0")
    sgst = Decimal("0")
    igst = Decimal("0")

    total_gst = Decimal("0")

    if gst_enabled and current_gst_rate > 0:

        total_gst = _rounded(
            taxable_amount * current_gst_rate / Decimal("100")
        )

        if gst_type_normalized == "IGST":

            # Entire GST goes to IGST.
            igst = total_gst

        else:

            # Split GST equally. Odd paise SGST me jaata hai, taaki
            # CGST + SGST == Total GST hamesha rahe.
            cgst = _rounded(total_gst / Decimal("2"))
            sgst = _rounded(total_gst - cgst)

        total_gst = _rounded(cgst + sgst + igst)

    # -----------------------------------------------------------------------
    # 8. OTHER CHARGES
    # -----------------------------------------------------------------------

    damages = _rounded(_non_negative(damage_charges))
    losses = _rounded(_non_negative(loss_charges))
    others = _rounded(_non_negative(other_charges))

    # -----------------------------------------------------------------------
    # 9. SECURITY DEPOSIT
    # -----------------------------------------------------------------------

    security_deposit = _rounded(_non_negative(deposit))

    # IMPORTANT:
    # Security deposit is refundable and therefore NOT included
    # in grand_total.
    #
    # Grand Total = taxable amount + GST + damage + loss + other charges

    # -----------------------------------------------------------------------
    # 10. GRAND TOTAL
    # -----------------------------------------------------------------------

    grand_total = _rounded(
        taxable_amount
        + total_gst
        + damages
        + losses
        + others
    )

    # -----------------------------------------------------------------------
    # 11. RETURN ALL CALCULATED VALUES
    # -----------------------------------------------------------------------

    return {
        # Basic totals
        "subtotal": money(subtotal),
        "discount": money(discount),
        "taxable_amount": money(taxable_amount),

        # GST
        "gst_applicable": gst_enabled,
        "gst_type": gst_type_normalized,
        "gst_rate": money(current_gst_rate),

        "cgst": money(cgst),
        "sgst": money(sgst),
        "igst": money(igst),

        # Total GST
        "tax": money(total_gst),

        # Legacy compatibility
        "tax_rate": money(current_gst_rate),

        # Other charges
        "deposit": money(security_deposit),
        "damage_charges": money(damages),
        "loss_charges": money(losses),
        "other_charges": money(others),

        # Final payable amount
        "grand_total": money(grand_total),
    }


# ---------------------------------------------------------------------------
# PAYMENT STATUS
# ---------------------------------------------------------------------------
#
# IMPORTANT - kept compatible with billing_service.py, which calls this as:
#
#     status, net_received, remaining = payment_status(
#         grand_total, received, refunded, cancelled
#     )
#
# (i.e. 4 positional args, 3 return values). Do NOT change this signature
# without also updating every caller in billing_service.py.
# ---------------------------------------------------------------------------

def payment_status(
    grand_total: object,
    received: object = 0,
    refunded: object = 0,
    cancelled: bool = False,
) -> tuple[str, float, float]:
    """Return (status, net_received, remaining) for a bill.

    status is one of: "Paid", "Partial", "Pending", "Canceled".

    Security deposit is not considered here because it is tracked
    separately from the bill's payable amount.
    """

    total = D(grand_total)

    if cancelled:
        # Cancelled bill: net received = received - refunded.
        net_received = D(received) - D(refunded)
        remaining = _rounded(D(grand_total) - D(received) + D(refunded))
        return "Canceled", money(net_received), float(remaining)

    net_received = D(received) - D(refunded)

    if net_received < 0:
        net_received = Decimal("0")

    remaining = _rounded(total - net_received)

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

    return status, money(net_received), float(remaining)


# ---------------------------------------------------------------------------
# OPTIONAL DATE HELPER
# ---------------------------------------------------------------------------

def today() -> date:
    """Return today's date."""
    return date.today()


def now() -> datetime:
    """Return current local datetime."""
    return datetime.now()