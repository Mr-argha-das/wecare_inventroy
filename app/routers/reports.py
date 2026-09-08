"""Reports with date filters, print and PDF."""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import database as db
from ..dependencies import require_permission, template_context
from ..services import report_service
from ..utils import flash
from .dashboard import resolve_range

router = APIRouter(prefix="/reports", tags=["reports"])


def _ctx(request, preset, start, end):
    s, e = resolve_range(preset if preset != "custom" else "custom", start, end)
    if preset == "last_month":
        s, e = resolve_range("last_month", "", "")
    return s, e


@router.get("", response_class=HTMLResponse)
def report_home(request: Request, user: dict = Depends(require_permission("view_reports"))):
    from ..main import templates
    return templates.TemplateResponse(request, "reports/home.html", template_context(request, {"active_nav": "reports"}))


@router.get("/daily", response_class=HTMLResponse)
def daily(request: Request, preset: str = Query("month"), start: str = Query(""), end: str = Query(""),
          user: dict = Depends(require_permission("view_reports"))):
    from ..main import templates
    s, e = _ctx(request, preset, start, end)
    data = report_service.daily_collection(s, e)
    return templates.TemplateResponse(request, "reports/daily.html", template_context(request, {
        "active_nav": "reports", "preset": preset, "start": s, "end": e, **data,
    }))


@router.get("/monthly", response_class=HTMLResponse)
def monthly(request: Request, year: str = Query(""), user: dict = Depends(require_permission("view_reports"))):
    from ..main import templates
    year = year or db.today_str()[:4]
    data = report_service.monthly_collection(year)
    years = [str(int(db.today_str()[:4]) - i) for i in range(6)]
    return templates.TemplateResponse(request, "reports/monthly.html", template_context(request, {
        "active_nav": "reports", "year": year, "years": years, **data,
    }))


@router.get("/pending", response_class=HTMLResponse)
def pending(request: Request, search: str = Query(""), user: dict = Depends(require_permission("view_reports"))):
    from ..main import templates
    rows = report_service.pending_payments()
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["patient_name"].lower() or s in r["bill_number"].lower() or s in r["patient_id"].lower()]
    total = round(sum(r["pending"] for r in rows), 2)
    return templates.TemplateResponse(request, "reports/pending.html", template_context(request, {
        "active_nav": "reports", "rows": rows, "total": total, "search": search,
    }))


@router.get("/services", response_class=HTMLResponse)
def services_rep(request: Request, preset: str = Query("month"), start: str = Query(""), end: str = Query(""),
                 user: dict = Depends(require_permission("view_reports"))):
    from ..main import templates
    s, e = _ctx(request, preset, start, end)
    rows = report_service.service_wise(s, e)
    total = round(sum(r["revenue"] for r in rows), 2)
    return templates.TemplateResponse(request, "reports/services.html", template_context(request, {
        "active_nav": "reports", "preset": preset, "start": s, "end": e, "rows": rows, "total": total,
    }))


@router.get("/equipment", response_class=HTMLResponse)
def equipment_rep(request: Request, preset: str = Query("month"), start: str = Query(""), end: str = Query(""),
                  user: dict = Depends(require_permission("view_reports"))):
    from ..main import templates
    s, e = _ctx(request, preset, start, end)
    rows = report_service.equipment_rental_report(s, e)
    returns = report_service.equipment_return_report(s, e)
    total_rent = round(sum(r["rent_amount"] for r in rows), 2)
    total_dep = round(sum(r["deposit"] for r in rows), 2)
    return templates.TemplateResponse(request, "reports/equipment.html", template_context(request, {
        "active_nav": "reports", "preset": preset, "start": s, "end": e, "rows": rows,
        "returns": returns, "total_rent": total_rent, "total_dep": total_dep,
    }))


@router.get("/cash-online", response_class=HTMLResponse)
def cash_online(request: Request, preset: str = Query("month"), start: str = Query(""), end: str = Query(""),
                user: dict = Depends(require_permission("view_reports"))):
    from ..main import templates
    s, e = _ctx(request, preset, start, end)
    data = report_service.cash_vs_online(s, e)
    return templates.TemplateResponse(request, "reports/cash_online.html", template_context(request, {
        "active_nav": "reports", "preset": preset, "start": s, "end": e, **data,
    }))


@router.get("/profit", response_class=HTMLResponse)
def profit(request: Request, preset: str = Query("month"), start: str = Query(""), end: str = Query(""),
           user: dict = Depends(require_permission("view_profit_report"))):
    from ..main import templates
    s, e = _ctx(request, preset, start, end)
    data = report_service.profit_report(s, e)
    return templates.TemplateResponse(request, "reports/profit.html", template_context(request, {
        "active_nav": "reports", "preset": preset, "start": s, "end": e, **data,
    }))


@router.get("/{name}/pdf")
def report_pdf(request: Request, name: str, preset: str = Query("month"), start: str = Query(""),
               end: str = Query(""), year: str = Query(""),
               user: dict = Depends(require_permission("generate_pdf"))):
    from ..services.document_service import DocPDF, company_profile
    from ..utils import format_inr
    # view_reports required as well
    from ..dependencies import role_default_permissions
    from ..permissions import has_permission
    if not has_permission(user, "view_reports", role_default_permissions(str(user.get("role", "staff")))):
        flash(request, "You do not have permission.", "error")
        return RedirectResponse(url="/reports", status_code=303)
    if name == "profit" and not has_permission(user, "view_profit_report", role_default_permissions(str(user.get("role", "staff")))):
        flash(request, "You do not have permission.", "error")
        return RedirectResponse(url="/reports", status_code=303)
    company = company_profile()
    pdf = DocPDF(company)
    pdf.alias_nb_pages("{nb}")
    pdf.add_page()
    s, e = _ctx(request, preset, start, end)
    if name == "daily":
        data = report_service.daily_collection(s, e)
        pdf.title_band("DAILY COLLECTION REPORT", f"{s} to {e}", db.today_str())
        rows = [[r["date"], str(r["bills"]), format_inr(r["cash"]), format_inr(r["upi"]),
                 format_inr(r["bank"]), format_inr(r["total"])] for r in data["rows"]]
        pdf.items_table(["Date", "Bills", "Cash", "UPI", "Bank", "Total"], rows,
                        [30, 18, 34, 34, 34, 36], ["C", "C", "R", "R", "R", "R"])
        pdf.totals_block([("Cash", format_inr(data["totals"]["cash"])), ("UPI", format_inr(data["totals"]["upi"])),
                          ("Bank", format_inr(data["totals"]["bank"])), ("Refunds", format_inr(data["totals"]["refunds"]))],
                         grand_label="NET COLLECTION", grand_value=format_inr(data["totals"]["net"]))
    elif name == "monthly":
        data = report_service.monthly_collection(year or db.today_str()[:4])
        pdf.title_band("MONTHLY COLLECTION REPORT", str(data["year"]), db.today_str())
        rows = [[r["month"], str(r["bills"]), format_inr(r["cash"]), format_inr(r["upi"]),
                 format_inr(r["bank"]), format_inr(r["total"])] for r in data["rows"]]
        pdf.items_table(["Month", "Bills", "Cash", "UPI", "Bank", "Total"], rows,
                        [30, 18, 34, 34, 34, 36], ["C", "C", "R", "R", "R", "R"])
        pdf.totals_block([], grand_label="YEAR TOTAL", grand_value=format_inr(data["totals"]["total"]))
    elif name == "pending":
        rows = report_service.pending_payments()
        pdf.title_band("PENDING PAYMENT REPORT", f"As on {e}", db.today_str())
        pdf.items_table(["Patient", "Bill", "Date", "Total", "Received", "Pending"],
                        [[r["patient_name"][:28], r["bill_number"], r["bill_date"],
                          format_inr(r["grand_total"]), format_inr(r["received"]), format_inr(r["pending"])] for r in rows],
                        [52, 44, 24, 26, 26, 26], ["L", "L", "C", "R", "R", "R"])
        pdf.totals_block([], grand_label="TOTAL PENDING", grand_value=format_inr(sum(r["pending"] for r in rows)))
    elif name == "services":
        rows = report_service.service_wise(s, e)
        pdf.title_band("SERVICE-WISE REPORT", f"{s} to {e}", db.today_str())
        pdf.items_table(["Service", "Qty", "Bills", "Revenue"],
                        [[r["service"][:60], str(r["quantity"]), str(r["bills"]), format_inr(r["revenue"])] for r in rows],
                        [96, 28, 28, 34], ["L", "C", "C", "R"])
        pdf.totals_block([], grand_label="TOTAL REVENUE", grand_value=format_inr(sum(r["revenue"] for r in rows)))
    elif name == "equipment":
        rows = report_service.equipment_rental_report(s, e)
        pdf.title_band("EQUIPMENT RENTAL REPORT", f"{s} to {e}", db.today_str())
        pdf.items_table(["Equipment", "Patient", "Start", "End", "Rent", "Deposit"],
                        [[r["equipment"][:26], r["patient_name"][:22], r["issue_date"], r["expected_return_date"],
                          format_inr(r["rent_amount"]), format_inr(r["deposit"])] for r in rows],
                        [44, 38, 24, 24, 28, 28], ["L", "L", "C", "C", "R", "R"])
    elif name == "cash-online":
        data = report_service.cash_vs_online(s, e)
        pdf.title_band("CASH VS ONLINE COLLECTION", f"{s} to {e}", db.today_str())
        pdf.totals_block([("Cash", format_inr(data["cash"])), ("UPI", format_inr(data["upi"])),
                          ("Bank", format_inr(data["bank"])), ("Refunds", format_inr(data["refunds"]))],
                         grand_label="NET TOTAL", grand_value=format_inr(data["net"]))
    elif name == "profit":
        data = report_service.profit_report(s, e)
        pdf.title_band("PROFIT REPORT", f"{s} to {e}", db.today_str())
        pdf.totals_block([("Gross Revenue", format_inr(data["revenue"])), ("Refunds", format_inr(data["refunds"])),
                          ("Net Revenue", format_inr(data["net_revenue"])), ("Tax Collected", format_inr(data["tax_collected"])),
                          ("Discounts", format_inr(data["discounts"])), ("Equipment Cost (sold items)", format_inr(data["equipment_cost"]))],
                         grand_label="PROFIT", grand_value=format_inr(data["profit"]))
        if data["cost_lines"]:
            pdf.items_table(["Item", "Qty", "Unit Cost", "Total Cost"],
                            [[c["description"][:60], str(c["quantity"]), format_inr(c["unit_cost"]), format_inr(c["total_cost"])]
                             for c in data["cost_lines"]], [96, 24, 33, 33], ["L", "C", "R", "R"])
    else:
        flash(request, "Unknown report.", "error")
        return RedirectResponse(url="/reports", status_code=303)
    pdf.bank_terms_signature(company)
    import io
    buf = io.BytesIO(pdf.output())
    filename = f"{name}_report_{s}_to_{e}.pdf"
    return Response(content=buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
