"""Reports generated from live Feather data.

Canceled bills are excluded from revenue/collection calculations; refunds are
reflected correctly; pending balances come from actual outstanding amounts.
"""
from datetime import datetime

import pandas as pd

from .. import database as db

ACTIVE_BILL_FILTER = {"cancelled": False}


def _active_bills() -> pd.DataFrame:
    df = db.read_table("bills")
    if df.empty:
        return df
    return df[~df["cancelled"].astype(bool)].copy()


def _in_range(df: pd.DataFrame, column: str, start: str, end: str) -> pd.DataFrame:
    if df.empty or column not in df.columns:
        return df.iloc[0:0]
    s = df[column].astype(str)
    mask = (s >= start) & (s <= end)
    return df[mask].copy()


def _bill_numbers(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    return {str(r["bill_id"]): str(r["bill_number"]) for _, r in df.iterrows()}


def daily_collection(start: str, end: str) -> dict:
    bills = _active_bills()
    valid_bill_ids = set(bills["bill_id"].astype(str)) if not bills.empty else set()
    payments = db.read_table("payments")
    payments = _in_range(payments, "payment_date", start, end)
    if not payments.empty and valid_bill_ids:
        # Exclude payments that belong to canceled bills.
        payments = payments[payments["bill_id"].astype(str).isin(valid_bill_ids) | (payments["bill_id"].astype(str) == "")]
    refunds = _in_range(db.read_table("refunds"), "refund_date", start, end)
    refund_total = float(refunds["amount"].sum()) if not refunds.empty else 0.0

    rows: list[dict] = []
    by_date: dict[str, dict] = {}
    for _, p in payments.iterrows():
        d = str(p["payment_date"])[:10]
        cell = by_date.setdefault(d, {"date": d, "bills": set(), "Cash": 0.0, "UPI": 0.0, "Bank": 0.0, "total": 0.0})
        amt = float(p["amount"] or 0)
        cell["bills"].add(str(p["bill_id"]))
        if p["method"] in cell:
            cell[p["method"]] += amt
        cell["total"] += amt
    for d in sorted(by_date):
        cell = by_date[d]
        rows.append({
            "date": cell["date"], "bills": len(cell["bills"]),
            "cash": round(cell["Cash"], 2), "upi": round(cell["UPI"], 2),
            "bank": round(cell["Bank"], 2), "total": round(cell["total"], 2),
        })
    totals = {
        "bills": len({str(p['bill_id']) for _, p in payments.iterrows()}) if not payments.empty else 0,
        "cash": round(sum(r["cash"] for r in rows), 2),
        "upi": round(sum(r["upi"] for r in rows), 2),
        "bank": round(sum(r["bank"] for r in rows), 2),
        "total": round(sum(r["total"] for r in rows), 2),
        "refunds": round(refund_total, 2),
        "net": round(sum(r["total"] for r in rows) - refund_total, 2),
    }
    return {"rows": rows, "totals": totals}


def monthly_collection(year: str) -> dict:
    bills = _active_bills()
    valid_bill_ids = set(bills["bill_id"].astype(str)) if not bills.empty else set()
    payments = db.read_table("payments")
    if not payments.empty:
        payments = payments[payments["payment_date"].astype(str).str[:4] == str(year)]
        if valid_bill_ids:
            payments = payments[payments["bill_id"].astype(str).isin(valid_bill_ids) | (payments["bill_id"].astype(str) == "")]
    months = {f"{year}-{m:02d}": {"month": f"{year}-{m:02d}", "bills": set(), "cash": 0.0, "upi": 0.0, "bank": 0.0, "total": 0.0} for m in range(1, 13)}
    for _, p in payments.iterrows():
        key = str(p["payment_date"])[:7]
        if key in months:
            amt = float(p["amount"] or 0)
            months[key]["bills"].add(str(p["bill_id"]))
            if p["method"] in ("Cash", "UPI", "Bank"):
                months[key][p["method"].lower()] += amt
            months[key]["total"] += amt
    rows = []
    for key in sorted(months):
        m = months[key]
        rows.append({"month": m["month"], "bills": len(m["bills"]), "cash": round(m["cash"], 2),
                     "upi": round(m["upi"], 2), "bank": round(m["bank"], 2), "total": round(m["total"], 2)})
    totals = {"cash": round(sum(r["cash"] for r in rows), 2), "upi": round(sum(r["upi"] for r in rows), 2),
              "bank": round(sum(r["bank"] for r in rows), 2), "total": round(sum(r["total"] for r in rows), 2)}
    return {"rows": rows, "totals": totals, "year": year}


def pending_payments() -> list[dict]:
    bills = _active_bills()
    if bills.empty:
        return []
    pending = bills[bills["remaining_amount"].astype(float) > 0.005].copy()
    pending = pending.sort_values("bill_date")
    patients = db.read_table("patients")
    names = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    out = []
    for _, b in pending.iterrows():
        out.append({
            "bill_id": str(b["bill_id"]), "bill_number": str(b["bill_number"]),
            "bill_date": str(b["bill_date"]), "patient_id": str(b["patient_id"]),
            "patient_name": names.get(str(b["patient_id"]), "-"),
            "bill_type": str(b["bill_type"]), "grand_total": float(b["grand_total"]),
            "received": float(b["received_amount"]) - float(b["refunded_amount"]),
            "pending": float(b["remaining_amount"]), "status": str(b["status"]),
        })
    return out


def service_wise(start: str, end: str) -> list[dict]:
    bills = _in_range(_active_bills(), "bill_date", start, end)
    if bills.empty:
        return []
    items = db.read_table("bill_items")
    items = items[items["bill_id"].astype(str).isin(set(bills["bill_id"].astype(str)))]
    items = items[items["item_type"].astype(str) == "service"]
    agg: dict[str, dict] = {}
    for _, it in items.iterrows():
        key = str(it["description"]) or str(it["ref_id"])
        cell = agg.setdefault(key, {"service": key, "quantity": 0.0, "revenue": 0.0, "bills": set()})
        cell["quantity"] += float(it["quantity"] or 0)
        cell["revenue"] += float(it["amount"] or 0)
        cell["bills"].add(str(it["bill_id"]))
    return [{"service": v["service"], "quantity": round(v["quantity"], 2), "bills": len(v["bills"]),
             "revenue": round(v["revenue"], 2)} for v in sorted(agg.values(), key=lambda x: -x["revenue"])]


def equipment_rental_report(start: str, end: str) -> list[dict]:
    txns = db.read_table("equipment_transactions")
    if txns.empty:
        return []
    mask = (txns["txn_type"].astype(str) == "Issue")
    if start:
        mask &= txns["issue_date"].astype(str) >= start
    if end:
        mask &= txns["issue_date"].astype(str) <= end
    txns = txns[mask]
    eq = db.read_table("equipment")
    names = {str(r["equipment_id"]): str(r["equipment_name"]) for _, r in eq.iterrows()} if not eq.empty else {}
    patients = db.read_table("patients")
    pnames = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    out = []
    for _, t in txns.sort_values("issue_date").iterrows():
        out.append({
            "txn_id": str(t["txn_id"]), "equipment": names.get(str(t["equipment_id"]), str(t["equipment_id"])),
            "equipment_id": str(t["equipment_id"]), "serial_number": str(t["serial_number"]),
            "patient_name": pnames.get(str(t["patient_id"]), str(t["patient_id"])),
            "issue_date": str(t["issue_date"]), "expected_return_date": str(t["expected_return_date"]),
            "return_date": str(t["return_date"]), "rent_amount": float(t["rent_amount"]),
            "deposit": float(t["deposit"]), "bill_id": str(t["bill_id"]),
        })
    return out


def equipment_return_report(start: str, end: str) -> list[dict]:
    txns = db.read_table("equipment_transactions")
    if txns.empty:
        return []
    mask = (txns["txn_type"].astype(str) == "Return")
    if start:
        mask &= txns["return_date"].astype(str) >= start
    if end:
        mask &= txns["return_date"].astype(str) <= end
    txns = txns[mask]
    eq = db.read_table("equipment")
    names = {str(r["equipment_id"]): str(r["equipment_name"]) for _, r in eq.iterrows()} if not eq.empty else {}
    patients = db.read_table("patients")
    pnames = {str(r["patient_id"]): str(r["patient_name"]) for _, r in patients.iterrows()} if not patients.empty else {}
    out = []
    for _, t in txns.sort_values("return_date").iterrows():
        out.append({
            "txn_id": str(t["txn_id"]), "equipment": names.get(str(t["equipment_id"]), str(t["equipment_id"])),
            "serial_number": str(t["serial_number"]),
            "patient_name": pnames.get(str(t["patient_id"]), str(t["patient_id"])),
            "issue_date": str(t["issue_date"]), "return_date": str(t["return_date"]),
            "condition": str(t["condition"]), "deposit": float(t["deposit"]),
            "damage_charge": float(t["damage_charge"]), "loss_charge": float(t["loss_charge"]),
            "refund_amount": float(t["refund_amount"]),
        })
    return out


def cash_vs_online(start: str, end: str) -> dict:
    data = daily_collection(start, end)
    t = data["totals"]
    return {"cash": t["cash"], "upi": t["upi"], "bank": t["bank"], "total": t["total"],
            "refunds": t["refunds"], "net": t["net"], "rows": data["rows"]}


def profit_report(start: str, end: str) -> dict:
    """Revenue vs cost-based profit. Costs come from equipment purchase_cost
    (for sold equipment) and service default_rate is revenue, not cost, so
    only equipment costs are deductible where known."""
    bills = _in_range(_active_bills(), "bill_date", start, end)
    revenue = float(bills["grand_total"].sum()) if not bills.empty else 0.0
    tax_collected = float(bills["tax"].sum()) if not bills.empty else 0.0
    discounts = float(bills["discount"].sum()) if not bills.empty else 0.0
    items = db.read_table("bill_items")
    if not items.empty and not bills.empty:
        items = items[items["bill_id"].astype(str).isin(set(bills["bill_id"].astype(str)))]
    else:
        items = items.iloc[0:0]
    eq = db.read_table("equipment")
    costs = {str(r["equipment_id"]): float(r["purchase_cost"] or 0) for _, r in eq.iterrows()} if not eq.empty else {}
    equipment_cost = 0.0
    cost_lines: list[dict] = []
    for _, it in items.iterrows():
        if str(it["item_type"]) == "equipment" and str(it["billing_type"]).lower() == "sale":
            unit_cost = costs.get(str(it["ref_id"]), 0.0)
            line_cost = unit_cost * float(it["quantity"] or 0)
            equipment_cost += line_cost
            if line_cost:
                cost_lines.append({"description": str(it["description"]), "quantity": float(it["quantity"] or 0),
                                   "unit_cost": unit_cost, "total_cost": round(line_cost, 2)})
    refunds = _in_range(db.read_table("refunds"), "refund_date", start, end)
    refund_total = float(refunds["amount"].sum()) if not refunds.empty else 0.0
    net_revenue = revenue - refund_total
    profit = net_revenue - equipment_cost
    return {
        "revenue": round(revenue, 2), "refunds": round(refund_total, 2),
        "net_revenue": round(net_revenue, 2), "tax_collected": round(tax_collected, 2),
        "discounts": round(discounts, 2), "equipment_cost": round(equipment_cost, 2),
        "profit": round(profit, 2), "cost_lines": cost_lines,
        "bill_count": len(bills),
    }


def dashboard_stats(start: str, end: str) -> dict:
    bills = _active_bills()
    ranged = _in_range(bills, "bill_date", start, end)
    payments = _in_range(db.read_table("payments"), "payment_date", start, end)
    collection = float(payments["amount"].sum()) if not payments.empty else 0.0
    pending = float(bills["remaining_amount"].sum()) if not bills.empty else 0.0
    patients = db.read_table("patients")
    services = db.read_table("services")
    equipment = db.read_table("equipment")
    txns = db.read_table("equipment_transactions")
    on_rent = 0
    if not equipment.empty:
        on_rent = int((equipment["status"].astype(str) == "On Rent").sum())
    month_start = db.today_str()[:7] + "-01"
    month_bills = _in_range(bills, "bill_date", month_start, db.today_str())
    month_revenue = float(month_bills["grand_total"].sum()) if not month_bills.empty else 0.0
    # daily series for charts
    series: list[dict] = []
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
        from datetime import timedelta
        day = d0
        pay_by_day: dict[str, float] = {}
        for _, p in payments.iterrows():
            k = str(p["payment_date"])[:10]
            pay_by_day[k] = pay_by_day.get(k, 0.0) + float(p["amount"] or 0)
        bill_by_day: dict[str, float] = {}
        for _, b in ranged.iterrows():
            k = str(b["bill_date"])[:10]
            bill_by_day[k] = bill_by_day.get(k, 0.0) + float(b["grand_total"] or 0)
        while day <= d1 and len(series) <= 62:
            key = day.strftime("%Y-%m-%d")
            series.append({"date": key, "label": day.strftime("%d/%m"),
                           "collection": round(pay_by_day.get(key, 0.0), 2),
                           "billing": round(bill_by_day.get(key, 0.0), 2)})
            day += timedelta(days=1)
    except Exception:
        pass
    active_services = 0
    if not services.empty:
        active_services = int((services["status"].astype(str) == "Active").sum())
    return {
        "total_patients": len(patients),
        "bills_count": len(ranged),
        "collection": round(collection, 2),
        "pending": round(pending, 2),
        "active_services": active_services,
        "on_rent": on_rent,
        "month_revenue": round(month_revenue, 2),
        "series": series,
    }
