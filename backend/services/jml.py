"""
Joiners, Movers, Leavers (JML) — Terminated User Access Review.

Tests whether users who have left the organisation still hold active system
access. One of the most commonly failed ITGC controls: HR records a
termination; IT provisioning never revokes access.

Takes two inputs:
  terminated export : user, termination_date, department, reason
  entitlements      : user, role  (same format as the SoD tool)

Every active entitlement belonging to a terminated user is a finding.
Severity is driven by how long access has been orphaned.
"""
from __future__ import annotations
from datetime import date, datetime

DATE_FMTS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y")


def _parse_date(s: str) -> date | None:
    for fmt in DATE_FMTS:
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def analyse(terminated_rows: list[dict], entitlement_rows: list[dict],
            as_of: date | None = None) -> dict:
    today = as_of or date.today()

    # terminated user index
    terminated: dict[str, dict] = {}
    for r in terminated_rows:
        user = (r.get("user") or r.get("username") or "").strip()
        if not user:
            continue
        td = _parse_date(r.get("termination_date") or "")
        terminated[user.lower()] = {
            "user": user,
            "termination_date": str(td) if td else (r.get("termination_date") or "unknown"),
            "termination_date_parsed": td,
            "department": r.get("department", ""),
            "reason": r.get("reason", "Terminated"),
        }

    # entitlement index: lower(user) -> [{role, system}]
    entitlements: dict[str, list] = {}
    for r in entitlement_rows:
        user = (r.get("user") or r.get("username") or "").strip().lower()
        if not user:
            continue
        role = (r.get("role") or r.get("permission") or r.get("entitlement") or "").strip()
        system = (r.get("system") or "ERP").strip()
        entitlements.setdefault(user, [])
        if role:
            entitlements[user].append({"role": role, "system": system})

    findings = []
    for user_lower, tinfo in sorted(terminated.items()):
        grants = entitlements.get(user_lower)
        if not grants:
            continue
        td = tinfo["termination_date_parsed"]
        days_since = (today - td).days if td else None

        if days_since is None or days_since >= 30:
            severity = "High"
        elif days_since >= 7:
            severity = "Medium"
        else:
            severity = "Low"

        for grant in grants:
            findings.append({
                "user": tinfo["user"],
                "department": tinfo["department"],
                "termination_date": tinfo["termination_date"],
                "days_since_termination": days_since,
                "reason": tinfo["reason"],
                "role": grant["role"],
                "system": grant["system"],
                "severity": severity,
                "risk": (
                    f"Terminated user retains active '{grant['role']}' access on "
                    f"{grant['system']}. Orphaned accounts enable unauthorised access "
                    f"by former employees and fail the access removal control."
                ),
                "control": "Access Management / JML · SOX 404, ISO 27001 A.9.2.6",
            })

    sev_rank = {"High": 0, "Medium": 1, "Low": 2}
    findings.sort(key=lambda f: (sev_rank[f["severity"]], -(f["days_since_termination"] or 0)))

    users_affected = len({f["user"] for f in findings})
    by_severity = {s: sum(1 for f in findings if f["severity"] == s)
                   for s in ("High", "Medium", "Low")}

    return {
        "total_terminated": len(terminated),
        "total_entitlements_checked": sum(len(v) for v in entitlements.values()),
        "users_with_active_access": users_affected,
        "clean_terminated": len(terminated) - users_affected,
        "findings": findings,
        "finding_count": len(findings),
        "by_severity": by_severity,
        "as_of": str(today),
    }
