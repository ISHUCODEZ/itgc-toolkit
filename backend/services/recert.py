"""
User-access recertification tracker.

Manages the periodic access-review cycle: every user-access grant should be
re-reviewed on a defined cadence. This engine takes the population of grants
with their last-review dates and computes which reviews are current, due soon,
or overdue — the monitoring control that proves access stays appropriate over
time, not just at provisioning.
"""
from __future__ import annotations
from datetime import date, datetime


def _parse_date(v):
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def analyse(rows: list[dict], today: date | None = None, cadence_days: int = 90) -> dict:
    """
    rows: {"user", "system"/"application", "access_level"/"role",
           "last_reviewed" (date), optional "review_cadence_days"}
    cadence_days: default review period if a row doesn't specify one.
    """
    today = today or date.today()
    items = []
    for r in rows:
        user = (r.get("user") or r.get("username") or "").strip()
        if not user:
            continue
        system = (r.get("system") or r.get("application") or r.get("app") or "\u2014").strip()
        level = (r.get("access_level") or r.get("role") or r.get("entitlement") or "\u2014").strip()
        last = _parse_date(r.get("last_reviewed") or r.get("last_review") or r.get("reviewed_on"))
        cadence = r.get("review_cadence_days")
        try:
            cadence = int(cadence) if cadence else cadence_days
        except (ValueError, TypeError):
            cadence = cadence_days

        if last is None:
            status, days_over = "Never reviewed", None
        else:
            age = (today - last).days
            due_in = cadence - age
            if due_in < 0:
                status, days_over = "Overdue", -due_in
            elif due_in <= 14:
                status, days_over = "Due soon", due_in
            else:
                status, days_over = "Current", due_in

        items.append({
            "user": user, "system": system, "access_level": level,
            "last_reviewed": last.isoformat() if last else "\u2014",
            "cadence_days": cadence, "status": status,
            "days": days_over,
        })

    order = {"Never reviewed": 0, "Overdue": 1, "Due soon": 2, "Current": 3}
    items.sort(key=lambda x: (order[x["status"]], -(x["days"] or 0) if x["status"] in ("Overdue",) else 0))

    counts = {k: sum(1 for x in items if x["status"] == k) for k in order}
    total = len(items)
    at_risk = counts["Never reviewed"] + counts["Overdue"]
    return {
        "total_grants": total,
        "counts": counts,
        "at_risk": at_risk,
        "current_rate": round((counts["Current"]) / total * 100, 1) if total else 0,
        "items": items,
        "cadence_days": cadence_days,
        "as_of": today.isoformat(),
    }
