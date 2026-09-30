"""Document data assembly + PDF generation (fpdf2) using live database data.

All documents use the latest company branding settings.
"""
import io
from pathlib import Path

from fpdf import FPDF

from .. import database as db
from ..config import PDF_DIR, UPLOAD_DIR
from ..utils import format_date, format_inr

FONT_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"
SYSTEM_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
SYSTEM_FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


DEFAULT_BRAND_COLOR = "#174478"


def _hex_to_rgb(value: str, fallback: tuple[int, int, int] = (23, 68, 120)) -> tuple[int, int, int]:
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


def company_profile() -> dict:
    s = db.get_all_settings("company_settings")
    profile = {
        "brand_name": s.get("brand_name", "WE CARE HOME HEALTHCARE"),
        "address": s.get("address", ""),
        "mobile": s.get("mobile", ""),
        "email": s.get("email", ""),
        "website": s.get("website", ""),
        "gstin": s.get("gstin", ""),
        "gst_rate": s.get("gst_rate", "0"),
        "brand_color": s.get("brand_color", "") or DEFAULT_BRAND_COLOR,
        # structured bank fields (new) + legacy free-text fallback
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
        "template": db.get_setting("document_settings", "template", "A"),
    }
    profile["bank_lines"] = bank_lines(profile)
    profile["rgb"] = _hex_to_rgb(profile["brand_color"])
    return profile


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


def _kv(pdf: DocPDF, label: str, value: str):
    pdf.set_font(pdf.font_family, "B", 9)
    pdf.cell(48, 6, pdf._t(label + ":"), new_x="END", new_y="LAST")
    pdf.set_font(pdf.font_family, "", 9)
    pdf.cell(0, 6, pdf._t(value), new_x="LMARGIN", new_y="NEXT")


def generate_pdf_bytes(doc_type: str, ref_id: str) -> tuple[bytes | None, str]:
    ctx = document_context(doc_type, ref_id)
    if not ctx:
        return None, "Document data not found."
    company = ctx["company"]
    pdf = DocPDF(company)
    pdf.alias_nb_pages("{nb}")
    pdf.add_page()
    pdf.title_band(ctx["title"], ctx.get("doc_number", ""), ctx.get("doc_date", ""))

    if doc_type in ("service_invoice", "equipment_invoice", "combined_invoice", "bill"):
        bill, patient = ctx["bill"], ctx["patient"]
        pdf.patient_block(patient)
        services = [i for i in ctx["items"] if i.get("item_type") == "service"]
        equipments = [i for i in ctx["items"] if i.get("item_type") == "equipment"]
        widths = [10, 78, 22, 28, 24, 24]
        headers = ["#", "Description", "Qty", "Rate", "Discount", "Amount"]
        aligns = ["C", "L", "C", "R", "R", "R"]
        n = 0
        for section, items in (("SERVICES", services), ("EQUIPMENT", equipments)):
            if not items:
                continue
            pdf.set_font(pdf.font_family, "B", 9)
            pdf.set_text_color(*pdf.brand_rgb)
            pdf.cell(0, 6, section, new_x="LMARGIN", new_y="NEXT")
            rows = []
            for it in items:
                n += 1
                desc = str(it.get("description", ""))
                if it.get("start_date") or it.get("end_date"):
                    desc += f" ({format_date(it.get('start_date'))} - {format_date(it.get('end_date'))})"
                if it.get("serial_number"):
                    desc += f" [S/N: {it.get('serial_number')}]"
                rows.append([str(n), desc[:90], str(it.get("quantity")),
                             format_inr(it.get("rate")), format_inr(it.get("discount")), format_inr(it.get("amount"))])
            pdf.items_table(headers, rows, widths, aligns)
            pdf.ln(1)
        pdf.totals_block([
            ("Subtotal", format_inr(bill.get("subtotal"))),
            ("Discount", format_inr(bill.get("discount"))),
            (f"Tax ({bill.get('tax_rate')}%)", format_inr(bill.get("tax"))),
            ("Damage Charges", format_inr(bill.get("damage_charges"))),
            ("Loss Charges", format_inr(bill.get("loss_charges"))),
            ("Other Charges", format_inr(bill.get("other_charges"))),
            ("Security Deposit (refundable)", format_inr(bill.get("deposit"))),
            ("Received", format_inr(float(bill.get("received_amount", 0)) - float(bill.get("refunded_amount", 0)))),
            ("Pending", format_inr(bill.get("remaining_amount"))),
            ("Status", str(bill.get("status"))),
        ], grand_value=format_inr(bill.get("grand_total")))
    elif doc_type == "payment_receipt":
        p, bill, patient = ctx["payment"], ctx["bill"], ctx["patient"]
        pdf.patient_block(patient)
        for label, val in [
            ("Receipt No", p.get("payment_id", "")), ("Bill No", bill.get("bill_number", "")),
            ("Amount", format_inr(p.get("amount"))), ("Method", p.get("method", "")),
            ("Transaction ID", p.get("transaction_id", "") or "-"), ("Received By", p.get("received_by", "")),
            ("Notes", p.get("notes", "") or "-"),
        ]:
            _kv(pdf, label, val)
        pdf.ln(2)
    elif doc_type == "deposit_receipt":
        d, patient = ctx["deposit"], ctx["patient"]
        pdf.patient_block(patient)
        for label, val in [
            ("Receipt No", d.get("deposit_id", "")), ("Purpose", d.get("purpose", "")),
            ("Amount", format_inr(d.get("amount"))), ("Method", d.get("method", "")),
            ("Transaction ID", d.get("transaction_id", "") or "-"),
            ("Status", d.get("status", "")), ("Notes", d.get("notes", "") or "-"),
        ]:
            _kv(pdf, label, val)
        pdf.ln(2)
    elif doc_type == "pending_statement":
        pdf.patient_block(ctx["patient"])
        rows = [[str(i + 1), r["bill_number"], format_date(r["bill_date"]), format_inr(r["grand_total"]),
                 format_inr(r["received"]), format_inr(r["pending"]), r["status"]]
                for i, r in enumerate(ctx["rows"])]
        pdf.items_table(["#", "Bill No", "Date", "Total", "Received", "Pending", "Status"],
                        rows, [10, 48, 24, 30, 30, 30, 14],
                        ["C", "L", "C", "R", "R", "R", "C"])
        pdf.totals_block([], grand_label="TOTAL PENDING", grand_value=format_inr(ctx["total_pending"]))
    elif doc_type in ("equipment_issue", "equipment_return"):
        t, eq, patient = ctx["txn"], ctx["equipment"], ctx["patient"]
        pdf.patient_block(patient)
        for label, val in [
            ("Equipment", f"{eq.get('equipment_name', '')} ({eq.get('equipment_id', '')})"),
            ("Serial Number", t.get("serial_number", "") or "-"),
            ("Issue Date", format_date(t.get("issue_date"))),
            ("Expected Return", format_date(t.get("expected_return_date"))),
            ("Return Date", format_date(t.get("return_date"))),
            ("Condition", t.get("condition", "") or "-"),
            ("Rent Amount", format_inr(t.get("rent_amount"))),
            ("Security Deposit", format_inr(t.get("deposit"))),
            ("Damage Charge", format_inr(t.get("damage_charge"))),
            ("Loss Charge", format_inr(t.get("loss_charge"))),
            ("Refundable", format_inr(t.get("refund_amount"))),
        ]:
            _kv(pdf, label, val)
        pdf.ln(2)
    elif doc_type == "service_agreement":
        bill, patient = ctx["bill"], ctx["patient"]
        pdf.patient_block(patient)
        _kv(pdf, "Agreement Ref", ctx.get("doc_number", ""))
        _kv(pdf, "Bill No", bill.get("bill_number", ""))
        _kv(pdf, "Bill Amount", format_inr(bill.get("grand_total")))
        pdf.ln(2)
        pdf.set_font(pdf.font_family, "", 9)
        pdf.set_text_color(40, 40, 40)
        terms = company.get("terms") or "Services will be provided as per the agreed schedule."
        pdf.multi_cell(0, 5.5, pdf._t(terms), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    elif doc_type == "patient_sheet":
        pdf.patient_block(ctx["patient"])
        if ctx["services"]:
            rows = [[s.get("service_name", ""), format_date(s.get("start_date")), format_date(s.get("end_date")),
                     str(s.get("quantity")), format_inr(s.get("amount"))] for s in ctx["services"]]
            pdf.set_font(pdf.font_family, "B", 9)
            pdf.set_text_color(*pdf.brand_rgb)
            pdf.cell(0, 6, "SERVICES", new_x="LMARGIN", new_y="NEXT")
            pdf.items_table(["Service", "Start", "End", "Qty", "Amount"], rows, [76, 28, 28, 22, 32],
                            ["L", "C", "C", "C", "R"])
        if ctx["txns"]:
            rows = [[str(t.get("equipment_id")), str(t.get("txn_type")), format_date(t.get("issue_date")),
                     format_date(t.get("return_date")) or "-"] for t in ctx["txns"]]
            pdf.set_font(pdf.font_family, "B", 9)
            pdf.set_text_color(*pdf.brand_rgb)
            pdf.cell(0, 6, "EQUIPMENT", new_x="LMARGIN", new_y="NEXT")
            pdf.items_table(["Equipment ID", "Type", "Issue", "Return"], rows, [60, 40, 43, 43],
                            ["L", "C", "C", "C"])
        if ctx["bills"]:
            rows = [[b.get("bill_number", ""), format_date(b.get("bill_date")), format_inr(b.get("grand_total")),
                     str(b.get("status"))] for b in ctx["bills"]]
            pdf.set_font(pdf.font_family, "B", 9)
            pdf.set_text_color(*pdf.brand_rgb)
            pdf.cell(0, 6, "BILLS", new_x="LMARGIN", new_y="NEXT")
            pdf.items_table(["Bill No", "Date", "Total", "Status"], rows, [66, 40, 44, 36],
                            ["L", "C", "R", "C"])
        pdf.ln(2)
    elif doc_type == "monthly_statement":
        pdf.patient_block(ctx["patient"], {"Month": ctx["month"]})
        rows = [[b.get("bill_number", ""), format_date(b.get("bill_date")), format_inr(b.get("grand_total")),
                 format_inr(float(b.get("received_amount", 0)) - float(b.get("refunded_amount", 0))),
                 format_inr(b.get("remaining_amount")), str(b.get("status"))] for b in ctx["rows"]]
        pdf.items_table(["Bill No", "Date", "Total", "Received", "Pending", "Status"], rows,
                        [48, 24, 30, 30, 30, 24], ["L", "C", "R", "R", "R", "C"])
        pdf.totals_block([], grand_label="MONTH TOTAL", grand_value=format_inr(ctx["total"]))
    elif doc_type == "quotation":
        q = ctx["quotation"]
        pdf.patient_block(ctx["patient"] or {"patient_name": q.get("customer_name", "")},
                          {"Valid Until": format_date(q.get("valid_until"))})
        rows = []
        for i, it in enumerate(ctx["items"], start=1):
            rows.append([str(i), str(it.get("description", ""))[:90], str(it.get("quantity")),
                         format_inr(it.get("rate")), format_inr(it.get("discount", 0)), format_inr(it.get("amount", 0))])
        pdf.items_table(["#", "Description", "Qty", "Rate", "Discount", "Amount"], rows,
                        [10, 78, 22, 28, 24, 24], ["C", "L", "C", "R", "R", "R"])
        pdf.totals_block([
            ("Subtotal", format_inr(q.get("subtotal"))),
            ("Discount", format_inr(q.get("discount"))),
            (f"Tax ({q.get('tax_rate')}%)", format_inr(q.get("tax"))),
        ], grand_label="ESTIMATED TOTAL", grand_value=format_inr(q.get("grand_total")))
        if q.get("terms"):
            pdf.set_font(pdf.font_family, "", 8.5)
            pdf.multi_cell(0, 5, pdf._t("Terms: " + str(q.get("terms"))), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
    else:
        return None, "Unknown document type."

    pdf.bank_terms_signature(company)
    buf = io.BytesIO(pdf.output())
    return buf.getvalue(), ""


def save_pdf(doc_type: str, ref_id: str) -> tuple[str | None, str]:
    data, err = generate_pdf_bytes(doc_type, ref_id)
    if not data:
        return None, err
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{doc_type}_{ref_id}_{db.today_str()}.pdf".replace("/", "_")
    path = PDF_DIR / filename
    path.write_bytes(data)
    return str(path), ""
