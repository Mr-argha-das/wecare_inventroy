"""Dashboard + global search + activity logs."""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from .. import database as db
from ..dependencies import require_permission, template_context
from ..services import report_service
from ..utils import paginate

router = APIRouter(tags=["dashboard"])


def resolve_range(preset: str, start: str, end: str) -> tuple[str, str]:
    today = db.today_str()
    d = datetime.strptime(today, "%Y-%m-%d").date()
    if preset == "yesterday":
        y = (d - timedelta(days=1)).strftime("%Y-%m-%d")
        return y, y
    if preset == "week":
        monday = d - timedelta(days=d.weekday())
        return monday.strftime("%Y-%m-%d"), today
    if preset == "month":
        return d.strftime("%Y-%m-01"), today
    if preset == "last_month":
        first = d.replace(day=1) - timedelta(days=1)
        return first.strftime("%Y-%m-01"), first.strftime("%Y-%m-%d")
    if preset == "custom" and start and end:
        return min(start, end), max(start, end)
    return today, today


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    preset: str = Query("month"),
    start: str = Query(""),
    end: str = Query(""),
    user: dict = Depends(require_permission("view_dashboard")),
):
    from ..main import templates
    s, e = resolve_range(preset, start, end)
    stats = report_service.dashboard_stats(s, e)
    pending = report_service.pending_payments()[:8]
    bills = db.read_table("bills")
    recent_bills = []
    if not bills.empty:
        bills = bills.sort_values("created_at", ascending=False).head(8)
        patients = db.read_table("patients")
        names = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
        for _, b in bills.iterrows():
            recent_bills.append({**b.to_dict(), "patient_name": names.get(str(b["patient_id"]), "-")})
    return templates.TemplateResponse(request, "dashboard.html", template_context(request, {
        "active_nav": "dashboard", "stats": stats, "pending": pending, "recent_bills": recent_bills,
        "preset": preset, "start": s, "end": e, "series_json": json.dumps(stats.get("series", [])),
    }))


@router.get("/search", response_class=HTMLResponse)
def global_search(request: Request, q: str = Query(""), user: dict = Depends(require_permission("view_dashboard"))):
    from ..main import templates
    q = q.strip()
    results: list[dict] = []
    if q:
        ql = q.lower()
        patients = db.read_table("patients")
        for _, r in patients.iterrows():
            if ql in str(r["patient_name"]).lower() or ql in str(r["mobile"]) or ql in str(r["patient_id"]).lower():
                results.append({"category": "Patient", "title": f"{r['patient_name']} ({r['patient_id']})",
                                "subtitle": str(r["mobile"]), "url": f"/patients/{r['patient_id']}"})
        bills = db.read_table("bills")
        for _, r in bills.iterrows():
            if ql in str(r["bill_number"]).lower() or ql in str(r["patient_id"]).lower():
                results.append({"category": "Bill", "title": f"{r['bill_number']} - {r['status']}",
                                "subtitle": f"{r['bill_date']} | {r['grand_total']}", "url": f"/bills/{r['bill_id']}"})
        equipment = db.read_table("equipment")
        for _, r in equipment.iterrows():
            if ql in str(r["equipment_name"]).lower() or ql in str(r["serial_number"]).lower() or ql in str(r["equipment_id"]).lower():
                results.append({"category": "Equipment", "title": f"{r['equipment_name']} ({r['equipment_id']})",
                                "subtitle": str(r["status"]), "url": f"/equipment/{r['equipment_id']}"})
        quotations = db.read_table("quotations")
        for _, r in quotations.iterrows():
            if ql in str(r["quotation_number"]).lower() or ql in str(r["customer_name"]).lower():
                results.append({"category": "Quotation", "title": f"{r['quotation_number']} - {r['status']}",
                                "subtitle": str(r["customer_name"]), "url": f"/quotations/{r['quotation_id']}"})
        payments = db.read_table("payments")
        for _, r in payments.iterrows():
            if ql in str(r["payment_id"]).lower() or ql in str(r["transaction_id"]).lower():
                results.append({"category": "Payment", "title": f"{r['payment_id']} - {r['amount']}",
                                "subtitle": str(r["method"]), "url": f"/payments/{r['payment_id']}"})
        results = results[:60]
    return templates.TemplateResponse(request, "search_results.html", template_context(request, {
        "active_nav": "", "query": q, "results": results,
    }))


@router.get("/activity-logs", response_class=HTMLResponse)
def activity_logs(
    request: Request,
    page: int = Query(1),
    action: str = Query(""),
    entity: str = Query(""),
    search: str = Query(""),
    user: dict = Depends(require_permission("view_activity_logs")),
):
    from ..main import templates
    df = db.read_table("activity_logs")
    if not df.empty:
        df = df.sort_values("timestamp", ascending=False)
        if action:
            df = df[df["action"].astype(str) == action]
        if entity:
            df = df[df["entity_type"].astype(str) == entity]
        if search:
            s = search.lower()
            mask = (
                df["description"].astype(str).str.lower().str.contains(s, na=False)
                | df["username"].astype(str).str.lower().str.contains(s, na=False)
                | df["entity_id"].astype(str).str.lower().str.contains(s, na=False)
            )
            df = df[mask]
    total = len(df)
    pg = paginate(total, page, 25)
    rows = df.iloc[pg["start"]:pg["end"]].to_dict("records") if total else []
    actions = sorted(db.read_table("activity_logs")["action"].astype(str).unique().tolist()) if total else []
    return templates.TemplateResponse(request, "activity_logs.html", template_context(request, {
        "active_nav": "activity", "rows": rows, "pg": pg, "action": action, "entity": entity,
        "search": search, "actions": actions,
    }))


@router.get("/activity-logs/{log_id}", response_class=HTMLResponse)
def activity_detail(request: Request, log_id: str, user: dict = Depends(require_permission("view_activity_logs"))):
    from ..main import templates
    rec = db.get_record("activity_logs", log_id)
    if not rec:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Log not found")
    before = rec.get("before_data") or ""
    after = rec.get("after_data") or ""
    import json as _json
    def _fmt(v: str):
        if not v:
            return None
        try:
            return _json.dumps(_json.loads(v), indent=2, ensure_ascii=False)
        except Exception:
            return v
    return templates.TemplateResponse(request, "activity_detail.html", template_context(request, {
        "active_nav": "activity", "log": rec, "before": _fmt(before), "after": _fmt(after),
    }))
