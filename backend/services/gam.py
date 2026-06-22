"""GAM (Global Audit Methodology) data service.

Provides CRUD for: IT Risk Assessment, Relevant IT Systems,
IPE/ITOR Register, Rollforward Procedures, Application Controls.
Domain overview is computed live from CCM data.
"""
from __future__ import annotations
import time
from . import db, ccm

TS = lambda: time.strftime("%Y-%m-%d %H:%M:%S")

GAM_DOMAINS = [
    ("Access to programs and data", ["SoD", "Recert", "JML"]),
    ("Program changes",             ["Change"]),
    ("Computer operations",         []),
    ("Program development",         []),
]


# ── IT Risk Assessment ──────────────────────────────────────────────────────
def risk_assessment() -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT * FROM gam_risk_assessment ORDER BY rowid").fetchall()
    c.close()
    return [dict(r) for r in rows]


def save_risk_factor(factor: str, assessment: str, implication: str, notes: str) -> dict:
    c = db.connect()
    c.execute("""INSERT INTO gam_risk_assessment (factor,assessment,implication,notes,updated_ts)
                 VALUES (?,?,?,?,?)
                 ON CONFLICT(factor) DO UPDATE SET
                   assessment=excluded.assessment,
                   implication=excluded.implication,
                   notes=excluded.notes,
                   updated_ts=excluded.updated_ts""",
              (factor, assessment, implication, notes, TS()))
    c.commit(); c.close()
    return {"factor": factor, "assessment": assessment,
            "implication": implication, "notes": notes}


def overall_risk() -> str:
    """Derive overall IT risk from individual factor assessments."""
    c = db.connect()
    rows = c.execute("SELECT assessment FROM gam_risk_assessment").fetchall()
    c.close()
    assessments = [r["assessment"] for r in rows]
    if "High" in assessments:
        return "High"
    if "Moderate" in assessments:
        return "Moderate"
    return "Low"


# ── Relevant IT Systems ─────────────────────────────────────────────────────
def systems_list() -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT * FROM gam_systems ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


def add_system(name, type_, process, hosting, in_scope, domains, cuecs, notes) -> dict:
    c = db.connect()
    cur = c.execute(
        """INSERT INTO gam_systems (name,type,process,hosting,in_scope,domains,cuecs,notes,updated_ts)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (name, type_, process, hosting, 1 if in_scope else 0, domains, cuecs, notes, TS()))
    row = c.execute("SELECT * FROM gam_systems WHERE id=?", (cur.lastrowid,)).fetchone()
    c.commit(); c.close()
    return dict(row)


def update_system(sid: int, **kwargs) -> dict | None:
    allowed = {"name", "type", "process", "hosting", "in_scope", "domains", "cuecs", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return None
    updates["updated_ts"] = TS()
    sets = ", ".join(f"{k}=?" for k in updates)
    c = db.connect()
    c.execute(f"UPDATE gam_systems SET {sets} WHERE id=?", list(updates.values()) + [sid])
    row = c.execute("SELECT * FROM gam_systems WHERE id=?", (sid,)).fetchone()
    c.commit(); c.close()
    return dict(row) if row else None


def delete_system(sid: int):
    c = db.connect()
    c.execute("DELETE FROM gam_systems WHERE id=?", (sid,))
    c.commit(); c.close()


# ── GITC Domain Overview (live from CCM) ────────────────────────────────────
def domain_overview() -> list[dict]:
    excs = ccm.exceptions_list(status="open")
    k    = ccm.kpis()
    m    = k.get("latest_metrics", {})
    out  = []
    for domain, controls in GAM_DOMAINS:
        exc_count = sum(1 for e in excs if e["control"] in controls)
        if not controls:
            conclusion = "Not in scope (checklist)"
            tested = "Checklist"
        elif exc_count:
            conclusion = "Deficiencies noted"
            tested = ", ".join(controls)
        else:
            conclusion = "Effective"
            tested = ", ".join(controls)
        out.append({
            "domain": domain, "controls": controls, "tested": tested,
            "open_exceptions": exc_count if controls else None,
            "conclusion": conclusion,
        })
    return out


# ── IPE/ITOR Register ───────────────────────────────────────────────────────
def ipeitor_list() -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT * FROM gam_ipeitor ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


def add_ipeitor(name, system, control_supported, completeness, accuracy,
                tested_by, test_date, notes) -> dict:
    c = db.connect()
    cur = c.execute(
        """INSERT INTO gam_ipeitor
           (name,system,control_supported,completeness_tested,accuracy_tested,tested_by,test_date,notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (name, system, control_supported, 1 if completeness else 0,
         1 if accuracy else 0, tested_by, test_date, notes))
    row = c.execute("SELECT * FROM gam_ipeitor WHERE id=?", (cur.lastrowid,)).fetchone()
    c.commit(); c.close()
    return dict(row)


def delete_ipeitor(iid: int):
    c = db.connect()
    c.execute("DELETE FROM gam_ipeitor WHERE id=?", (iid,))
    c.commit(); c.close()


# ── Rollforward ─────────────────────────────────────────────────────────────
def rollforward_list() -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT * FROM gam_rollforward ORDER BY rowid").fetchall()
    c.close()
    return [dict(r) for r in rows]


def save_rollforward(control, interim_date, yearend_date, changes,
                     procedures, conclusion, performed_by) -> dict:
    c = db.connect()
    c.execute("""INSERT INTO gam_rollforward
                 (control,interim_date,yearend_date,changes,procedures,conclusion,performed_by,updated_ts)
                 VALUES (?,?,?,?,?,?,?,?)
                 ON CONFLICT(control) DO UPDATE SET
                   interim_date=excluded.interim_date,
                   yearend_date=excluded.yearend_date,
                   changes=excluded.changes,
                   procedures=excluded.procedures,
                   conclusion=excluded.conclusion,
                   performed_by=excluded.performed_by,
                   updated_ts=excluded.updated_ts""",
              (control, interim_date, yearend_date, changes,
               procedures, conclusion, performed_by, TS()))
    row = c.execute("SELECT * FROM gam_rollforward WHERE control=?", (control,)).fetchone()
    c.commit(); c.close()
    return dict(row)


# ── Application Controls ────────────────────────────────────────────────────
def app_controls_list() -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT * FROM gam_app_controls ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


def add_app_control(name, application, control_type, gitc_dependent, notes) -> dict:
    c = db.connect()
    cur = c.execute(
        "INSERT INTO gam_app_controls (name,application,control_type,gitc_dependent,notes) VALUES (?,?,?,?,?)",
        (name, application, control_type, 1 if gitc_dependent else 0, notes))
    row = c.execute("SELECT * FROM gam_app_controls WHERE id=?", (cur.lastrowid,)).fetchone()
    c.commit(); c.close()
    return dict(row)


def delete_app_control(aid: int):
    c = db.connect()
    c.execute("DELETE FROM gam_app_controls WHERE id=?", (aid,))
    c.commit(); c.close()
