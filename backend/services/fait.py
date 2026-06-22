"""FAIT (Financial Audit IT) data service.

Provides: Deficiency classification, five-component findings (editable),
RACM, walkthrough checklist, prior year comparison, IPE/ITOR (shared with GAM).
"""
from __future__ import annotations
import time
from . import db, ccm
from .reporting import _classify, _five_component

TS = lambda: time.strftime("%Y-%m-%d %H:%M:%S")

WALKTHROUGH_CONTROLS = ["SoD", "Change", "Recert", "JML"]


# ── Deficiency Classification ────────────────────────────────────────────────
def classified_exceptions() -> list[dict]:
    """All open exceptions with auto-classification + any saved overrides."""
    excs  = ccm.exceptions_list(status="open")
    c     = db.connect()
    ovrs  = {r["fingerprint"]: dict(r)
             for r in c.execute("SELECT * FROM fait_exc_override").fetchall()}
    c.close()

    out = []
    for e in excs:
        ovr = ovrs.get(e["fingerprint"], {})
        auto_cls = _classify(e)
        fc = _five_component(e)
        out.append({
            **e,
            "auto_classification": auto_cls,
            "classification": ovr.get("classification") or auto_cls,
            "condition":      ovr.get("condition_text") or fc["condition"],
            "criteria":       ovr.get("criteria_text")  or fc["criteria"],
            "cause":          ovr.get("cause_text")     or fc["cause"],
            "effect":         ovr.get("effect_text")    or fc["effect"],
            "recommendation": ovr.get("recommendation_text") or fc["recommendation"],
            "mgmt_response":  ovr.get("mgmt_response", ""),
        })
    return out


def deficiency_summary() -> dict:
    excs = classified_exceptions()
    mw = [e for e in excs if e["classification"] == "Material Weakness"]
    sd = [e for e in excs if e["classification"] == "Significant Deficiency"]
    cd = [e for e in excs if e["classification"] == "Control Deficiency"]
    return {
        "total": len(excs),
        "material_weakness": len(mw),
        "significant_deficiency": len(sd),
        "control_deficiency": len(cd),
        "exceptions": excs,
    }


def save_exception_override(fingerprint: str, **kwargs) -> dict:
    allowed = {"classification", "condition_text", "criteria_text",
               "cause_text", "effect_text", "recommendation_text", "mgmt_response"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    updates["fingerprint"] = fingerprint
    updates["updated_ts"]  = TS()
    cols = list(updates.keys())
    placeholders = ", ".join("?" * len(cols))
    sets = ", ".join(f"{k}=excluded.{k}" for k in cols if k != "fingerprint")
    c = db.connect()
    c.execute(
        f"INSERT INTO fait_exc_override ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(fingerprint) DO UPDATE SET {sets}",
        list(updates.values()))
    c.commit(); c.close()
    return updates


# ── RACM ────────────────────────────────────────────────────────────────────
def racm_list() -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT * FROM fait_racm ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


def add_racm(control_ref, domain, objective, risk, assertion,
             approach, population, conclusion) -> dict:
    c = db.connect()
    cur = c.execute(
        """INSERT INTO fait_racm
           (control_ref,domain,objective,risk,assertion,approach,population,conclusion)
           VALUES (?,?,?,?,?,?,?,?)""",
        (control_ref, domain, objective, risk, assertion, approach, population, conclusion))
    row = c.execute("SELECT * FROM fait_racm WHERE id=?", (cur.lastrowid,)).fetchone()
    c.commit(); c.close()
    return dict(row)


def update_racm(rid: int, **kwargs) -> dict | None:
    allowed = {"control_ref","domain","objective","risk","assertion",
               "approach","population","conclusion"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return None
    sets = ", ".join(f"{k}=?" for k in updates)
    c = db.connect()
    c.execute(f"UPDATE fait_racm SET {sets} WHERE id=?", list(updates.values()) + [rid])
    row = c.execute("SELECT * FROM fait_racm WHERE id=?", (rid,)).fetchone()
    c.commit(); c.close()
    return dict(row) if row else None


def delete_racm(rid: int):
    c = db.connect()
    c.execute("DELETE FROM fait_racm WHERE id=?", (rid,))
    c.commit(); c.close()


# ── Walkthrough Checklist ────────────────────────────────────────────────────
def walkthroughs_list() -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT * FROM fait_walkthroughs ORDER BY id").fetchall()
    c.close()
    existing = {r["control"]: dict(r) for r in rows}
    # ensure all four controls always appear
    out = []
    for ctrl in WALKTHROUGH_CONTROLS:
        if ctrl in existing:
            out.append(existing[ctrl])
        else:
            out.append({"id": None, "control": ctrl, "description": "",
                        "evidence": "", "design_ok": 1, "conclusion": "",
                        "performed_by": "", "walkthrough_date": ""})
    return out


def save_walkthrough(control, description, evidence, design_ok,
                     conclusion, performed_by, walkthrough_date) -> dict:
    c = db.connect()
    existing = c.execute("SELECT id FROM fait_walkthroughs WHERE control=?",
                         (control,)).fetchone()
    if existing:
        c.execute("""UPDATE fait_walkthroughs SET
                     description=?, evidence=?, design_ok=?,
                     conclusion=?, performed_by=?, walkthrough_date=?
                     WHERE control=?""",
                  (description, evidence, 1 if design_ok else 0,
                   conclusion, performed_by, walkthrough_date, control))
    else:
        c.execute("""INSERT INTO fait_walkthroughs
                     (control,description,evidence,design_ok,conclusion,performed_by,walkthrough_date)
                     VALUES (?,?,?,?,?,?,?)""",
                  (control, description, evidence, 1 if design_ok else 0,
                   conclusion, performed_by, walkthrough_date))
    row = c.execute("SELECT * FROM fait_walkthroughs WHERE control=?", (control,)).fetchone()
    c.commit(); c.close()
    return dict(row)


# ── Prior Year Comparison ────────────────────────────────────────────────────
def prior_year_list() -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT * FROM fait_prior_year ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


def add_prior_year(finding, control, classification,
                   prior_status, current_status, repeat, notes) -> dict:
    c = db.connect()
    cur = c.execute(
        """INSERT INTO fait_prior_year
           (finding,control,classification,prior_status,current_status,repeat,notes)
           VALUES (?,?,?,?,?,?,?)""",
        (finding, control, classification, prior_status,
         current_status, 1 if repeat else 0, notes))
    row = c.execute("SELECT * FROM fait_prior_year WHERE id=?", (cur.lastrowid,)).fetchone()
    c.commit(); c.close()
    return dict(row)


def update_prior_year(pid: int, **kwargs) -> dict | None:
    allowed = {"finding","control","classification","prior_status",
               "current_status","repeat","notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return None
    sets = ", ".join(f"{k}=?" for k in updates)
    c = db.connect()
    c.execute(f"UPDATE fait_prior_year SET {sets} WHERE id=?", list(updates.values()) + [pid])
    row = c.execute("SELECT * FROM fait_prior_year WHERE id=?", (pid,)).fetchone()
    c.commit(); c.close()
    return dict(row) if row else None


def delete_prior_year(pid: int):
    c = db.connect()
    c.execute("DELETE FROM fait_prior_year WHERE id=?", (pid,))
    c.commit(); c.close()


# ── Program Development checklist (static questions, no DB needed) ──────────
PROG_DEV_CHECKLIST = [
    ("Project governance",      "Was there a formal project plan and steering committee?"),
    ("Requirements sign-off",   "Were business requirements formally approved before build?"),
    ("Testing",                 "Was independent UAT performed before go-live?"),
    ("Parallel run",            "Was a parallel run or phased cutover performed?"),
    ("Go-live approval",        "Was there a formal go-live authorisation from management?"),
    ("Data migration",          "Was data migration tested for completeness and accuracy?"),
    ("Access setup",            "Was access provisioned following the SoD ruleset?"),
    ("Training",                "Was user training completed before go-live?"),
    ("Post-implementation",     "Was a post-implementation review performed within 90 days?"),
    ("DR / BCP testing",        "Was disaster recovery tested after implementation?"),
]

def prog_dev_checklist() -> list[dict]:
    return [{"area": a, "question": q} for a, q in PROG_DEV_CHECKLIST]
