"""Payments, deposits/advances, refunds."""
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import database as db
from ..dependencies import require_permission, template_context
from ..security import validate_csrf
from ..services import payment_service
from ..services.payment_service import PAYMENT_METHODS
from ..utils import client_ip, flash, paginate

router = APIRouter(tags=["payments"])


@router.get("/payments", response_class=HTMLResponse)
def payment_list(
    request: Request, page: int = Query(1), search: str = Query(""), method: str = Query(""),
    start: str = Query(""), end: str = Query(""),
    user: dict = Depends(require_permission("view_payments")),
):
    from ..main import templates
    df = db.read_table("payments")
    bills = db.read_table("bills")
    bnums = {str(r["bill_id"]): str(r["bill_number"]) for _, r in bills.iterrows()} if not bills.empty else {}
    patients = db.read_table("patients")
    pnames = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    if not df.empty:
        df = df.sort_values("payment_date", ascending=False)
        if search:
            s = search.lower()
            df = df[
                df["payment_id"].astype(str).str.lower().str.contains(s, na=False)
                | df["transaction_id"].astype(str).str.lower().str.contains(s, na=False)
                | df["bill_id"].astype(str).map(lambda b: bnums.get(str(b), "").lower()).str.contains(s, na=False)
                | df["patient_id"].astype(str).map(lambda p: pnames.get(str(p), "").lower()).str.contains(s, na=False)
            ]
        if method:
            df = df[df["method"].astype(str) == method]
        if start:
            df = df[df["payment_date"].astype(str) >= start]
        if end:
            df = df[df["payment_date"].astype(str) <= end]
    total = len(df)
    pg = paginate(total, page, 20)
    rows = []
    if total:
        for _, r in df.iloc[pg["start"]:pg["end"]].iterrows():
            d = r.to_dict()
            d["bill_number"] = bnums.get(str(d["bill_id"]), "-")
            d["patient_name"] = pnames.get(str(d["patient_id"]), "-")
            rows.append(d)
    collected = round(float(df["amount"].sum()) if total else 0.0, 2)
    return templates.TemplateResponse(request, "payments/list.html", template_context(request, {
        "active_nav": "payments", "rows": rows, "pg": pg, "search": search, "method": method,
        "start": start, "end": end, "collected": collected, "methods": PAYMENT_METHODS,
    }))


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
def payment_detail(request: Request, payment_id: str, user: dict = Depends(require_permission("view_payments"))):
    from ..main import templates
    payment = db.get_record("payments", payment_id)
    if not payment:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Payment not found")
    bill = db.get_record("bills", str(payment.get("bill_id", ""))) or {}
    patient = db.get_record("patients", str(payment.get("patient_id", ""))) or {}
    refunds = db.find_records("refunds", payment_id=payment_id).to_dict("records")
    return templates.TemplateResponse(request, "payments/detail.html", template_context(request, {
        "active_nav": "payments", "payment": payment, "bill": bill, "patient": patient,
        "refunds": refunds, "today": db.today_str(),
    }))


@router.post("/payments/{payment_id}/refund")
def payment_refund(
    request: Request, payment_id: str,
    amount: str = Form("0"),
    method: str = Form("Cash"),
    reason: str = Form(""),
    transaction_id: str = Form(""),
    refund_date: str = Form(""),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("refund_payment")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url=f"/payments/{payment_id}", status_code=303)
    payment = db.get_record("payments", payment_id)
    if not payment:
        flash(request, "Payment not found.", "error")
        return RedirectResponse(url="/payments", status_code=303)
    try:
        amt = float(amount or 0)
    except Exception:
        amt = 0
    refund, errors = payment_service.create_refund(
        bill_id=str(payment.get("bill_id", "")), payment_id=payment_id,
        patient_id=str(payment.get("patient_id", "")),
        refund_date=refund_date or db.today_str(), amount=amt, method=method,
        reason=reason.strip(), transaction_id=transaction_id.strip(),
        user=user, ip=client_ip(request),
    )
    if errors:
        for e in errors:
            flash(request, e, "error")
    else:
        flash(request, "Refund processed successfully.")
    return RedirectResponse(url=f"/payments/{payment_id}", status_code=303)


@router.get("/deposits", response_class=HTMLResponse)
def deposit_list(
    request: Request, page: int = Query(1), search: str = Query(""), status: str = Query(""),
    user: dict = Depends(require_permission("view_payments")),
):
    from ..main import templates
    df = db.read_table("deposits")
    patients = db.read_table("patients")
    pnames = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    if not df.empty:
        df = df.sort_values("deposit_date", ascending=False)
        if search:
            s = search.lower()
            df = df[
                df["deposit_id"].astype(str).str.lower().str.contains(s, na=False)
                | df["patient_id"].astype(str).map(lambda p: pnames.get(str(p), "").lower()).str.contains(s, na=False)
            ]
        if status:
            df = df[df["status"].astype(str) == status]
    total = len(df)
    pg = paginate(total, page, 20)
    rows = []
    if total:
        for _, r in df.iloc[pg["start"]:pg["end"]].iterrows():
            d = r.to_dict()
            d["patient_name"] = pnames.get(str(d["patient_id"]), "-")
            d["available"] = round(float(d.get("amount", 0)) - float(d.get("adjusted_amount", 0)) - float(d.get("refunded_amount", 0)), 2)
            rows.append(d)
    held = round(sum(r["available"] for r in rows), 2)
    all_patients = db.read_table("patients")
    all_patients = all_patients[all_patients["status"].astype(str) == "Active"].sort_values("patient_name").to_dict("records") if not all_patients.empty else []
    bills_df = db.read_table("bills")
    pending_bills: dict[str, list[dict]] = {}
    if not bills_df.empty:
        open_bills = bills_df[(~bills_df["cancelled"].astype(bool)) & (bills_df["remaining_amount"].astype(float) > 0.005)]
        for _, b in open_bills.sort_values("bill_date", ascending=False).iterrows():
            pending_bills.setdefault(str(b["patient_id"]), []).append({
                "bill_id": str(b["bill_id"]), "bill_number": str(b["bill_number"]),
                "remaining": float(b["remaining_amount"]),
            })
    return templates.TemplateResponse(request, "payments/deposits.html", template_context(request, {
        "active_nav": "payments", "rows": rows, "pg": pg, "search": search, "status": status,
        "held": held, "patients": all_patients, "today": db.today_str(), "methods": PAYMENT_METHODS,
        "pending_bills": pending_bills,
    }))


@router.post("/deposits/new")
def deposit_create(
    request: Request,
    patient_id: str = Form(""),
    deposit_date: str = Form(""),
    amount: str = Form("0"),
    method: str = Form("Cash"),
    transaction_id: str = Form(""),
    purpose: str = Form("Advance"),
    notes: str = Form(""),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("create_payment")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/deposits", status_code=303)
    try:
        amt = float(amount or 0)
    except Exception:
        amt = 0
    deposit, errors = payment_service.add_deposit(
        patient_id=patient_id, deposit_date=deposit_date or db.today_str(), amount=amt,
        method=method, transaction_id=transaction_id.strip(), purpose=purpose,
        notes=notes.strip(), user=user, ip=client_ip(request),
    )
    if errors:
        for e in errors:
            flash(request, e, "error")
    else:
        flash(request, "Deposit recorded successfully.")
    return RedirectResponse(url="/deposits", status_code=303)


@router.post("/deposits/{deposit_id}/adjust")
def deposit_adjust(
    request: Request, deposit_id: str,
    bill_id: str = Form(""),
    amount: str = Form("0"),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("create_payment")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/deposits", status_code=303)
    try:
        amt = float(amount or 0)
    except Exception:
        amt = 0
    _, errors = payment_service.adjust_deposit_to_bill(deposit_id, bill_id, amt, user=user, ip=client_ip(request))
    if errors:
        for e in errors:
            flash(request, e, "error")
    else:
        flash(request, "Deposit adjusted to bill successfully.")
    return RedirectResponse(url="/deposits", status_code=303)


@router.post("/deposits/{deposit_id}/refund")
def deposit_refund(
    request: Request, deposit_id: str,
    amount: str = Form("0"),
    method: str = Form("Cash"),
    reason: str = Form(""),
    transaction_id: str = Form(""),
    csrf_token: str = Form(""),
    user: dict = Depends(require_permission("refund_payment")),
):
    if not validate_csrf(request.session, csrf_token):
        flash(request, "Session expired. Please try again.", "error")
        return RedirectResponse(url="/deposits", status_code=303)
    deposit = db.get_record("deposits", deposit_id)
    if not deposit:
        flash(request, "Deposit not found.", "error")
        return RedirectResponse(url="/deposits", status_code=303)
    try:
        amt = float(amount or 0)
    except Exception:
        amt = 0
    _, errors = payment_service.create_refund(
        deposit_id=deposit_id, bill_id=str(deposit.get("bill_id", "")),
        patient_id=str(deposit.get("patient_id", "")),
        refund_date=db.today_str(), amount=amt, method=method,
        reason=reason.strip(), transaction_id=transaction_id.strip(),
        user=user, ip=client_ip(request),
    )
    if errors:
        for e in errors:
            flash(request, e, "error")
    else:
        flash(request, "Deposit refunded successfully.")
    return RedirectResponse(url="/deposits", status_code=303)


@router.get("/refunds", response_class=HTMLResponse)
def refund_list(request: Request, page: int = Query(1), user: dict = Depends(require_permission("view_payments"))):
    from ..main import templates
    df = db.read_table("refunds")
    total = len(df)
    pg = paginate(total, page, 20)
    rows = []
    patients = db.read_table("patients")
    pnames = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    if total:
        df = df.sort_values("refund_date", ascending=False)
        for _, r in df.iloc[pg["start"]:pg["end"]].iterrows():
            d = r.to_dict()
            d["patient_name"] = pnames.get(str(d["patient_id"]), "-")
            rows.append(d)
    return templates.TemplateResponse(request, "payments/refunds.html", template_context(request, {
        "active_nav": "payments", "rows": rows, "pg": pg,
    }))
