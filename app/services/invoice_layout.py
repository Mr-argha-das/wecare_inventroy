"""Normalised A4 billing-document model.

Every printable billing document (invoice, receipt, statement, quotation,
handover note...) is converted into ONE structure by this module, so the
browser print view (``templates/documents/_invoice_pages.html``) and the
generated PDF (``services/invoice_pdf.py``) always render the same thing,
with the same page breaks, from the same data.

Nothing here reads the database: the caller passes the context assembled by
``document_service.document_context()``.

All geometry constants are in millimetres and mirror
``static/css/invoice_a4.css`` 1:1 — change them in both places together.
"""
from __future__ import annotations

import textwrap
from datetime import datetime

from ..calculations import line_total
from ..utils import amount_in_words, format_date, format_inr_doc, format_qty

# ---------------------------------------------------------------------------
# Page geometry (mm) — identical to invoice_a4.css
# ---------------------------------------------------------------------------
PAGE_W = 210.0
PAGE_H = 297.0
PAD_TOP = 31.0
PAD_X = 11.5
PAD_BOTTOM = 10.0
CONTENT_W = PAGE_W - 2 * PAD_X          # 187.0
CONTENT_X = PAD_X
CONTENT_Y = PAD_TOP

HEADER_H = 37.0
RULE_H = 1.2
TITLE_H = 7.0
META_PAD_TOP = 6.0
META_MIN_H = 38.0

PAD_CELL = 1.323              # 5px cell padding, left and right
TABLE_HEAD_H = 5.159          # 10px text + 4px/4px padding
TABLE_ROW_H = 5.159
TABLE_SUB_H = 2.6             # extra .sub line (8.5px)

LOWER_TOP = 6.0
LOWER_GAP = 2.0
LEFT_COL_W = (CONTENT_W - LOWER_GAP) * 0.56     # 103.6
RIGHT_COL_W = (CONTENT_W - LOWER_GAP) * 0.44    # 81.4

BAND_H = 6.0                 # .section-title min-height
BODY_PAD = 2.65              # .section-body vertical padding (4px + 6px)
BODY_LINE_H = 3.97           # 10px * 1.5
BODY_MIN_H = 9.0             # .section-body min-height
BLOCK_GAP = 3.0              # .left-col > .block margin-bottom

BANK_TOP = 3.0               # .bank margin-top
BANK_PAD_TOP = 2.0           # .bank-content padding-top
BANK_LINE_H = 3.69           # 9px * 1.55
QR_W = 20.0
QR_H = 24.0
QR_GAP = 3.0

AMOUNT_ROW_H = 5.16          # 10px text + 4px/4px padding

FOOTER_H = 16.0
FOOTER_BOTTOM = 11.0
FOOTER_TOP = PAGE_H - FOOTER_BOTTOM - FOOTER_H   # 270.0
CONTENT_BOTTOM = FOOTER_TOP - 2.0                # safety gutter

SIGN_H = 20.0
NOTE_LINE_H = 3.5

# Characters per line used only for page-break estimation (Arial metrics).
LEFT_BODY_CHARS = 76
BANK_CHARS_WITH_QR = 66
BANK_CHARS = 86

INVOICE_TYPES = ("bill", "service_invoice", "equipment_invoice", "combined_invoice")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _f(value: object) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _s(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text.lower() in ("nan", "nat", "none") else text.strip()


def _date(value: object) -> str:
    text = format_date(value)
    return "-" if text in ("", "-") else text


def _dash_date(value: object) -> str:
    """dd-mm-yyyy (invoice details block)."""
    text = format_date(value)
    return "" if text == "-" else text.replace("/", "-")


def _money(value: object) -> str:
    return format_inr_doc(value)


def cell(text: object, sub: str = "") -> dict:
    """One table cell. Alignment comes from its column, never from the cell."""
    return {"text": _s(text), "sub": _s(sub)}


def _time_of(value: object) -> str:
    raw = _s(value)
    if not raw:
        return ""
    for parser in (datetime.fromisoformat,):
        try:
            return parser(raw).strftime("%I:%M %p")
        except Exception:
            continue
    return ""


def _wrapped_lines(text: str, width: int) -> int:
    """Number of visual lines a block of text needs (page-break estimation)."""
    total = 0
    for raw in _s(text).splitlines() or [""]:
        total += max(1, len(textwrap.wrap(raw, width=max(10, width))) or 1)
    return max(1, total)


# ---------------------------------------------------------------------------
# Height estimation (keeps HTML and PDF page breaks identical)
# ---------------------------------------------------------------------------
def _block_height(body: str) -> float:
    lines = _wrapped_lines(body, LEFT_BODY_CHARS)
    body_h = max(BODY_MIN_H, BODY_PAD + lines * BODY_LINE_H)
    return BAND_H + body_h + BLOCK_GAP


def _bank_height(bank: dict) -> float:
    if not bank.get("show"):
        return 0.0
    chars = BANK_CHARS_WITH_QR if bank.get("qr") else BANK_CHARS
    lines = sum(_wrapped_lines(line, chars) for line in bank.get("lines") or []) or 1
    text_h = lines * BANK_LINE_H + 0.3
    content = max(QR_H if bank.get("qr") else 0.0, text_h)
    return BANK_TOP + BAND_H + BANK_PAD_TOP + content


def lower_section_height(doc: dict) -> float:
    left = sum(_block_height(b["body"]) for b in doc.get("blocks", []))
    left += _bank_height(doc.get("bank", {}))
    right = BAND_H + len(doc.get("amounts", [])) * AMOUNT_ROW_H
    if doc.get("signature", {}).get("show"):
        right += SIGN_H
    extra = 0.0
    if doc.get("notes"):
        extra += BLOCK_GAP + _wrapped_lines(doc["notes"], 110) * NOTE_LINE_H
    if doc.get("company", {}).get("declaration"):
        extra += BLOCK_GAP + _wrapped_lines(doc["company"]["declaration"], 125) * NOTE_LINE_H
    return LOWER_TOP + max(left, right) + extra


TABLE_LINE_H = 3.043          # each extra wrapped line inside a cell (11.5px)
CELL_CHAR_W = 1.38            # mm per character, 10px Arial/Helvetica (safe avg)
SUB_CHAR_W = 1.17             # mm per character, 8.5px


def cell_chars(col_w_mm: float, char_w: float = CELL_CHAR_W) -> int:
    """How many characters fit on one line of a column (wrap estimation)."""
    return max(6, int((col_w_mm - 2 * PAD_CELL) / char_w))


def cell_lines(text: str, col_w_mm: float, char_w: float = CELL_CHAR_W) -> int:
    return _wrapped_lines(text, cell_chars(col_w_mm, char_w))


def row_metrics(row: list[dict], columns: list[dict] | None = None) -> tuple[int, int, float]:
    """(main text lines, sub lines, row height) for one table row.

    The PDF renderer uses the very same numbers, so a row occupies exactly the
    same height in the browser and in the download.
    """
    lines, sub_lines = 1, 0
    for idx, data in enumerate(row):
        col = (columns or [])[idx] if columns and idx < len(columns) else None
        w = CONTENT_W * _f(col.get("w")) / 100.0 if col else CONTENT_W / max(1, len(row))
        if _s(data.get("text")):
            lines = max(lines, cell_lines(_s(data.get("text")), w))
        if _s(data.get("sub")):
            sub_lines = max(sub_lines, cell_lines(_s(data.get("sub")), w, SUB_CHAR_W))
    height = TABLE_ROW_H + (lines - 1) * TABLE_LINE_H + sub_lines * TABLE_SUB_H
    return lines, sub_lines, height


def row_height(row: list[dict], columns: list[dict] | None = None) -> float:
    return row_metrics(row, columns)[2]


TITLE_BORDER = 0.265          # 1px rule under the document title


def table_top() -> float:
    """Y of the items-table header on every page."""
    return CONTENT_Y + HEADER_H + RULE_H + TITLE_H + TITLE_BORDER + META_PAD_TOP + META_MIN_H


def paginate(doc: dict) -> list[dict]:
    """Split table rows across A4 pages; the lower section sits on the last page."""
    rows = doc.get("table", {}).get("rows", [])
    columns = doc.get("table", {}).get("columns", [])
    start_y = table_top() + TABLE_HEAD_H
    space_full = CONTENT_BOTTOM - start_y
    space_last = space_full - lower_section_height(doc)

    def fit(items: list[list[dict]], space: float) -> int:
        used, count = 0.0, 0
        for row in items:
            h = row_height(row, columns)
            if used + h > space:
                break
            used += h
            count += 1
        return count

    chunks: list[list[list[dict]]] = []
    rest = list(rows)
    guard = 0
    while True:
        guard += 1
        if guard > 200:                      # never loop forever on bad data
            chunks.append(rest)
            break
        if fit(rest, space_last) >= len(rest):
            chunks.append(rest)
            break
        take = max(1, fit(rest, space_full))
        chunks.append(rest[:take])
        rest = rest[take:]
        if not rest:
            chunks.append([])                # lower section needs its own page
            break

    if len(chunks) > 1 and not chunks[-1] and chunks[-2]:
        moved = chunks[-2][-1:]
        chunks[-2] = chunks[-2][:-1]
        chunks[-1] = moved

    total = len(chunks)
    return [{"index": i + 1, "count": total, "rows": chunk, "show_lower": i == total - 1}
            for i, chunk in enumerate(chunks)]


# ---------------------------------------------------------------------------
# Column sets
# ---------------------------------------------------------------------------
INVOICE_COLUMNS = [
    {"label": "Services/Equipment", "cls": "desc", "w": 32},
    {"label": "Starting Date", "cls": "date", "w": 16},
    {"label": "Till Date", "cls": "date", "w": 16},
    {"label": "Days", "cls": "days", "w": 8},
    {"label": "Price", "cls": "price", "w": 14},
    {"label": "Amount", "cls": "amount", "w": 14},
]

RECEIPT_COLUMNS = [
    {"label": "Description", "cls": "desc", "w": 38},
    {"label": "Date", "cls": "date", "w": 14},
    {"label": "Mode", "cls": "date", "w": 14},
    {"label": "Reference", "cls": "date", "w": 20},
    {"label": "Amount", "cls": "amount", "w": 14},
]

STATEMENT_COLUMNS = [
    {"label": "Invoice No.", "cls": "desc", "w": 24},
    {"label": "Date", "cls": "date", "w": 14},
    {"label": "Status", "cls": "date", "w": 14},
    {"label": "Total", "cls": "price", "w": 16},
    {"label": "Received", "cls": "price", "w": 16},
    {"label": "Pending", "cls": "amount", "w": 16},
]

EQUIPMENT_COLUMNS = [
    {"label": "Equipment", "cls": "desc", "w": 28},
    {"label": "Serial No.", "cls": "date", "w": 16},
    {"label": "Issue Date", "cls": "date", "w": 14},
    {"label": "Return Date", "cls": "date", "w": 14},
    {"label": "Condition", "cls": "date", "w": 14},
    {"label": "Amount", "cls": "amount", "w": 14},
]

SHEET_COLUMNS = [
    {"label": "Service / Equipment", "cls": "desc", "w": 32},
    {"label": "Starting Date", "cls": "date", "w": 16},
    {"label": "Till Date", "cls": "date", "w": 16},
    {"label": "Days", "cls": "days", "w": 8},
    {"label": "Price", "cls": "price", "w": 14},
    {"label": "Amount", "cls": "amount", "w": 14},
]


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------
def _bank_block(company: dict) -> dict:
    lines: list[str] = []
    bank_name = _s(company.get("bank_name"))
    branch = _s(company.get("branch"))
    if bank_name:
        lines.append("Name: " + bank_name + (f", {branch}" if branch else ""))
    elif branch:
        lines.append(f"Branch: {branch}")
    if _s(company.get("account_number")):
        lines.append("Account No: " + _s(company["account_number"]))
    if _s(company.get("ifsc")):
        lines.append("IFSC code: " + _s(company["ifsc"]))
    if _s(company.get("account_name")):
        lines.append("Account Holder's Name: " + _s(company["account_name"]))
    if _s(company.get("upi_details")):
        lines.append("UPI: " + _s(company["upi_details"]))
    if not lines and _s(company.get("bank_details")):
        lines = [ln.strip() for ln in _s(company["bank_details"]).splitlines() if ln.strip()]
    qr = _s(company.get("upi_qr")) if company.get("show_qr", True) else ""
    return {"show": bool(lines or qr), "lines": lines, "qr": qr}


def _signature(company: dict) -> dict:
    return {
        "show": bool(company.get("show_signature")),
        "image": _s(company.get("signature_image")),
        "name": _s(company.get("signature_name")) or "Authorized Signatory",
    }


def _blocks(words: str, payment_type: str, company: dict, terms: str | None = None,
            words_title: str = "Amount in Words") -> list[dict]:
    blocks: list[dict] = []
    if words:
        blocks.append({"title": words_title, "body": words})
    if payment_type:
        blocks.append({"title": "Payment Type", "body": payment_type})
    text = terms if terms is not None else _s(company.get("terms"))
    if _s(text):
        blocks.append({"title": "Terms and conditions", "body": _s(text)})
    return blocks


def _patient_lines(patient: dict) -> list[str]:
    lines = []
    if _s(patient.get("mobile")):
        lines.append("Contact No : " + _s(patient["mobile"]))
    if _s(patient.get("address")):
        lines.append(_s(patient["address"]))
    return lines


def _invoice_details(company: dict, number: str, date: str, time_text: str,
                     label: str = "Invoice No.", extra: list[tuple[str, str]] | None = None,
                     heading: str = "Invoice Details", po_date: bool = False) -> dict:
    rows: list[tuple[str, str]] = [(label, _s(number)), ("Date", _dash_date(date))]
    if time_text and company.get("show_time", True):
        rows.append(("Time", time_text))
    if po_date and company.get("show_po_date", True):
        rows.append(("PO date", _dash_date(date)))
    rows.extend(extra or [])
    return {"heading": heading, "rows": [(k, v) for k, v in rows if v]}


# ---------------------------------------------------------------------------
# Amount panels
# ---------------------------------------------------------------------------
def _amount(label: str, value: object, strong: bool = False, muted: bool = False) -> dict:
    return {"label": label, "value": _money(value), "strong": strong, "muted": muted}


def bill_amount_rows(bill: dict, net_received: float) -> list[dict]:
    rows = [_amount("Sub Total", bill.get("subtotal"))]
    discount = _f(bill.get("discount"))
    if discount:
        rows.append({"label": "Discount", "value": "- " + _money(discount), "strong": False, "muted": False})
    cgst, sgst, igst = _f(bill.get("cgst")), _f(bill.get("sgst")), _f(bill.get("igst"))
    gst_rate = _f(bill.get("gst_rate")) or _f(bill.get("tax_rate"))
    if cgst or sgst or igst:
        if discount:
            rows.append(_amount("Taxable Amount", bill.get("taxable_amount") or (_f(bill.get("subtotal")) - discount)))
        if cgst:
            rows.append(_amount(f"CGST ({format_qty(gst_rate / 2)}%)", cgst))
        if sgst:
            rows.append(_amount(f"SGST ({format_qty(gst_rate / 2)}%)", sgst))
        if igst:
            rows.append(_amount(f"IGST ({format_qty(gst_rate)}%)", igst))
    elif _f(bill.get("tax")):
        rows.append(_amount(f"Tax ({format_qty(gst_rate)}%)", bill.get("tax")))
    for key, label in (("damage_charges", "Damage Charges"), ("loss_charges", "Loss Charges"),
                       ("other_charges", "Other Charges")):
        if _f(bill.get(key)):
            rows.append(_amount(label, bill.get(key)))
    rows.append(_amount("Total", bill.get("grand_total"), strong=True))
    rows.append(_amount("Received", net_received))
    balance = _f(bill.get("remaining_amount"))
    if round(balance, 2):
        rows.append(_amount("Balance", balance))
    if _f(bill.get("deposit")):
        rows.append(_amount("Security Deposit (refundable)", bill.get("deposit"), muted=True))
    return rows


# ---------------------------------------------------------------------------
# Item rows
# ---------------------------------------------------------------------------
def _item_units(item: dict) -> float:
    """Billable units actually multiplied by the rate (days / qty / qty*days)."""
    unit_type = _s(item.get("unit_type")).lower()
    units = _f(item.get("units"))
    if units:
        return units
    if unit_type == "days":
        return _f(item.get("days")) or _f(item.get("quantity"))
    if unit_type == "both":
        return (_f(item.get("quantity")) or 1) * (_f(item.get("days")) or 1)
    return _f(item.get("quantity")) or _f(item.get("days"))


def _item_amount(item: dict) -> float:
    """Line amount as stored; recomputed when the record does not carry one."""
    if item.get("amount") not in (None, ""):
        return _f(item.get("amount"))
    return line_total(item.get("quantity"), item.get("rate"), item.get("discount"),
                      days=item.get("days"), unit_type=item.get("unit_type"))


def _item_rows(items: list[dict]) -> list[list[dict]]:
    rows = []
    for it in items or []:
        sub_parts = []
        if _s(it.get("serial_number")):
            sub_parts.append("S/N: " + _s(it["serial_number"]))
        if _f(it.get("discount")):
            sub_parts.append("Discount: " + _money(it.get("discount")))
        rows.append([
            cell(_s(it.get("description")) or "-", " | ".join(sub_parts)),
            cell(_date(it.get("start_date"))),
            cell(_date(it.get("end_date"))),
            cell(format_qty(_item_units(it))),
            cell(_money(it.get("rate"))),
            cell(_money(_item_amount(it))),
        ])
    return rows


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
def build_document(doc_type: str, ctx: dict) -> dict:
    """Convert a document context into the shared A4 layout model."""
    company = ctx.get("company", {}) or {}
    patient = ctx.get("patient", {}) or {}
    title = _s(ctx.get("title")) or "Tax Invoice"

    doc: dict = {
        "doc_type": doc_type,
        "title": title,
        "doc_number": _s(ctx.get("doc_number")),
        "doc_date": _s(ctx.get("doc_date")),
        "company": company,
        "amounts_title": "Amounts",
        "bill_to": {"heading": "Bill To", "name": _s(patient.get("patient_name")), "lines": _patient_lines(patient)},
        "details": _invoice_details(company, ctx.get("doc_number"), ctx.get("doc_date"), ""),
        "table": {"columns": INVOICE_COLUMNS, "rows": []},
        "blocks": [],
        "bank": _bank_block(company),
        "amounts": [],
        "signature": _signature(company),
        "notes": "",
    }

    # ---------------- Invoices / agreement -------------------------------
    if doc_type in INVOICE_TYPES or doc_type == "service_agreement":
        bill = ctx.get("bill", {}) or {}
        net_received = _f(ctx.get("net_received"))
        doc["title"] = _s(company.get("invoice_title")) or "Tax Invoice"
        if doc_type == "service_agreement":
            doc["title"] = "Service Agreement"
        extra = []
        if bool(bill.get("cancelled")):
            extra.append(("Status", "CANCELLED"))
        doc["details"] = _invoice_details(
            company, bill.get("bill_number") or ctx.get("doc_number"),
            bill.get("bill_date") or ctx.get("doc_date"), _time_of(bill.get("created_at")),
            extra=extra, po_date=True)
        doc["table"]["rows"] = _item_rows(ctx.get("items") or [])
        doc["blocks"] = _blocks(amount_in_words(bill.get("grand_total")),
                                _s(ctx.get("payment_type")), company,
                                words_title="Invoice Amount in Words")
        doc["amounts"] = bill_amount_rows(bill, net_received)
        doc["notes"] = _s(bill.get("notes"))
        return _finish(doc)

    # ---------------- Quotation ------------------------------------------
    if doc_type == "quotation":
        quo = ctx.get("quotation", {}) or {}
        doc["title"] = "Quotation"
        doc["bill_to"]["name"] = _s(patient.get("patient_name")) or _s(quo.get("customer_name"))
        extra = []
        if _s(quo.get("valid_until")):
            extra.append(("Valid Until", _dash_date(quo.get("valid_until"))))
        doc["details"] = _invoice_details(company, quo.get("quotation_number"), quo.get("quotation_date"),
                                          _time_of(quo.get("created_at")), label="Quotation No.",
                                          extra=extra, heading="Quotation Details")
        doc["table"]["rows"] = _item_rows(ctx.get("items") or [])
        doc["blocks"] = _blocks(amount_in_words(quo.get("grand_total")), "",
                                company, terms=_s(quo.get("terms")) or _s(company.get("terms")))
        amounts = [_amount("Sub Total", quo.get("subtotal"))]
        if _f(quo.get("discount")):
            amounts.append({"label": "Discount", "value": "- " + _money(quo.get("discount")),
                            "strong": False, "muted": False})
        if _f(quo.get("tax")):
            amounts.append(_amount(f"Tax ({format_qty(quo.get('tax_rate'))}%)", quo.get("tax")))
        amounts.append(_amount("Total", quo.get("grand_total"), strong=True))
        doc["amounts"] = amounts
        doc["notes"] = _s(quo.get("notes"))
        return _finish(doc)

    # ---------------- Payment receipt ------------------------------------
    if doc_type == "payment_receipt":
        pay = ctx.get("payment", {}) or {}
        bill = ctx.get("bill", {}) or {}
        doc["title"] = "Payment Receipt"
        doc["table"]["columns"] = RECEIPT_COLUMNS
        doc["details"] = _invoice_details(company, pay.get("payment_id"), pay.get("payment_date"),
                                          _time_of(pay.get("created_at")), label="Receipt No.",
                                          heading="Receipt Details")
        desc = "Payment received"
        if _s(bill.get("bill_number")):
            desc += " against Invoice " + _s(bill["bill_number"])
        doc["table"]["rows"] = [[
            cell(desc, _s(pay.get("notes"))),
            cell(_date(pay.get("payment_date"))),
            cell(_s(pay.get("method")) or "-"),
            cell(_s(pay.get("transaction_id")) or "-"),
            cell(_money(pay.get("amount"))),
        ]]
        doc["blocks"] = _blocks(amount_in_words(pay.get("amount")), _s(pay.get("method")), company)
        amounts = [_amount("Received Now", pay.get("amount"), strong=True)]
        if bill:
            net = _f(bill.get("received_amount")) - _f(bill.get("refunded_amount"))
            amounts = [
                _amount("Invoice Total", bill.get("grand_total")),
                _amount("Received Now", pay.get("amount"), strong=True),
                _amount("Total Received", net),
                _amount("Balance", bill.get("remaining_amount")),
            ]
        doc["amounts"] = amounts
        doc["amounts_title"] = "Amounts"
        if _s(pay.get("received_by")):
            doc["notes"] = "Received by: " + _s(pay["received_by"])
        return _finish(doc)

    # ---------------- Deposit receipt ------------------------------------
    if doc_type == "deposit_receipt":
        dep = ctx.get("deposit", {}) or {}
        doc["title"] = "Advance / Deposit Receipt"
        doc["table"]["columns"] = RECEIPT_COLUMNS
        doc["details"] = _invoice_details(company, dep.get("deposit_id"), dep.get("deposit_date"),
                                          _time_of(dep.get("created_at")), label="Receipt No.",
                                          heading="Receipt Details")
        doc["table"]["rows"] = [[
            cell(_s(dep.get("purpose")) or "Advance", _s(dep.get("notes"))),
            cell(_date(dep.get("deposit_date"))),
            cell(_s(dep.get("method")) or "-"),
            cell(_s(dep.get("transaction_id")) or "-"),
            cell(_money(dep.get("amount"))),
        ]]
        doc["blocks"] = _blocks(amount_in_words(dep.get("amount")), _s(dep.get("method")), company)
        held = _f(dep.get("amount")) - _f(dep.get("adjusted_amount")) - _f(dep.get("refunded_amount"))
        doc["amounts"] = [
            _amount("Deposit Amount", dep.get("amount"), strong=True),
            _amount("Adjusted", dep.get("adjusted_amount")),
            _amount("Refunded", dep.get("refunded_amount")),
            _amount("Balance Held", max(0.0, held)),
        ]
        if _s(dep.get("received_by")):
            doc["notes"] = "Received by: " + _s(dep["received_by"])
        return _finish(doc)

    # ---------------- Pending / monthly statement ------------------------
    if doc_type in ("pending_statement", "monthly_statement"):
        rows_in = ctx.get("rows") or []
        monthly = doc_type == "monthly_statement"
        doc["title"] = "Monthly Bill Statement" if monthly else "Pending Payment Statement"
        doc["table"]["columns"] = STATEMENT_COLUMNS
        extra = [("Month", _s(ctx.get("month")))] if monthly and _s(ctx.get("month")) else []
        doc["details"] = _invoice_details(company, ctx.get("doc_number"), ctx.get("doc_date"), "",
                                          label="Statement No.", extra=extra,
                                          heading="Statement Details")
        rows, total, received, pending = [], 0.0, 0.0, 0.0
        for r in rows_in:
            r_total = _f(r.get("grand_total"))
            r_recv = _f(r.get("received")) if "received" in r else (
                _f(r.get("received_amount")) - _f(r.get("refunded_amount")))
            r_pend = _f(r.get("pending")) if "pending" in r else _f(r.get("remaining_amount"))
            total += r_total
            received += r_recv
            pending += r_pend
            rows.append([
                cell(_s(r.get("bill_number")) or "-"),
                cell(_date(r.get("bill_date"))),
                cell(_s(r.get("status")) or "-"),
                cell(_money(r_total)),
                cell(_money(r_recv)),
                cell(_money(r_pend)),
            ])
        doc["table"]["rows"] = rows
        headline = pending if not monthly else total
        doc["blocks"] = _blocks(amount_in_words(headline), "", company)
        doc["amounts"] = [
            _amount("Total Billed", total),
            _amount("Total Received", received),
            _amount("Total Pending", pending, strong=True),
        ]
        return _finish(doc)

    # ---------------- Equipment handover / return ------------------------
    if doc_type in ("equipment_issue", "equipment_return"):
        txn = ctx.get("txn", {}) or {}
        eq = ctx.get("equipment", {}) or {}
        doc["title"] = "Equipment Handover Receipt" if doc_type == "equipment_issue" else "Equipment Return Receipt"
        doc["table"]["columns"] = EQUIPMENT_COLUMNS
        doc["details"] = _invoice_details(company, ctx.get("doc_number"), ctx.get("doc_date"),
                                          _time_of(txn.get("created_at")), label="Receipt No.",
                                          heading="Receipt Details")
        doc["table"]["rows"] = [[
            cell(_s(eq.get("equipment_name")) or "-", _s(eq.get("equipment_id"))),
            cell(_s(txn.get("serial_number")) or "-"),
            cell(_date(txn.get("issue_date"))),
            cell(_date(txn.get("return_date")) if _s(txn.get("return_date"))
                 else _date(txn.get("expected_return_date"))),
            cell(_s(txn.get("condition")) or "-"),
            cell(_money(txn.get("rent_amount"))),
        ]]
        doc["blocks"] = _blocks(amount_in_words(txn.get("rent_amount")), "", company)
        amounts = [_amount("Rent Amount", txn.get("rent_amount"))]
        for key, label in (("deposit", "Security Deposit"), ("damage_charge", "Damage Charge"),
                           ("loss_charge", "Loss Charge")):
            if _f(txn.get(key)):
                amounts.append(_amount(label, txn.get(key)))
        amounts.append(_amount("Refundable", txn.get("refund_amount"), strong=True))
        doc["amounts"] = amounts
        doc["notes"] = _s(txn.get("notes"))
        return _finish(doc)

    # ---------------- Patient service sheet ------------------------------
    if doc_type == "patient_sheet":
        doc["title"] = "Patient Service Details Sheet"
        doc["table"]["columns"] = SHEET_COLUMNS
        doc["details"] = _invoice_details(company, ctx.get("doc_number"), ctx.get("doc_date"), "",
                                          label="Sheet No.", heading="Sheet Details")
        rows = []
        for s in ctx.get("services") or []:
            units = _item_units(s)
            rows.append([
                cell(_s(s.get("service_name")) or "-", _s(s.get("billing_type"))),
                cell(_date(s.get("start_date"))),
                cell(_date(s.get("end_date"))),
                cell(format_qty(units)),
                cell(_money(s.get("rate"))),
                cell(_money(s.get("amount"))),
            ])
        for t in ctx.get("txns") or []:
            rows.append([
                cell(_s(t.get("equipment_id")) or "-", _s(t.get("txn_type"))),
                cell(_date(t.get("issue_date"))),
                cell(_date(t.get("return_date"))),
                cell("-"),
                cell("-"),
                cell(_money(t.get("rent_amount"))),
            ])
        doc["table"]["rows"] = rows
        bills = ctx.get("bills") or []
        total = sum(_f(b.get("grand_total")) for b in bills)
        received = sum(_f(b.get("received_amount")) - _f(b.get("refunded_amount")) for b in bills)
        pending = sum(_f(b.get("remaining_amount")) for b in bills)
        doc["blocks"] = _blocks(amount_in_words(total), "", company)
        doc["amounts"] = [
            _amount("Total Billed", total),
            _amount("Total Received", received),
            _amount("Total Pending", pending, strong=True),
        ]
        return _finish(doc)

    # ---------------- Fallback -------------------------------------------
    doc["blocks"] = _blocks("", "", company)
    return _finish(doc)


def _finish(doc: dict) -> dict:
    doc["pages"] = paginate(doc)
    return doc
