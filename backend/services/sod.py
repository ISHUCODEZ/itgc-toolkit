"""
Segregation-of-Duties (SoD) analyzer.

Takes a user-entitlement export (who has which roles/permissions) and flags
toxic combinations — pairs of duties that, held by one person, break
segregation of duties. This is one of the most common real ITGC access tests.

The conflict ruleset is explicit and editable, so every flag traces to a named
rule with a rationale and a control reference — the way an auditor documents a
finding. Deterministic: same input always yields the same conflicts.
"""
from __future__ import annotations
from collections import defaultdict

# ---- the SoD conflict ruleset -------------------------------------------------
# Each rule: two duty-sets that must not be held by the same person, with the
# business risk it creates and the control framework it maps to.
SOD_RULES = [
    {
        "id": "SOD-01", "name": "Create vendor + Approve payment",
        "duty_a": {"vendor_create", "vendor_maintain", "ap_vendor_setup"},
        "duty_b": {"payment_approve", "payment_release", "ap_payment_run"},
        "severity": "High",
        "risk": "A user who can both create a vendor and approve payments can set "
                "up a fictitious vendor and pay it — a classic fraud path.",
        "control": "Access Management / SoD \u00B7 SOX 404, COBIT DSS06",
    },
    {
        "id": "SOD-02", "name": "Create vendor + Maintain bank details",
        "duty_a": {"vendor_create", "vendor_maintain"},
        "duty_b": {"vendor_bank_edit", "bank_details_maintain"},
        "severity": "High",
        "risk": "Creating a vendor and editing its bank account enables diverting "
                "payments to an attacker-controlled account.",
        "control": "Access Management / SoD \u00B7 SOX 404",
    },
    {
        "id": "SOD-03", "name": "Post journal + Approve journal",
        "duty_a": {"journal_create", "journal_post"},
        "duty_b": {"journal_approve"},
        "severity": "High",
        "risk": "Self-approving journal entries removes the independent review "
                "that prevents fraudulent or erroneous postings to the GL.",
        "control": "Access Management / SoD \u00B7 SOX 404",
    },
    {
        "id": "SOD-04", "name": "Develop code + Deploy to production",
        "duty_a": {"dev_write", "code_commit", "developer"},
        "duty_b": {"prod_deploy", "release_manager", "deploy_prod"},
        "severity": "High",
        "risk": "A developer who can also deploy to production can push "
                "unreviewed or malicious code without independent release control.",
        "control": "Change Management / SoD \u00B7 COBIT BAI06, SOX ITGC",
    },
    {
        "id": "SOD-05", "name": "User admin + Approve own access",
        "duty_a": {"user_admin", "iam_admin", "grant_access"},
        "duty_b": {"access_approve", "access_review"},
        "severity": "High",
        "risk": "An administrator who approves access requests can grant "
                "themselves privileges with no independent check.",
        "control": "Access Management / SoD \u00B7 SOX 404, ISO 27001 A.9",
    },
    {
        "id": "SOD-06", "name": "Process payroll + Maintain employee master",
        "duty_a": {"payroll_run", "payroll_process"},
        "duty_b": {"hr_employee_create", "employee_master_maintain"},
        "severity": "Medium",
        "risk": "Creating employees and running payroll enables paying a "
                "fictitious or 'ghost' employee.",
        "control": "Access Management / SoD \u00B7 SOX 404",
    },
    {
        "id": "SOD-07", "name": "Purchase order + Goods receipt",
        "duty_a": {"po_create", "po_approve"},
        "duty_b": {"goods_receipt", "grn_post"},
        "severity": "Medium",
        "risk": "Raising and approving a PO and also confirming receipt allows "
                "payment for goods never delivered.",
        "control": "Access Management / SoD \u00B7 COBIT DSS06",
    },
    {
        "id": "SOD-08", "name": "Database admin + Application user",
        "duty_a": {"dba", "db_admin"},
        "duty_b": {"app_business_user", "transaction_entry"},
        "severity": "Medium",
        "risk": "Direct database access plus a business role lets a user alter "
                "data behind the application's controls and audit trail.",
        "control": "Access Management / Privileged Access \u00B7 ISO 27001 A.9",
    },
]


def _normalise(token: str) -> str:
    return token.strip().lower().replace(" ", "_").replace("-", "_")


def analyse(rows: list[dict]) -> dict:
    """
    rows: list of {"user": str, "role": str} (one row per user-role grant),
          OR {"user": str, "roles": "r1;r2;r3"} (semicolon list).
    Returns conflicts grouped by user plus summary stats.
    """
    # build user -> set(roles)
    user_roles: dict[str, set] = defaultdict(set)
    for r in rows:
        user = (r.get("user") or r.get("username") or r.get("user_id") or "").strip()
        if not user:
            continue
        if r.get("roles"):
            for tok in str(r["roles"]).replace(",", ";").split(";"):
                if tok.strip():
                    user_roles[user].add(_normalise(tok))
        role = r.get("role") or r.get("permission") or r.get("entitlement")
        if role:
            user_roles[user].add(_normalise(role))

    findings = []
    for user, roles in sorted(user_roles.items()):
        for rule in SOD_RULES:
            hit_a = roles & {_normalise(x) for x in rule["duty_a"]}
            hit_b = roles & {_normalise(x) for x in rule["duty_b"]}
            if hit_a and hit_b:
                findings.append({
                    "user": user, "rule_id": rule["id"], "rule": rule["name"],
                    "severity": rule["severity"], "risk": rule["risk"],
                    "control": rule["control"],
                    "conflicting_roles": sorted(hit_a | hit_b),
                })

    sev_rank = {"High": 0, "Medium": 1, "Low": 2}
    findings.sort(key=lambda f: (sev_rank[f["severity"]], f["user"]))

    users_with_conflicts = len({f["user"] for f in findings})
    by_severity = {s: sum(1 for f in findings if f["severity"] == s)
                   for s in ("High", "Medium", "Low")}

    return {
        "total_users": len(user_roles),
        "total_grants": sum(len(v) for v in user_roles.values()),
        "conflicts": findings,
        "conflict_count": len(findings),
        "users_with_conflicts": users_with_conflicts,
        "clean_users": len(user_roles) - users_with_conflicts,
        "by_severity": by_severity,
        "rules_evaluated": len(SOD_RULES),
    }


def ruleset_summary() -> list[dict]:
    """Return the ruleset for display (without the raw duty token sets)."""
    return [{"id": r["id"], "name": r["name"], "severity": r["severity"],
             "risk": r["risk"], "control": r["control"]} for r in SOD_RULES]
