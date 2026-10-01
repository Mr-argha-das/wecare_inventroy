"""Document data assembly + PDF generation using live database data.

Every billing document (invoice, receipt, statement, quotation, handover
note) is assembled here, normalised by ``invoice_layout`` and rendered by
``invoice_pdf`` so the PDF is identical to the browser print view.

``DocPDF`` below is still used for the *report* PDFs (Reports section).
"""
import io
import os
from pathlib import Path

from fpdf import FPDF

from .. import database as db
from ..config import PDF_DIR, UPLOAD_DIR
from ..utils import amount_in_words, format_date, format_inr, format_inr_doc, format_qty
from . import invoice_layout, invoice_pdf

FONT_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"
SYSTEM_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
SYSTEM_FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

DEFAULT_BRAND_COLOR = "#16a34a"
# Exact palette of the approved invoice design.
DEFAULT_INVOICE_GREEN = "#008d09"
DEFAULT_RULE_GREEN = "#0a8f12"
DEFAULT_TITLE_GREEN = "#07850e"
DEFAULT_LINK_BLUE = "#27a5d5"
DEFAULT_INVOICE_TITLE = "Tax Invoice"

# "auto" uses WeasyPrint when it is installed (pixel-perfect HTML rendering)
# and otherwise the built-in renderer, which paints the same layout.
PDF_ENGINE = (os.environ.get("PDF_ENGINE") or "auto").strip().lower()


def _hex_to_rgb(value: str, fallback: tuple[int, int, int] = (22, 163, 74)) -> tuple[int, int, int]:
    """Convert '#rrggbb' / '#rgb' to an (r, g, b) tuple, falling back safely."""
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return fallback


def _tint(rgb: tuple[int, int, int], factor: float = 0.88) -> tuple[int, int, int]:
    """Lighten an RGB colour towards white (used for table header fills)."""
    return tuple(int(c + (255 - c) * factor) for c in rgb)  # type: ignore[return-value]


def bank_lines(company: dict) -> list[str]:
    """Human-readable payment lines built from structured bank fields,
    falling back to the free-text `bank_details` box."""
    pairs = [
        ("Bank", company.get("bank_name")),
        ("Account Name", company.get("account_name")),
        ("Account No", company.get("account_number")),
        ("IFSC", company.get("ifsc")),
        ("Branch", company.get("branch")),
    ]
    lines = [f"{label}: {val}" for label, val in pairs if str(val or "").strip()]
    if not lines and str(company.get("bank_details") or "").strip():
        lines = [ln for ln in str(company["bank_details"]).splitlines() if ln.strip()]
    if str(company.get("upi_details") or "").strip():
        lines.append(f"UPI: {company['upi_details']}")
    return lines




def _flag(settings: dict, key: str, default: bool = True) -> bool:
    raw = str(settings.get(key, "")).strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "on")


def header_lines(profile: dict) -> list[str]:
    """Lines printed under the brand name, top-right of every document."""
    lines: list[str] = []
    for part in str(profile.get("address") or "").splitlines():
        if part.strip():
            lines.append(part.strip())
    contact = " ".join(x for x in [
        f"Phone no : {profile['mobile']}" if str(profile.get("mobile") or "").strip() else "",
        f"Email: {profile['email']}" if str(profile.get("email") or "").strip() else "",
    ] if x)
    if contact:
        lines.append(contact)
    if str(profile.get("header_note") or "").strip():
        lines.append(str(profile["header_note"]).strip())
    return lines


def company_profile() -> dict:
    """Latest branding + invoice-design settings (admin editable)."""
    s = db.get_all_settings("company_settings")
    invoice_color = (s.get("invoice_color", "") or s.get("brand_color", "") or DEFAULT_INVOICE_GREEN).strip()
    exact = invoice_color.lower() in ("", DEFAULT_INVOICE_GREEN)
    profile = {
        "brand_name": s.get("brand_name", "WE CARE HOME HEALTHCARE"),
        "address": s.get("address", ""),
        "mobile": s.get("mobile", ""),
        "email": s.get("email", ""),
        "website": s.get("website", ""),
        "gstin": s.get("gstin", ""),
        "gst_rate": s.get("gst_rate", "0"),
        "brand_color": s.get("brand_color", "") or DEFAULT_BRAND_COLOR,
        # ---- invoice design (Settings -> Invoice Design) ----
        "invoice_title": s.get("invoice_title", "") or DEFAULT_INVOICE_TITLE,
        "header_note": s.get("header_note", ""),
        "invoice_color": invoice_color or DEFAULT_INVOICE_GREEN,
        "rule_color": DEFAULT_RULE_GREEN if exact else invoice_color,
        "title_color": DEFAULT_TITLE_GREEN if exact else invoice_color,
        "link_color": s.get("link_color", "") or DEFAULT_LINK_BLUE,
        "footer_text": s.get("footer_text", "") if "footer_text" in s else s.get("website", ""),
        "footer_image": s.get("footer_image", ""),
        "show_po_date": _flag(s, "show_po_date", True),
        "show_time": _flag(s, "show_time", True),
        "show_page_number": _flag(s, "show_page_number", True),
        "show_qr": _flag(s, "show_qr", True),
        "show_signature": _flag(s, "show_signature", False),
        # ---- structured bank fields + legacy free-text fallback ----
        "bank_name": s.get("bank_name", ""),
        "account_name": s.get("account_name", ""),
        "account_number": s.get("account_number", ""),
        "ifsc": s.get("ifsc", ""),
        "branch": s.get("branch", ""),
        "bank_details": s.get("bank_details", ""),
        "upi_details": s.get("upi_details", ""),
        "upi_qr": s.get("upi_qr", ""),
        "terms": s.get("terms", ""),
        "declaration": s.get("declaration", ""),
        "footer_note": s.get("footer_note", ""),
        "signature_name": s.get("signature_name", "Authorized Signatory"),
        "signature_image": s.get("signature_image", ""),
        "logo": s.get("logo", ""),
        "invoice_prefix": s.get("invoice_prefix", "") or db.DEFAULT_INVOICE_PREFIX,
    }
    if not str(profile["footer_text"]).strip():
        profile["footer_text"] = str(profile.get("website") or "").strip()
    profile["bank_lines"] = bank_lines(profile)
    profile["header_lines"] = header_lines(profile)
    profile["rgb"] = _hex_to_rgb(profile["brand_color"])
    return profile


def invoice_extras(full: dict) -> dict:
    """Derived invoice fields: payment type, created time, amount in words."""
    bill = full.get("bill", {}) or {}
    payments = [p for p in (full.get("payments") or []) if not bool(p.get("cancelled", False))]
    methods: list[str] = []
    for p in payments:
        m = str(p.get("method", "") or "").strip()
        if m and m not in methods:
            methods.append(m)
    received = float(bill.get("received_amount", 0) or 0) - float(bill.get("refunded_amount", 0) or 0)
    payment_type = " / ".join(methods) if methods else ("Credit" if received <= 0 else "Cash")
    created = str(bill.get("created_at", "") or "")
    time_text = ""
    if created:
        try:
            from datetime import datetime
            time_text = datetime.fromisoformat(created).strftime("%I:%M %p")
        except Exception:
            time_text = ""
    return {
        "payment_type": payment_type,
        "invoice_time": time_text,
        "amount_words": amount_in_words(bill.get("grand_total", 0)),
        "net_received": round(received, 2),
    }


def document_context(doc_type: str, ref_id: str) -> dict | None:
    """Assemble full data for any printable document from live records."""
    company = company_profile()
    base = {"company": company, "doc_type": doc_type}
    if doc_type in ("service_invoice", "equipment_invoice", "combined_invoice", "bill"):
        from .billing_service import get_bill_full
        full = get_bill_full(ref_id)
        if not full:
            return None
        base.update(full)
        base["title"] = {
            "service_invoice": "SERVICE INVOICE", "equipment_invoice": "EQUIPMENT RENTAL INVOICE",
            "combined_invoice": "COMBINED INVOICE", "bill": "TAX INVOICE",
        }[doc_type]
        base["doc_number"] = full["bill"]["bill_number"]
        base["doc_date"] = full["bill"]["bill_date"]
        base.update(invoice_extras(full))
        return base
    if doc_type == "payment_receipt":
        payment = db.get_record("payments", ref_id)
        if not payment:
            return None
        from .billing_service import get_bill_full
        full = get_bill_full(str(payment.get("bill_id", "")))
        base.update({"payment": payment, "bill": (full or {}).get("bill", {}),
                     "patient": (full or {}).get("patient", {}),
                     "title": "PAYMENT RECEIPT", "doc_number": payment["payment_id"],
                     "doc_date": payment["payment_date"]})
        return base
    if doc_type == "deposit_receipt":
        deposit = db.get_record("deposits", ref_id)
        if not deposit:
            return None
        patient = db.get_record("patients", str(deposit.get("patient_id", ""))) or {}
        base.update({"deposit": deposit, "patient": patient, "title": "ADVANCE / DEPOSIT RECEIPT",
                     "doc_number": deposit["deposit_id"], "doc_date": deposit["deposit_date"]})
        return base
    if doc_type == "pending_statement":
        from . import report_service
        patient = db.get_record("patients", ref_id)
        if not patient:
            return None
        rows = [r for r in report_service.pending_payments() if r["patient_id"] == ref_id]
        base.update({"patient": patient, "rows": rows, "title": "PENDING PAYMENT STATEMENT",
                     "doc_number": f"STMT-{ref_id}", "doc_date": db.today_str(),
                     "total_pending": round(sum(r["pending"] for r in rows), 2)})
        return base
    if doc_type in ("equipment_issue", "equipment_return"):
        txn = db.get_record("equipment_transactions", ref_id)
        if not txn:
            return None
        eq = db.get_record("equipment", str(txn.get("equipment_id", ""))) or {}
        patient = db.get_record("patients", str(txn.get("patient_id", ""))) or {}
        base.update({"txn": txn, "equipment": eq, "patient": patient,
                     "title": "EQUIPMENT HANDOVER RECEIPT" if doc_type == "equipment_issue" else "EQUIPMENT RETURN RECEIPT",
                     "doc_number": txn["txn_id"][:14].upper(), "doc_date": txn.get("return_date") or txn.get("issue_date")})
        return base
    if doc_type == "service_agreement":
        from .billing_service import get_bill_full
        full = get_bill_full(ref_id)
        if not full:
            return None
        base.update(full)
        base.update({"title": "SERVICE AGREEMENT / TERMS & CONDITIONS",
                     "doc_number": f"AGR-{full['bill']['bill_number']}", "doc_date": full["bill"]["bill_date"]})
        base.update(invoice_extras(full))
        return base
    if doc_type == "patient_sheet":
        patient = db.get_record("patients", ref_id)
        if not patient:
            return None
        bills = db.find_records("bills", patient_id=ref_id).to_dict("records")
        services = db.find_records("service_records", patient_id=ref_id).to_dict("records")
        txns = db.find_records("equipment_transactions", patient_id=ref_id).to_dict("records")
        base.update({"patient": patient, "bills": bills, "services": services, "txns": txns,
                     "title": "PATIENT SERVICE DETAILS SHEET", "doc_number": f"PDS-{ref_id}",
                     "doc_date": db.today_str()})
        return base
    if doc_type == "monthly_statement":
        # ref_id = patient_id; month passed via query handled by router -> use current month rows
        patient = db.get_record("patients", ref_id)
        if not patient:
            return None
        month = db.today_str()[:7]
        bills = db.find_records("bills", patient_id=ref_id)
        rows = [dict(r) for _, r in bills.iterrows() if str(r["bill_date"])[:7] == month and not bool(r["cancelled"])]
        base.update({"patient": patient, "rows": rows, "month": month, "title": "MONTHLY BILL STATEMENT",
                     "doc_number": f"MS-{month}-{ref_id}", "doc_date": db.today_str(),
                     "total": round(sum(float(r.get("grand_total", 0)) for r in rows), 2)})
        return base
    if doc_type == "quotation":
        q = db.get_record("quotations", ref_id)
        if not q:
            return None
        import json
        try:
            items = json.loads(q.get("items_json", "[]"))
        except Exception:
            items = []
        patient = db.get_record("patients", str(q.get("patient_id", ""))) or {}
        base.update({"quotation": q, "items": items, "patient": patient, "title": "QUOTATION / ESTIMATE",
                     "doc_number": q["quotation_number"], "doc_date": q["quotation_date"]})
        return base
    return None




# ---------------------------------------------------------------------------
# One layout for every billing document
# ---------------------------------------------------------------------------
def build_doc(doc_type: str, ref_id: str) -> tuple[dict | None, str]:
    """Context -> shared A4 document model (print view and PDF use this)."""
    ctx = document_context(doc_type, ref_id)
    if not ctx:
        return None, "Document data not found."
    return invoice_layout.build_document(doc_type, ctx), ""


_JINJA_ENV = None


def _jinja_env():
    global _JINJA_ENV
    if _JINJA_ENV is None:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
        _JINJA_ENV = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                                 autoescape=select_autoescape(["html"]))
    return _JINJA_ENV


def document_html(doc: dict) -> str:
    """Stand-alone HTML of the document (used by the optional HTML->PDF engine)."""
    css = (STATIC_DIR / "css" / "invoice_a4.css").read_text(encoding="utf-8")
    asset_base = Path(UPLOAD_DIR).resolve().as_uri() + "/"
    return _jinja_env().get_template("documents/invoice_pdf.html").render(
        doc=doc, invoice_css=css, asset_base=asset_base)


def _html_pdf(doc: dict) -> bytes | None:
    """Render through WeasyPrint when available/requested, else None."""
    if PDF_ENGINE == "builtin":
        return None
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:
        return None
    try:
        return HTML(string=document_html(doc), base_url=str(STATIC_DIR)).write_pdf()
    except Exception:  # never fail a download because of an optional engine
        import logging
        logging.getLogger("wecare.documents").warning("WeasyPrint failed; using built-in PDF renderer",
                                                      exc_info=True)
        return None


def generate_pdf_bytes(doc_type: str, ref_id: str) -> tuple[bytes | None, str]:
    """PDF bytes for any billing document, in the approved A4 design."""
    doc, err = build_doc(doc_type, ref_id)
    if not doc:
        return None, err
    data = _html_pdf(doc)
    if data:
        return data, ""
    return invoice_pdf.render(doc), ""


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------
class DocPDF(FPDF):
    def __init__(self, company: dict):
        super().__init__(format="A4")
        self.company = company
        self.brand_rgb = company.get("rgb") or _hex_to_rgb(company.get("brand_color", ""))
        self.brand_soft = _tint(self.brand_rgb)
        self._setup_fonts()
        self.set_auto_page_break(True, margin=25)
        self.set_margins(12, 12, 12)

    def _setup_fonts(self):
        regular = FONT_DIR / "DejaVuSans.ttf"
        bold = FONT_DIR / "DejaVuSans-Bold.ttf"
        if not regular.exists() and SYSTEM_FONT.exists():
            regular = SYSTEM_FONT
        if not bold.exists() and SYSTEM_FONT_BOLD.exists():
            bold = SYSTEM_FONT_BOLD
        try:
            self.add_font("wc", "", str(regular))
            self.add_font("wc", "B", str(bold))
            self.font_family = "wc"
        except Exception:
            self.font_family = "helvetica"

    def _t(self, text: object) -> str:
        s = "" if text is None else str(text)
        if self.font_family == "helvetica":
            s = s.replace("₹", "Rs. ")
        return s

    def header(self):
        c = self.company
        # Logo
        logo = (c.get("logo") or "")
        if logo:
            lp = Path(UPLOAD_DIR) / logo
            if lp.exists():
                try:
                    self.image(str(lp), x=12, y=8, h=18)
                except Exception:
                    pass
        self.set_font(self.font_family, "B", 17)
        self.set_text_color(*self.brand_rgb)
        self.cell(0, 9, self._t(c.get("brand_name", "WE CARE HOME HEALTHCARE")), align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font(self.font_family, "", 8.5)
        self.set_text_color(60, 60, 60)
        lines = []
        if c.get("address"):
            lines.append(c["address"])
        contact = ", ".join(x for x in [f"Mobile: {c['mobile']}" if c.get("mobile") else "", f"Email: {c['email']}" if c.get("email") else ""] if x)
        if contact:
            lines.append(contact)
        if c.get("website"):
            lines.append(str(c["website"]))
        if c.get("gstin"):
            lines.append(f"GSTIN: {c['gstin']}")
        for line in lines:
            self.cell(0, 4.5, self._t(line), align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*self.brand_rgb)
        self.set_line_width(0.8)
        self.line(12, self.get_y() + 2, 198, self.get_y() + 2)
        self.ln(6)

    def footer(self):
        self.set_y(-20)
        self.set_font(self.font_family, "", 7.5)
        self.set_text_color(120, 120, 120)
        note = self.company.get("footer_note") or f"Generated by {self.company.get('brand_name', '')} billing software"
        self.cell(0, 4, self._t(note), align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 4, f"Page {self.page_no()}/{{nb}}", align="C")

    def title_band(self, title: str, number: str, date: str):
        self.set_fill_color(*self.brand_rgb)
        self.set_text_color(255, 255, 255)
        self.set_font(self.font_family, "B", 12)
        self.cell(0, 9, self._t(title), align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(40, 40, 40)
        self.set_font(self.font_family, "", 9)
        self.cell(95, 6, self._t(f"Document No: {number}"), new_x="END", new_y="LAST")
        self.cell(0, 6, self._t(f"Date: {format_date(date)}"), align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def patient_block(self, patient: dict, extra: dict | None = None):
        self.set_font(self.font_family, "B", 9)
        self.set_text_color(*self.brand_rgb)
        self.cell(0, 6, "PATIENT DETAILS", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(40, 40, 40)
        self.set_font(self.font_family, "", 9)
        rows = [
            ("Patient", patient.get("patient_name", "")),
            ("Patient ID", patient.get("patient_id", "")),
            ("Mobile", patient.get("mobile", "")),
            ("Address", patient.get("address", "")),
            ("Doctor", patient.get("doctor_name", "")),
            ("Attendant", patient.get("attendant_name", "")),
        ]
        if extra:
            rows.extend(list(extra.items()))
        for label, val in rows:
            if val in (None, ""):
                continue
            self.set_font(self.font_family, "B", 9)
            self.cell(32, 5.5, self._t(label + ":"), new_x="END", new_y="LAST")
            self.set_font(self.font_family, "", 9)
            self.multi_cell(0, 5.5, self._t(val), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def items_table(self, headers: list[str], rows: list[list[str]], widths: list[float], aligns: list[str] | None = None):
        aligns = aligns or ["L"] * len(headers)
        self.set_font(self.font_family, "B", 8.5)
        self.set_fill_color(*self.brand_soft)
        self.set_text_color(*self.brand_rgb)
        for h, w, a in zip(headers, widths, aligns):
            self.cell(w, 7, self._t(h), border=1, align=a, fill=True)
        self.ln()
        self.set_font(self.font_family, "", 8.5)
        self.set_text_color(30, 30, 30)
        for row in rows:
            h = 6.5
            if self.get_y() + h > 270:
                self.add_page()
            for val, w, a in zip(row, widths, aligns):
                self.cell(w, h, self._t(val), border=1, align=a)
            self.ln()

    def totals_block(self, pairs: list[tuple[str, str]], grand_label: str = "GRAND TOTAL", grand_value: str = ""):
        self.ln(1)
        x = 118
        self.set_font(self.font_family, "", 9)
        self.set_text_color(30, 30, 30)
        for label, val in pairs:
            self.set_x(x)
            self.cell(40, 6, self._t(label), new_x="END", new_y="LAST")
            self.cell(0, 6, self._t(val), align="R", new_x="LMARGIN", new_y="NEXT")
        if grand_value:
            self.set_x(x)
            self.set_font(self.font_family, "B", 11)
            self.set_text_color(*self.brand_rgb)
            self.cell(40, 8, self._t(grand_label), new_x="END", new_y="LAST")
            self.cell(0, 8, self._t(grand_value), align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def bank_terms_signature(self, company: dict):
        lines = company.get("bank_lines") or bank_lines(company)
        qr = str(company.get("upi_qr") or "")
        qr_path = Path(UPLOAD_DIR) / qr if qr else None
        has_qr = bool(qr_path and qr_path.exists())
        if lines or has_qr:
            self.set_font(self.font_family, "B", 9)
            self.set_text_color(*self.brand_rgb)
            self.cell(0, 6, "PAYMENT DETAILS", new_x="LMARGIN", new_y="NEXT")
            self.set_font(self.font_family, "", 8.5)
            self.set_text_color(40, 40, 40)
            top = self.get_y()
            width = 140 if has_qr else 0
            for line in lines:
                self.multi_cell(width, 5, self._t(line), new_x="LMARGIN", new_y="NEXT")
            if has_qr:
                bottom = self.get_y()
                try:
                    self.image(str(qr_path), x=160, y=top, h=28)
                    self.set_xy(160, top + 29)
                    self.set_font(self.font_family, "", 7.5)
                    self.set_text_color(110, 110, 110)
                    self.cell(28, 4, "Scan to pay", align="C", new_x="LMARGIN", new_y="NEXT")
                except Exception:
                    pass
                self.set_y(max(bottom, top + 34))
            self.ln(1)
        if company.get("terms"):
            self.set_font(self.font_family, "B", 9)
            self.set_text_color(*self.brand_rgb)
            self.cell(0, 6, "TERMS & CONDITIONS", new_x="LMARGIN", new_y="NEXT")
            self.set_font(self.font_family, "", 8)
            self.set_text_color(60, 60, 60)
            self.multi_cell(0, 4.5, self._t(company["terms"]), new_x="LMARGIN", new_y="NEXT")
            self.ln(2)
        if company.get("declaration"):
            self.set_font(self.font_family, "", 7.5)
            self.set_text_color(90, 90, 90)
            self.multi_cell(0, 4.2, self._t(company["declaration"]), new_x="LMARGIN", new_y="NEXT")
            self.ln(1)
        self.ln(4)
        self.set_font(self.font_family, "", 9)
        self.set_text_color(40, 40, 40)
        self.cell(0, 5, self._t(f"For {company.get('brand_name', '')}"), align="R", new_x="LMARGIN", new_y="NEXT")
        sign = str(company.get("signature_image") or "")
        sign_path = Path(UPLOAD_DIR) / sign if sign else None
        if sign_path and sign_path.exists():
            try:
                self.image(str(sign_path), x=150, y=self.get_y() + 1, h=14)
                self.ln(16)
            except Exception:
                self.ln(8)
        else:
            self.ln(8)
        self.set_font(self.font_family, "B", 9)
        self.cell(0, 5, self._t(company.get("signature_name", "Authorized Signatory")), align="R")


def save_pdf(doc_type: str, ref_id: str) -> tuple[str | None, str]:
    data, err = generate_pdf_bytes(doc_type, ref_id)
    if not data:
        return None, err
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{doc_type}_{ref_id}_{db.today_str()}.pdf".replace("/", "_")
    path = PDF_DIR / filename
    path.write_bytes(data)
    return str(path), ""
