"""Printable documents + PDF generation + WhatsApp helpers."""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import database as db
from ..audit import log_activity
from ..dependencies import require_permission, template_context
from ..services.document_service import build_doc, company_profile, document_context, generate_pdf_bytes
from ..utils import client_ip, flash, whatsapp_link

router = APIRouter(tags=["documents"])

DOC_TITLES = {
    "service_invoice": "Service Invoice",
    "equipment_invoice": "Equipment Rental Invoice",
    "combined_invoice": "Combined Invoice",
    "bill": "Invoice",
    "payment_receipt": "Payment Receipt",
    "deposit_receipt": "Advance / Deposit Receipt",
    "pending_statement": "Pending Payment Statement",
    "equipment_issue": "Equipment Handover Receipt",
    "equipment_return": "Equipment Return Receipt",
    "service_agreement": "Service Agreement",
    "patient_sheet": "Patient Service Details Sheet",
    "monthly_statement": "Monthly Bill Statement",
    "quotation": "Quotation / Estimate",
}


@router.get("/documents", response_class=HTMLResponse)
def document_home(request: Request, user: dict = Depends(require_permission("print_documents"))):
    from ..main import templates
    bills = db.read_table("bills")
    recent_bills = []
    if not bills.empty:
        bills = bills.sort_values("created_at", ascending=False).head(10)
        patients = db.read_table("patients")
        names = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
        for _, b in bills.iterrows():
            recent_bills.append({**b.to_dict(), "patient_name": names.get(str(b["patient_id"]), "-")})
    patients = db.read_table("patients")
    patients = patients.sort_values("patient_name").to_dict("records") if not patients.empty else []
    payments = db.read_table("payments")
    recent_payments = payments.sort_values("created_at", ascending=False).head(10).to_dict("records") if not payments.empty else []
    deposits = db.read_table("deposits")
    recent_deposits = deposits.sort_values("created_at", ascending=False).head(10).to_dict("records") if not deposits.empty else []
    txns = db.read_table("equipment_transactions")
    recent_txns = txns.sort_values("created_at", ascending=False).head(10).to_dict("records") if not txns.empty else []
    quotations = db.read_table("quotations")
    recent_quotations = quotations.sort_values("created_at", ascending=False).head(10).to_dict("records") if not quotations.empty else []
    return templates.TemplateResponse(request, "documents/home.html", template_context(request, {
        "active_nav": "documents", "recent_bills": recent_bills, "patients": patients,
        "recent_payments": recent_payments, "recent_deposits": recent_deposits,
        "recent_txns": recent_txns, "recent_quotations": recent_quotations,
    }))


@router.get("/documents/{doc_type}/{ref_id}/print", response_class=HTMLResponse)
def document_print(request: Request, doc_type: str, ref_id: str,
                   user: dict = Depends(require_permission("print_documents"))):
    """Browser print view — the exact A4 design that the PDF also produces."""
    from ..main import templates
    if doc_type not in DOC_TITLES:
        flash(request, "Unknown document type.", "error")
        return RedirectResponse(url="/documents", status_code=303)
    doc, err = build_doc(doc_type, ref_id)
    if not doc:
        flash(request, err or "Document data not found.", "error")
        return RedirectResponse(url="/documents", status_code=303)
    log_activity(user=user, action="PRINT_DOCUMENT", entity_type="document", entity_id=ref_id,
                 description=f"Printed {DOC_TITLES[doc_type]} ({doc.get('doc_number')})", ip=client_ip(request))
    referer = str(request.headers.get("referer") or "")
    back_url = referer if referer.startswith(str(request.base_url)) else "/documents"
    return templates.TemplateResponse(request, "documents/print.html", template_context(request, {
        "active_nav": "documents", "doc_type": doc_type, "title": DOC_TITLES[doc_type],
        "ref_id": ref_id, "doc": doc, "asset_base": "/static/uploads/", "back_url": back_url,
    }))


@router.get("/documents/{doc_type}/{ref_id}/pdf")
def document_pdf(request: Request, doc_type: str, ref_id: str,
                 download: int = Query(1), user: dict = Depends(require_permission("generate_pdf"))):
    if doc_type not in DOC_TITLES:
        flash(request, "Unknown document type.", "error")
        return RedirectResponse(url="/documents", status_code=303)
    # Also require print permission implicitly? No — generate_pdf suffices.
    data, err = generate_pdf_bytes(doc_type, ref_id)
    if not data:
        flash(request, err or "Unable to generate PDF.", "error")
        return RedirectResponse(url="/documents", status_code=303)
    log_activity(user=user, action="GENERATE_PDF", entity_type="document", entity_id=ref_id,
                 description=f"Generated PDF for {DOC_TITLES[doc_type]} ({ref_id})", ip=client_ip(request))
    filename = f"{doc_type}_{ref_id}.pdf".replace("/", "_")
    disp = "attachment" if download else "inline"
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="{filename}"'})


@router.get("/documents/{doc_type}/{ref_id}/whatsapp", response_class=HTMLResponse)
def document_whatsapp(request: Request, doc_type: str, ref_id: str,
                      user: dict = Depends(require_permission("share_whatsapp"))):
    from ..main import templates
    if doc_type not in DOC_TITLES:
        flash(request, "Unknown document type.", "error")
        return RedirectResponse(url="/documents", status_code=303)
    ctx = document_context(doc_type, ref_id)
    if not ctx:
        flash(request, "Document data not found.", "error")
        return RedirectResponse(url="/documents", status_code=303)
    company = ctx["company"]
    patient = ctx.get("patient", {}) or {}
    lines = [
        f"*{company.get('brand_name', '')}*",
        f"{ctx.get('title', '')}: {ctx.get('doc_number', '')}",
        f"Date: {ctx.get('doc_date', '')}",
    ]
    if patient.get("patient_name"):
        lines.append(f"Patient: {patient.get('patient_name')}")
    bill = ctx.get("bill")
    if bill:
        lines.append(f"Amount: Rs. {float(bill.get('grand_total', 0)):.2f}")
        lines.append(f"Pending: Rs. {float(bill.get('remaining_amount', 0)):.2f}")
    if ctx.get("payment"):
        lines.append(f"Paid: Rs. {float(ctx['payment'].get('amount', 0)):.2f}")
    if ctx.get("quotation"):
        lines.append(f"Estimate: Rs. {float(ctx['quotation'].get('grand_total', 0)):.2f}")
    lines.append(f"Contact: {company.get('mobile', '')}")
    message = "\n".join(lines)
    link = whatsapp_link(str(patient.get("mobile", "")), message)
    log_activity(user=user, action="SHARE_WHATSAPP", entity_type="document", entity_id=ref_id,
                 description=f"Prepared WhatsApp share for {DOC_TITLES[doc_type]} ({ctx.get('doc_number')})",
                 ip=client_ip(request))
    return templates.TemplateResponse(request, "documents/whatsapp.html", template_context(request, {
        "active_nav": "documents", "doc_type": doc_type, "title": DOC_TITLES[doc_type],
        "ref_id": ref_id, "ctx": ctx, "message": message, "wa_link": link,
        "has_phone": bool(patient.get("mobile")),
    }))
