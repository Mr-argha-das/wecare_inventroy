"""The one invoice design: model, HTML print view and PDF download.

Every billing document goes through ``document_service`` ->
``invoice_layout.build_document`` -> (``_invoice_pages.html`` | ``invoice_pdf``),
so these tests check the shared model, that the browser page and the PDF stay
in sync, and that Settings -> Invoice Design really drives the output.
"""
import pytest

from app import database as db
from app.services import billing_service, document_service, invoice_layout, payment_service

ADMIN = {"username": "admin", "role": "admin", "staff_id": "WC-ST-000001", "full_name": "Administrator"}


@pytest.fixture()
def client(admin_user):
    """Logged-in test client (same flow a user goes through in the browser)."""
    import re
    from fastapi.testclient import TestClient
    from app.main import app

    test_client = TestClient(app)
    page = test_client.get("/auth/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    res = test_client.post("/auth/login", data={"username": "admin", "password": "Admin@123",
                                                "csrf_token": token}, follow_redirects=True)
    assert res.status_code == 200
    return test_client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def company(admin_user):
    """Company / bank / design settings as an admin would enter them."""
    values = {
        "brand_name": "We Care Home Healthcare",
        "address": "432/ 4th floor, Cityscape Complex\nAhmedabad.",
        "mobile": "9929144275",
        "email": "wecare83@gmail.com",
        "gstin": "08BGLPG814P1Z7",
        "header_note": "We Care Home Healthcare: 006622001300341",
        "bank_name": "Kotak Mahindra Bank Limited",
        "branch": "Prahladnagar Branch",
        "account_number": "8432144275",
        "ifsc": "KKBK0002560",
        "account_name": "WE Care360 Global Home Healthcare",
        "terms": "1.The deposit will be refunded upon service closure.",
        "logo": "logo.png",
        "upi_qr": "upiqr.png",
        "footer_text": "www.wecare360.in",
    }
    for key, value in values.items():
        db.set_setting("company_settings", key, value)
    return values


@pytest.fixture()
def patient(admin_user):
    return db.append_record("patients", {
        "patient_id": db.generate_patient_id(), "patient_name": "Khalid bhai",
        "mobile": "8511110605", "address": "Satellite, Ahmedabad",
        "created_at": db.now_iso(), "updated_at": db.now_iso(), "status": "Active"})


def _bill(patient, items=None, **kwargs):
    items = items or [
        {"item_type": "service", "ref_id": "", "description": "Nurse Care 24hrs",
         "start_date": "2026-09-01", "end_date": "2026-09-15", "billing_type": "Per Day",
         "unit_type": "days", "quantity": 0, "days": 15, "rate": 1700, "discount": 0},
        {"item_type": "service", "ref_id": "", "description": "Care taker (24hrs)",
         "start_date": "2026-09-01", "end_date": "2026-09-15", "billing_type": "Per Day",
         "unit_type": "days", "quantity": 0, "days": 15, "rate": 950, "discount": 0},
    ]
    payload, errors = billing_service.prepare_bill_payload(
        patient_id=patient["patient_id"], bill_type="service", bill_date="2026-09-16",
        items=items, **kwargs)
    assert not errors, errors
    return billing_service.create_bill(payload, user=ADMIN)


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------
def test_invoice_model_carries_every_printed_field(company, patient):
    bill = _bill(patient)
    doc, err = document_service.build_doc("service_invoice", bill["bill_id"])
    assert err == "" and doc

    assert doc["title"] == "Tax Invoice"
    assert doc["company"]["brand_name"] == "We Care Home Healthcare"
    assert "Phone no : 9929144275 Email: wecare83@gmail.com" in doc["company"]["header_lines"]
    assert doc["company"]["header_lines"][-1] == "We Care Home Healthcare: 006622001300341"

    assert doc["bill_to"]["name"] == "Khalid bhai"
    assert "Contact No : 8511110605" in doc["bill_to"]["lines"]

    details = dict(doc["details"]["rows"])
    assert details["Invoice No."] == bill["bill_number"]
    assert details["Date"] == "16-09-2026"
    assert "Time" in details and "PO date" in details

    labels = [c["label"] for c in doc["table"]["columns"]]
    assert labels == ["Services/Equipment", "Starting Date", "Till Date", "Days", "Price", "Amount"]
    assert sum(c["w"] for c in doc["table"]["columns"]) == 100
    first = doc["table"]["rows"][0]
    assert [c["text"] for c in first] == ["Nurse Care 24hrs", "01/09/2026", "15/09/2026",
                                          "15", "₹ 1,700.0", "₹ 25,500.0"]

    words = [b for b in doc["blocks"] if b["title"] == "Invoice Amount in Words"]
    assert words and "Thirty Nine Thousand Seven Hundred and Fifty" in words[0]["body"]
    assert any(b["title"] == "Terms and conditions" for b in doc["blocks"])

    amounts = {a["label"]: a["value"] for a in doc["amounts"]}
    assert amounts["Sub Total"] == "₹ 39,750.0"
    assert amounts["Total"] == "₹ 39,750.0"
    assert amounts["Received"] == "₹ 0.0"

    assert doc["bank"]["show"] is True
    assert "Account No: 8432144275" in doc["bank"]["lines"]
    assert "IFSC code: KKBK0002560" in doc["bank"]["lines"]
    assert doc["bank"]["qr"] == "upiqr.png"


def test_payment_updates_received_and_payment_type(company, patient):
    bill = _bill(patient)
    payment_service.add_payment(bill_id=bill["bill_id"], patient_id=patient["patient_id"],
                                payment_date="2026-09-18", method="UPI", amount=15000,
                                transaction_id="UPI123", user=ADMIN)
    doc, _ = document_service.build_doc("service_invoice", bill["bill_id"])
    amounts = {a["label"]: a["value"] for a in doc["amounts"]}
    assert amounts["Received"] == "₹ 15,000.0"
    assert amounts["Balance"] == "₹ 24,750.0"
    assert [b["body"] for b in doc["blocks"] if b["title"] == "Payment Type"] == ["UPI"]


def test_gst_discount_and_deposit_rows(company, patient):
    bill = _bill(patient, bill_discount=750, gst_applicable=True, gst_type="CGST_SGST",
                 gst_rate=18, deposit=2000)
    doc, _ = document_service.build_doc("service_invoice", bill["bill_id"])
    labels = [a["label"] for a in doc["amounts"]]
    assert "Discount" in labels and "Taxable Amount" in labels
    assert "CGST (9%)" in labels and "SGST (9%)" in labels
    assert "Security Deposit (refundable)" in labels
    total = [a for a in doc["amounts"] if a["label"] == "Total"][0]
    assert total["strong"] is True


def test_cancelled_bill_is_marked(company, patient):
    bill = _bill(patient)
    db.update_record("bills", bill["bill_id"], {"cancelled": True})
    doc, _ = document_service.build_doc("service_invoice", bill["bill_id"])
    assert ("Status", "CANCELLED") in doc["details"]["rows"]


# ---------------------------------------------------------------------------
# Pagination (identical for the print view and the PDF)
# ---------------------------------------------------------------------------
def test_long_invoice_paginates_with_lower_section_on_last_page(company, patient):
    items = [{"item_type": "service", "ref_id": "", "description": f"Nurse Care 24hrs - cycle {i}",
              "start_date": "2026-09-01", "end_date": "2026-09-15", "billing_type": "Per Day",
              "unit_type": "days", "quantity": 0, "days": 10, "rate": 1200, "discount": 0}
             for i in range(40)]
    bill = _bill(patient, items=items)
    doc, _ = document_service.build_doc("service_invoice", bill["bill_id"])
    pages = doc["pages"]
    assert len(pages) > 1
    assert sum(len(p["rows"]) for p in pages) == 40
    assert [p["show_lower"] for p in pages] == [False] * (len(pages) - 1) + [True]
    assert pages[0]["count"] == len(pages)
    # no page may run past the printable area
    top = invoice_layout.table_top() + invoice_layout.TABLE_HEAD_H
    for page in pages:
        used = sum(invoice_layout.row_height(r, doc["table"]["columns"]) for r in page["rows"])
        assert top + used <= invoice_layout.CONTENT_BOTTOM + 0.01


def test_wrapped_description_makes_the_row_taller():
    columns = invoice_layout.INVOICE_COLUMNS
    short = [invoice_layout.cell("Nurse Care"), invoice_layout.cell("01/09/2026")]
    long = [invoice_layout.cell("Care taker 24hrs with lifting support, night duty allowance "
                                "and weekend cover"), invoice_layout.cell("01/09/2026")]
    assert invoice_layout.row_height(long, columns) > invoice_layout.row_height(short, columns)


# ---------------------------------------------------------------------------
# HTML + PDF output
# ---------------------------------------------------------------------------
def test_print_page_renders_the_approved_design(client, company, patient):
    """The browser print view served by /documents/<type>/<id>/print."""
    bill = _bill(patient)
    page = client.get(f"/documents/service_invoice/{bill['bill_id']}/print")
    assert page.status_code == 200
    html = page.text
    for needle in ["/static/css/invoice_a4.css", "--inv-green: #008d09",
                   'class="invoice-title"', "Tax Invoice", "We Care Home Healthcare",
                   "Bill To", "Khalid bhai", "Invoice Details", bill["bill_number"],
                   "Services/Equipment", "₹ 25,500.0", "Invoice Amount in Words",
                   "Bank Details", "KKBK0002560", "/static/uploads/upiqr.png",
                   "/static/uploads/logo.png", "www.wecare360.in",
                   "Print / Save as PDF"]:
        assert needle in html, needle
    # the print view is stand-alone: no app chrome around the sheet
    assert "<nav" not in html and "app.js" not in html


def test_pdf_route_serves_the_same_document(client, company, patient):
    bill = _bill(patient)
    res = client.get(f"/documents/service_invoice/{bill['bill_id']}/pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content[:5] == b"%PDF-"


def test_invoice_colour_from_settings_reaches_the_document(company, patient):
    db.set_setting("company_settings", "invoice_color", "#123456")
    bill = _bill(patient)
    doc, _ = document_service.build_doc("service_invoice", bill["bill_id"])
    assert doc["company"]["invoice_color"] == "#123456"
    assert "--inv-green: #123456" in document_service.document_html(doc)


def test_switches_hide_optional_blocks(company, patient):
    for key in ("show_qr", "show_po_date", "show_time", "show_page_number"):
        db.set_setting("company_settings", key, "0")
    bill = _bill(patient)
    doc, _ = document_service.build_doc("service_invoice", bill["bill_id"])
    assert doc["bank"]["qr"] == ""
    assert "PO date" not in dict(doc["details"]["rows"])
    assert "Time" not in dict(doc["details"]["rows"])
    assert doc["company"]["show_page_number"] is False


def test_pdf_download_is_a_real_pdf(company, patient):
    bill = _bill(patient)
    data, err = document_service.generate_pdf_bytes("service_invoice", bill["bill_id"])
    assert err == ""
    assert data[:5] == b"%PDF-"
    assert len(data) > 5000


@pytest.mark.parametrize("doc_type", [
    "service_invoice", "combined_invoice", "service_agreement", "payment_receipt",
    "deposit_receipt", "pending_statement", "monthly_statement", "patient_sheet",
])
def test_every_billing_document_renders(company, patient, doc_type):
    bill = _bill(patient)
    pay, _ = payment_service.add_payment(bill_id=bill["bill_id"], patient_id=patient["patient_id"],
                                         payment_date="2026-09-18", method="Cash", amount=1000,
                                         user=ADMIN)
    dep, _ = payment_service.add_deposit(patient_id=patient["patient_id"], bill_id=bill["bill_id"],
                                         deposit_date="2026-09-16", amount=5000, method="Cash",
                                         purpose="Security Deposit", user=ADMIN)
    ref = {"payment_receipt": pay["payment_id"], "deposit_receipt": dep["deposit_id"]}.get(
        doc_type, bill["bill_id"] if "invoice" in doc_type or doc_type == "service_agreement"
        else patient["patient_id"])
    doc, err = document_service.build_doc(doc_type, ref)
    assert err == "" and doc, err
    assert doc["title"]
    html = document_service.document_html(doc)
    assert "--inv-green" in html and doc["title"] in html
    data, err = document_service.generate_pdf_bytes(doc_type, ref)
    assert err == "" and data[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Admin screen: logo / QR / bank details / design are editable
# ---------------------------------------------------------------------------
# a real 2x2 green PNG (upload validation must accept it)
PNG_2PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000020802000000fdd49a73"
    "0000001649444154789c6364e8e56460606062606060606000000729009a925c44"
    "010000000049454e44ae426082")


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    """Keep uploaded test images out of the repository."""
    folder = tmp_path / "uploads"
    folder.mkdir()
    import app.config as config
    import app.routers.settings as settings_router
    import app.services.document_service as doc_service
    import app.services.invoice_pdf as invoice_pdf
    monkeypatch.setattr(config, "UPLOAD_DIR", folder)
    monkeypatch.setattr(settings_router, "UPLOAD_DIR", folder)
    monkeypatch.setattr(doc_service, "UPLOAD_DIR", folder)
    monkeypatch.setattr(invoice_pdf, "UPLOAD_DIR", folder)
    return folder


def _csrf(client, url="/settings"):
    import re
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(url).text).group(1)


def test_admin_can_upload_logo_and_qr(client, uploads, company, patient):
    for key, stored in (("logo", "logo"), ("upi_qr", "upiqr")):
        res = client.post(f"/settings/image/{key}",
                          data={"csrf_token": _csrf(client)},
                          files={"image": ("brand.png", PNG_2PX, "image/png")},
                          follow_redirects=True)
        assert res.status_code == 200
        saved = db.get_setting("company_settings", key, "")
        assert saved == stored + ".png"
        assert (uploads / saved).read_bytes() == PNG_2PX
    bill = _bill(patient)
    page = client.get(f"/documents/service_invoice/{bill['bill_id']}/print")
    assert "/static/uploads/" + db.get_setting("company_settings", "logo", "") in page.text
    assert "/static/uploads/" + db.get_setting("company_settings", "upi_qr", "") in page.text


def test_admin_can_update_bank_details(client, company, patient):
    res = client.post("/settings/company", data={
        "csrf_token": _csrf(client), "brand_name": "We Care Home Healthcare",
        "bank_name": "HDFC Bank", "branch": "Prahladnagar", "account_number": "50200012345678",
        "ifsc": "HDFC0000123", "account_name": "We Care 360", "upi_details": "wecare@hdfc",
    }, follow_redirects=True)
    assert res.status_code == 200
    bill = _bill(patient)
    doc, _ = document_service.build_doc("service_invoice", bill["bill_id"])
    assert "Name: HDFC Bank, Prahladnagar" in doc["bank"]["lines"]
    assert "Account No: 50200012345678" in doc["bank"]["lines"]
    assert "IFSC code: HDFC0000123" in doc["bank"]["lines"]
    assert "UPI: wecare@hdfc" in doc["bank"]["lines"]


def test_admin_can_update_invoice_design(client, company, patient):
    res = client.post("/settings/invoice-design", data={
        "csrf_token": _csrf(client), "invoice_title": "GST Invoice",
        "header_note": "Care without limits", "footer_text": "www.wecare360.in",
        "invoice_color": "#0b5ed7", "link_color": "#ff6600",
        "show_time": "1", "show_qr": "1",          # po date + page number left off
    }, follow_redirects=True)
    assert res.status_code == 200
    bill = _bill(patient)
    html = client.get(f"/documents/service_invoice/{bill['bill_id']}/print").text
    assert "GST Invoice" in html
    assert "Care without limits" in html
    assert "--inv-green: #0b5ed7" in html
    assert "--inv-link: #ff6600" in html
    assert "PO date" not in html
    assert 'class="page-no"' not in html
