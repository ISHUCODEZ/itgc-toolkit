"""
Change-management log auditor.

Reads a change-ticket export and tests each change against the core ITGC
change-management control objectives:
  1. Authorisation  — was the change approved before deployment?
  2. Testing        — is there evidence it was tested?
  3. Segregation    — is the deployer different from the developer?
  4. Emergency      — emergency changes get retrospective scrutiny.

Each failing check becomes a documented exception with a control reference,
mirroring how an auditor tests a sample of changes in a SOX/ITGC engagement.
"""
from __future__ import annotations


def _truthy(v) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"yes", "y", "true", "1", "approved", "passed", "pass", "done", "complete", "completed"}


def _norm(v) -> str:
    return (str(v) if v is not None else "").strip().lower()


def analyse(rows: list[dict]) -> dict:
    """
    rows: change tickets. Recognised columns (flexible names):
      change_id / id / ticket
      developer / requested_by / author
      deployer / implemented_by / released_by
      approved / approval (yes/no or approver name)
      tested / test_evidence (yes/no)
      type / change_type (normal/standard/emergency)
      status / deployment_status
    """
    results = []
    for r in rows:
        cid = (r.get("change_id") or r.get("id") or r.get("ticket") or r.get("ticket_id") or "?")
        developer = _norm(r.get("developer") or r.get("requested_by") or r.get("author"))
        deployer = _norm(r.get("deployer") or r.get("implemented_by") or r.get("released_by"))
        approved = r.get("approved") if r.get("approved") is not None else r.get("approval")
        tested = r.get("tested") if r.get("tested") is not None else r.get("test_evidence")
        ctype = _norm(r.get("type") or r.get("change_type")) or "normal"
        status = _norm(r.get("status") or r.get("deployment_status"))

        deployed = ("prod" in status or "deployed" in status or "released" in status
                    or "closed" in status or status == "")

        exceptions = []
        # 1. authorisation
        approver_present = _truthy(approved) or (approved and _norm(approved) not in {"", "no", "n", "false", "0", "pending"})
        if deployed and not approver_present:
            exceptions.append(("Unauthorised change", "High",
                "Change reached production with no evidence of approval.",
                "Change Management / Authorisation \u00B7 SOX ITGC, COBIT BAI06"))
        # 2. testing
        if deployed and not _truthy(tested):
            exceptions.append(("No test evidence", "Medium",
                "Change was deployed without recorded evidence of testing.",
                "Change Management / Testing \u00B7 COBIT BAI06"))
        # 3. segregation of duties
        if developer and deployer and developer == deployer:
            sev = "High" if ctype != "emergency" else "Medium"
            exceptions.append(("Developer = deployer (SoD breach)", sev,
                f"The same person ({developer}) both developed and deployed this "
                "change, removing independent release control.",
                "Change Management / SoD \u00B7 SOX ITGC"))
        # 4. emergency change scrutiny
        if ctype == "emergency" and not approver_present:
            exceptions.append(("Emergency change not retrospectively approved", "Medium",
                "Emergency change lacks the required after-the-fact approval.",
                "Change Management / Emergency \u00B7 COBIT BAI06"))

        results.append({
            "change_id": str(cid), "developer": developer or "\u2014",
            "deployer": deployer or "\u2014", "type": ctype,
            "approved": approver_present, "tested": _truthy(tested),
            "deployed": deployed,
            "exceptions": [{"title": e[0], "severity": e[1], "detail": e[2], "control": e[3]} for e in exceptions],
            "compliant": len(exceptions) == 0,
        })

    total = len(results)
    flagged = [r for r in results if not r["compliant"]]
    all_exc = [e for r in results for e in r["exceptions"]]
    by_severity = {s: sum(1 for e in all_exc if e["severity"] == s) for s in ("High", "Medium", "Low")}
    return {
        "total_changes": total,
        "compliant_changes": total - len(flagged),
        "flagged_changes": len(flagged),
        "exception_count": len(all_exc),
        "by_severity": by_severity,
        "compliance_rate": round((total - len(flagged)) / total * 100, 1) if total else 0,
        "results": results,
    }
