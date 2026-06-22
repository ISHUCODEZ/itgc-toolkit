"""
Seed CCM history — generate 12 weekly snapshots that tell a story.

Trend charts are meaningless with a single data point, so this backfills ~3
months of weekly runs of synthetic-but-engineered data, fed through the *real*
control engines. The story that emerges from the trend lines:

  • SoD conflicts decline week over week as remediation lands (8 -> 2) …
  • … with a visible spike at week 6 when a bad access-provisioning batch
    reintroduces conflicts (and a brand-new high-severity one), and
  • change-compliance dips below 80% the same week then recovers, while
  • the recertification backlog steadily shrinks (6 at-risk grants -> 1).

Because each week runs through sod / change_audit / recert and the CCM
fingerprint + delta machinery, the new/resolved/persisting counts, exception
aging and MTTR are all genuine — not hand-written numbers.

Run from the backend/ directory:   python backfill.py
"""
from __future__ import annotations
import sys, os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from services import ccm, db  # noqa: E402

WEEKS = 12

# ---- SoD: toxic role pairs, and which users still hold them each week -------
TOXIC = {
    "priya.sharma":   ("vendor_create", "payment_approve"),
    "rahul.verma":    ("journal_create", "journal_approve"),
    "anita.desai":    ("developer", "deploy_prod"),
    "vikram.singh":   ("po_create", "goods_receipt"),
    "sneha.iyer":     ("payroll_run", "hr_employee_create"),
    "arjun.nair":     ("vendor_create", "vendor_bank_edit"),
    "deepa.menon":    ("iam_admin", "access_approve"),
    "meera.joshi":    ("dba", "app_business_user"),
    "sanjay.gupta":   ("journal_post", "journal_approve"),   # appears in the spike
}
CLEAN_USERS = ["karan.malhotra", "rohit.kapoor", "neha.reddy", "amit.shah"]

# Conflicts present each week (oldest -> newest). Declining, with a spike at w5.
SOD_WEEKLY = [
    ["priya.sharma", "rahul.verma", "anita.desai", "vikram.singh", "sneha.iyer", "arjun.nair", "deepa.menon", "meera.joshi"],  # 8
    ["priya.sharma", "rahul.verma", "anita.desai", "vikram.singh", "sneha.iyer", "arjun.nair", "deepa.menon", "meera.joshi"],  # 8
    ["priya.sharma", "rahul.verma", "anita.desai", "vikram.singh", "sneha.iyer", "arjun.nair", "deepa.menon"],                 # 7  meera remediated
    ["priya.sharma", "rahul.verma", "anita.desai", "vikram.singh", "sneha.iyer", "arjun.nair"],                                # 6  deepa remediated
    ["priya.sharma", "rahul.verma", "anita.desai", "vikram.singh", "arjun.nair"],                                              # 5  sneha remediated
    ["priya.sharma", "rahul.verma", "anita.desai", "vikram.singh", "arjun.nair", "sanjay.gupta", "sneha.iyer"],                # 7  SPIKE: bad batch
    ["priya.sharma", "rahul.verma", "anita.desai", "vikram.singh", "arjun.nair"],                                              # 5  spike cleaned up
    ["priya.sharma", "rahul.verma", "anita.desai", "arjun.nair"],                                                              # 4  vikram remediated
    ["priya.sharma", "rahul.verma", "anita.desai", "arjun.nair"],                                                              # 4
    ["priya.sharma", "rahul.verma", "anita.desai"],                                                                            # 3  arjun remediated
    ["priya.sharma", "rahul.verma"],                                                                                           # 2  anita remediated
    ["priya.sharma", "rahul.verma"],                                                                                           # 2
]

# ---- Change: flagged changes per week (drives compliance %) -----------------
# 10 changes/week; a dip below 80% at the spike, then recovery.
CHANGE_FLAGGED = [2, 1, 1, 2, 1, 5, 3, 1, 1, 1, 1, 1]

# ---- Recert: how many grants are at-risk (overdue/never) each week ----------
RECERT_AT_RISK = [6, 6, 5, 5, 4, 4, 3, 2, 2, 1, 1, 1]
# Pool of grants, worst-first; the tail clears first so the worst ones age.
RECERT_POOL = [
    ("arjun.nair", "SAP ECC", "Vendor Master"),          # stays "never reviewed" longest
    ("meera.joshi", "Oracle DB", "DBA"),
    ("anita.desai", "Production Servers", "Admin"),
    ("sneha.iyer", "Payroll System", "Payroll Admin"),
    ("deepa.menon", "Identity Platform", "IAM Admin"),
    ("vikram.singh", "Procurement", "Buyer"),
]
RECERT_CURRENT = [
    ("priya.sharma", "SAP ECC", "Finance Power User"),
    ("rahul.verma", "SAP ECC", "GL Accountant"),
    ("karan.malhotra", "Data Warehouse", "Analyst"),
    ("rohit.kapoor", "SAP ECC", "Report Viewer"),
]


def sod_rows(week_idx: int) -> list[dict]:
    present = set(SOD_WEEKLY[week_idx])
    rows = []
    for user, (a, b) in TOXIC.items():
        if user in present:
            rows.append({"user": user, "role": a})
            rows.append({"user": user, "role": b})
        else:
            rows.append({"user": user, "role": "report_view"})   # remediated -> clean
    for user in CLEAN_USERS:
        rows.append({"user": user, "role": "report_view"})
    return rows


def change_rows(week_idx: int) -> list[dict]:
    flagged = CHANGE_FLAGGED[week_idx]
    rows, total = [], 10
    devs = ["anita.desai", "suresh.rao", "priya.sharma", "vikram.singh",
            "meera.joshi", "karan.malhotra", "rohit.kapoor", "neha.reddy",
            "amit.shah", "sanjay.gupta"]
    for n in range(total):
        cid = f"CHG-W{week_idx:02d}-{n+1:03d}"
        dev = devs[n % len(devs)]
        if n < flagged:
            # rotate through realistic violation types
            variant = n % 3
            if variant == 0:        # self-deployed (SoD breach)
                rows.append({"change_id": cid, "developer": dev, "deployer": dev,
                             "approved": "yes", "tested": "yes", "type": "normal",
                             "status": "deployed_prod"})
            elif variant == 1:      # unauthorised
                rows.append({"change_id": cid, "developer": dev, "deployer": "raj.kumar",
                             "approved": "no", "tested": "yes", "type": "normal",
                             "status": "deployed_prod"})
            else:                   # untested emergency, no retrospective approval
                rows.append({"change_id": cid, "developer": dev, "deployer": "raj.kumar",
                             "approved": "no", "tested": "no", "type": "emergency",
                             "status": "deployed_prod"})
        else:                       # compliant
            rows.append({"change_id": cid, "developer": dev, "deployer": "raj.kumar",
                         "approved": "yes", "tested": "yes", "type": "normal",
                         "status": "deployed_prod"})
    return rows


def recert_rows(week_idx: int, week_date: date) -> list[dict]:
    at_risk_n = RECERT_AT_RISK[week_idx]
    rows = []
    for i, (user, system, level) in enumerate(RECERT_POOL):
        if i < at_risk_n:
            # the first pool entry is "never reviewed"; the rest are overdue
            last = "" if i == 0 else (week_date - timedelta(days=115)).isoformat()
        else:
            last = (week_date - timedelta(days=25)).isoformat()   # freshly re-reviewed
        rows.append({"user": user, "system": system,
                     "access_level": level, "last_reviewed": last})
    for user, system, level in RECERT_CURRENT:
        rows.append({"user": user, "system": system, "access_level": level,
                     "last_reviewed": (week_date - timedelta(days=18)).isoformat()})
    return rows


def reset_ccm():
    """Wipe prior CCM run history so backfill is idempotent (thresholds kept)."""
    c = db.connect()
    for t in ("ccm_run_findings", "ccm_exceptions", "ccm_alerts", "ccm_runs"):
        c.execute(f"DELETE FROM {t}")
    # reset autoincrement counters so run_ids start at 1 again
    c.execute("DELETE FROM sqlite_sequence WHERE name IN "
              "('ccm_runs','ccm_alerts')")
    c.commit(); c.close()


def main():
    db.init()
    reset_ccm()
    ccm.seed_default_thresholds()

    end = date.today() - timedelta(days=3)        # leave room for a live "Run now"
    for i in range(WEEKS):
        week_date = end - timedelta(weeks=(WEEKS - 1 - i))
        ts = f"{week_date.isoformat()} 09:00:00"
        summary = ccm.run_all_controls(
            "backfill",
            datasets={"sod": sod_rows(i),
                      "change": change_rows(i),
                      "recert": recert_rows(i, week_date)},
            today=week_date, ts=ts,
        )
        d = summary["delta"]
        m = summary["metrics"]
        print(f"week {i+1:2d} {week_date}  "
              f"SoD={m['sod']['conflict_count']:>2}  "
              f"change={m['change']['compliance_rate']:>5.1f}%  "
              f"recert_at_risk={m['recert']['at_risk']:>2}  "
              f"(new {d['new']}, resolved {d['resolved']}, persisting {d['persisting']}, "
              f"alerts {summary.get('alerts_raised', 0)})")

    print("\nBackfill complete — open the CCM Monitor page to see the trends.")


if __name__ == "__main__":
    main()
