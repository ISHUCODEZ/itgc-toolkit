"""
Continuous Controls Monitoring (CCM).

The ITGC toolkit tests controls point-in-time: an auditor uploads an export and
runs a sample once. CCM is the monitoring layer wrapped around the same four
engines — it re-runs every control test on a cadence against the full
population, stores each run as a timestamped snapshot, and trends control health
over time.

The technically interesting core is *stable finding identity*: every exception
gets a fingerprint — a hash of (control + entity + rule) — so the same finding
keeps its identity run to run. That lets us compute new vs resolved vs
persisting between runs, age open findings, and measure mean-time-to-remediate.

Nothing here re-implements the analytical logic — it reuses sod / change_audit /
recert verbatim. CCM is the snapshot store, the fingerprinting, the delta
detection, the lifecycle, and the threshold alerting on top.
"""
from __future__ import annotations
import csv, hashlib, io, json, os, time
from datetime import date, datetime

from . import sod, change_audit, recert, db

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TS_FMT = "%Y-%m-%d %H:%M:%S"

# The three controls CCM monitors. (Framework mapping is a static crosswalk, not
# a population test, so it produces no findings to trend.)
CONTROLS = ("SoD", "Change", "Recert")


# --------------------------------------------------------------------------- #
#  data loading
# --------------------------------------------------------------------------- #
def _read_sample(name: str) -> list[dict]:
    with open(os.path.join(DATA_DIR, name), encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _default_datasets() -> dict:
    return {
        "sod": _read_sample("sample_entitlements.csv"),
        "change": _read_sample("sample_changes.csv"),
        "recert": _read_sample("sample_recert.csv"),
    }


# --------------------------------------------------------------------------- #
#  fingerprinting + finding extraction
# --------------------------------------------------------------------------- #
def fingerprint(control: str, entity: str, rule: str) -> str:
    """Stable identity for a finding across runs."""
    raw = f"{control}|{entity}|{rule}".lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _findings_from_results(sod_r: dict, change_r: dict, recert_r: dict) -> list[dict]:
    """Flatten the three engine outputs into one list of fingerprinted findings."""
    out = []

    # SoD: one finding per (user, conflict rule).
    for f in sod_r["conflicts"]:
        entity, rule = f["user"], f["rule_id"]
        out.append({
            "fingerprint": fingerprint("SoD", entity, rule),
            "control": "SoD", "entity": entity, "rule": rule,
            "severity": f["severity"], "detail": f["rule"],
        })

    # Change: one finding per (change ticket, exception type).
    for row in change_r["results"]:
        for exc in row["exceptions"]:
            entity, rule = row["change_id"], exc["title"]
            out.append({
                "fingerprint": fingerprint("Change", entity, rule),
                "control": "Change", "entity": entity, "rule": rule,
                "severity": exc["severity"], "detail": exc["detail"],
            })

    # Recert: a finding is an at-risk grant (overdue or never reviewed).
    for item in recert_r["items"]:
        if item["status"] not in ("Overdue", "Never reviewed"):
            continue
        entity = f'{item["user"]}@{item["system"]}'
        rule = item["status"]
        sev = "High" if item["status"] == "Never reviewed" else "Medium"
        out.append({
            "fingerprint": fingerprint("Recert", entity, rule),
            "control": "Recert", "entity": entity, "rule": rule,
            "severity": sev,
            "detail": f'{item["access_level"]} · last reviewed {item["last_reviewed"]}',
        })
    return out


def _metrics(sod_r, change_r, recert_r) -> dict:
    """The trendable health metrics for one run."""
    return {
        "sod": {
            "conflict_count": sod_r["conflict_count"],
            "high": sod_r["by_severity"]["High"],
            "medium": sod_r["by_severity"]["Medium"],
            "users_with_conflicts": sod_r["users_with_conflicts"],
        },
        "change": {
            "compliance_rate": change_r["compliance_rate"],
            "flagged": change_r["flagged_changes"],
            "total": change_r["total_changes"],
        },
        "recert": {
            "at_risk": recert_r["at_risk"],
            "current_rate": recert_r["current_rate"],
            "total": recert_r["total_grants"],
        },
    }


# --------------------------------------------------------------------------- #
#  the heartbeat: run all controls and persist a snapshot
# --------------------------------------------------------------------------- #
def run_all_controls(triggered_by: str = "manual", *, datasets: dict | None = None,
                     today: date | None = None, ts: str | None = None,
                     evaluate: bool = True) -> dict:
    """
    Run the four control tests against the full population and persist the result
    as one timestamped snapshot. Returns the run summary (metrics + delta counts).

    triggered_by : 'manual' | 'scheduler' | 'backfill' | a username
    datasets     : optional {sod, change, recert} row lists (defaults to samples)
    today, ts    : overrides used by the backfill to place runs in the past
    """
    ds = datasets or _default_datasets()
    ts = ts or time.strftime(TS_FMT)

    sod_r = sod.analyse(ds["sod"])
    change_r = change_audit.analyse(ds["change"])
    recert_r = recert.analyse(ds["recert"], today=today)

    findings = _findings_from_results(sod_r, change_r, recert_r)
    metrics = _metrics(sod_r, change_r, recert_r)

    c = db.connect()
    cur = c.execute("INSERT INTO ccm_runs (ts, triggered_by, summary) VALUES (?,?,?)",
                    (ts, triggered_by, "{}"))
    run_id = cur.lastrowid

    # previous run's finding set — the basis for run-to-run deltas
    prev = c.execute("SELECT run_id FROM ccm_runs WHERE run_id < ? ORDER BY run_id DESC LIMIT 1",
                     (run_id,)).fetchone()
    prev_set = set()
    if prev:
        prev_set = {r["fingerprint"] for r in c.execute(
            "SELECT fingerprint FROM ccm_run_findings WHERE run_id=?", (prev["run_id"],))}

    this_set = {f["fingerprint"] for f in findings}
    new_fps = this_set - prev_set
    resolved_fps = prev_set - this_set
    persisting_fps = this_set & prev_set

    # record this run's findings + upsert the exception register
    for f in findings:
        c.execute("INSERT OR IGNORE INTO ccm_run_findings (run_id, fingerprint) VALUES (?,?)",
                  (run_id, f["fingerprint"]))
        row = c.execute("SELECT fingerprint, status FROM ccm_exceptions WHERE fingerprint=?",
                        (f["fingerprint"],)).fetchone()
        if row is None:
            c.execute("""INSERT INTO ccm_exceptions
              (fingerprint, control, entity, rule, severity, detail,
               first_seen_run, last_seen_run, first_seen_ts, last_seen_ts, status)
              VALUES (?,?,?,?,?,?,?,?,?,?, 'open')""",
              (f["fingerprint"], f["control"], f["entity"], f["rule"],
               f["severity"], f["detail"], run_id, run_id, ts, ts))
        else:
            # seen again — refresh last_seen; reopen if it had been auto-resolved
            reopen = row["status"] == "resolved"
            c.execute("""UPDATE ccm_exceptions
                         SET last_seen_run=?, last_seen_ts=?, severity=?, detail=?,
                             status=CASE WHEN ?=1 THEN 'open' ELSE status END,
                             resolved_ts=CASE WHEN ?=1 THEN NULL ELSE resolved_ts END
                         WHERE fingerprint=?""",
                      (run_id, ts, f["severity"], f["detail"],
                       1 if reopen else 0, 1 if reopen else 0, f["fingerprint"]))

    # anything previously open but absent this run is auto-resolved (for MTTR).
    # A user-set 'risk-accepted' status is sticky and never auto-flipped.
    for fp in resolved_fps:
        c.execute("""UPDATE ccm_exceptions SET status='resolved', resolved_ts=?
                     WHERE fingerprint=? AND status IN ('open','acknowledged')""", (ts, fp))

    summary = {
        "run_id": run_id, "ts": ts, "triggered_by": triggered_by,
        "metrics": metrics,
        "delta": {"new": len(new_fps), "resolved": len(resolved_fps),
                  "persisting": len(persisting_fps), "total_findings": len(findings)},
        "new_by_severity": {s: sum(1 for f in findings
                                    if f["fingerprint"] in new_fps and f["severity"] == s)
                            for s in ("High", "Medium", "Low")},
    }
    c.execute("UPDATE ccm_runs SET summary=? WHERE run_id=?", (json.dumps(summary), run_id))
    c.commit(); c.close()

    if evaluate:
        summary["alerts_raised"] = _evaluate_thresholds(run_id, ts, summary)
    return summary


# --------------------------------------------------------------------------- #
#  threshold evaluation -> alerts
# --------------------------------------------------------------------------- #
def _metric_value(summary: dict, control: str, metric: str):
    """Resolve a threshold's (control, metric) against a run summary."""
    if metric == "new_high":
        return summary["new_by_severity"]["High"]
    key = {"SoD": "sod", "Change": "change", "Recert": "recert"}.get(control)
    return summary["metrics"].get(key, {}).get(metric)


_OPS = {">": lambda a, b: a > b, "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b}


def _evaluate_thresholds(run_id: int, ts: str, summary: dict) -> int:
    c = db.connect()
    rules = c.execute("SELECT * FROM ccm_thresholds WHERE enabled=1").fetchall()
    breaches = []
    for t in rules:
        val = _metric_value(summary, t["control"], t["metric"])
        if val is None:
            continue
        if _OPS[t["operator"]](val, t["value"]):
            msg = (f'{t["control"]} · {t["metric"]} = {val} '
                   f'{t["operator"]} {t["value"]:g} (threshold breached)')
            c.execute("""INSERT INTO ccm_alerts
                         (ts, run_id, control, metric, message, severity, acknowledged)
                         VALUES (?,?,?,?,?,?,0)""",
                      (ts, run_id, t["control"], t["metric"], msg, t["severity"]))
            breaches.append(msg)
    c.commit(); c.close()
    # log to the activity trail only after the alert write is committed/closed,
    # so we never hold two write transactions on the SQLite file at once.
    for msg in breaches:
        db.log("ccm", "ccm_alert", msg)
    return len(breaches)


def seed_default_thresholds():
    """Install a sensible starter alert pack if none exist yet."""
    c = db.connect()
    n = c.execute("SELECT COUNT(*) n FROM ccm_thresholds").fetchone()["n"]
    if n == 0:
        defaults = [
            ("SoD", "new_high", ">", 0, "High"),
            ("SoD", "conflict_count", ">", 6, "High"),
            ("Change", "compliance_rate", "<", 80, "High"),
            ("Recert", "at_risk", ">", 4, "Medium"),
        ]
        c.executemany("""INSERT INTO ccm_thresholds
                         (control, metric, operator, value, severity, enabled)
                         VALUES (?,?,?,?,?,1)""", defaults)
        c.commit()
    c.close()


# --------------------------------------------------------------------------- #
#  read models for the dashboard
# --------------------------------------------------------------------------- #
def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, TS_FMT)


def trend(limit: int = 30) -> list[dict]:
    """Per-run health metrics in chronological order, for the trend charts."""
    c = db.connect()
    rows = c.execute("SELECT run_id, ts, triggered_by, summary FROM ccm_runs "
                     "ORDER BY run_id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    out = []
    for r in reversed(rows):
        s = json.loads(r["summary"] or "{}")
        m = s.get("metrics", {})
        out.append({
            "run_id": r["run_id"], "ts": r["ts"], "triggered_by": r["triggered_by"],
            "sod_conflicts": m.get("sod", {}).get("conflict_count"),
            "sod_high": m.get("sod", {}).get("high"),
            "change_compliance": m.get("change", {}).get("compliance_rate"),
            "recert_at_risk": m.get("recert", {}).get("at_risk"),
            "recert_current_rate": m.get("recert", {}).get("current_rate"),
            "new": s.get("delta", {}).get("new"),
            "resolved": s.get("delta", {}).get("resolved"),
            "persisting": s.get("delta", {}).get("persisting"),
        })
    return out


def _aging_days(first_seen_ts: str, end_ts: str | None = None) -> int:
    end = _parse_ts(end_ts) if end_ts else datetime.now()
    return max(0, (end - _parse_ts(first_seen_ts)).days)


def kpis() -> dict:
    """Current scorecard: open exceptions, aging buckets, MTTR, latest delta."""
    c = db.connect()
    latest = c.execute("SELECT run_id, ts, summary FROM ccm_runs "
                       "ORDER BY run_id DESC LIMIT 1").fetchone()
    total_runs = c.execute("SELECT COUNT(*) n FROM ccm_runs").fetchone()["n"]
    open_rows = c.execute("""SELECT severity, first_seen_ts FROM ccm_exceptions
                             WHERE status IN ('open','acknowledged')""").fetchall()
    resolved_rows = c.execute("""SELECT first_seen_ts, resolved_ts FROM ccm_exceptions
                                 WHERE status='resolved' AND resolved_ts IS NOT NULL""").fetchall()
    accepted = c.execute("SELECT COUNT(*) n FROM ccm_exceptions WHERE status='risk-accepted'").fetchone()["n"]
    c.close()

    now_ts = latest["ts"] if latest else None
    buckets = {"0-7": 0, "8-30": 0, "31-90": 0, "90+": 0}
    oldest = 0
    for r in open_rows:
        d = _aging_days(r["first_seen_ts"], now_ts)
        oldest = max(oldest, d)
        if d <= 7: buckets["0-7"] += 1
        elif d <= 30: buckets["8-30"] += 1
        elif d <= 90: buckets["31-90"] += 1
        else: buckets["90+"] += 1

    mttr = None
    if resolved_rows:
        spans = [(_parse_ts(r["resolved_ts"]) - _parse_ts(r["first_seen_ts"])).days
                 for r in resolved_rows]
        mttr = round(sum(spans) / len(spans), 1)

    sev = {s: sum(1 for r in open_rows if r["severity"] == s) for s in ("High", "Medium", "Low")}
    summary = json.loads(latest["summary"]) if latest and latest["summary"] else {}
    return {
        "total_runs": total_runs,
        "last_run": now_ts,
        "open_exceptions": len(open_rows),
        "open_by_severity": sev,
        "risk_accepted": accepted,
        "aging_buckets": buckets,
        "oldest_open_days": oldest,
        "mttr_days": mttr,
        "resolved_total": len(resolved_rows),
        "latest_delta": summary.get("delta", {}),
        "latest_metrics": summary.get("metrics", {}),
    }


def reliance_opinion() -> dict:
    """
    Aggregate current control health into an overall ITGC reliance conclusion —
    the judgement an auditor reaches at the end of testing: can controls be
    relied upon, or must substantive testing expand to cover the gaps?

    Deterministic and explainable: a score is reduced by weighted detractors
    (open high-severity exceptions, ageing items, weak change compliance, a
    recert backlog), then mapped to a four-band opinion. Every deduction is
    surfaced as a driver so the conclusion always traces to evidence.
    """
    k = kpis()
    c = db.connect()
    open_rows = c.execute("""SELECT severity, first_seen_ts FROM ccm_exceptions
                             WHERE status IN ('open','acknowledged')""").fetchall()
    c.close()
    now_ts = k["last_run"]
    open_high = sum(1 for r in open_rows if r["severity"] == "High")
    open_med = sum(1 for r in open_rows if r["severity"] == "Medium")
    high_aged = sum(1 for r in open_rows
                    if r["severity"] == "High" and _aging_days(r["first_seen_ts"], now_ts) > 90)

    m = k.get("latest_metrics", {})
    change_comp = m.get("change", {}).get("compliance_rate", 100)
    recert_risk = m.get("recert", {}).get("at_risk", 0)

    score = 100.0
    drivers = []
    if open_high:
        score -= open_high * 7
        drivers.append(f"{open_high} open high-severity exception(s)")
    if high_aged:
        score -= high_aged * 6
        drivers.append(f"{high_aged} high-severity item(s) unremediated &gt;90 days")
    if open_med:
        score -= open_med * 2
        drivers.append(f"{open_med} open medium-severity exception(s)")
    if change_comp < 95:
        score -= (95 - change_comp) * 0.5
        drivers.append(f"change compliance at {change_comp:g}% (target &gt;= 95%)")
    if recert_risk:
        score -= recert_risk * 1.5
        drivers.append(f"{recert_risk} access grant(s) overdue or never recertified")
    score = max(0, min(100, round(score)))

    if score >= 85:
        band, label, color = "RELIANCE", "Reliance can be placed", "#2db757"
        verdict = ("ITGC are operating effectively. Controls reliance is supported; "
                   "substantive testing can be set at a reduced level.")
    elif score >= 70:
        band, label, color = "RELIANCE_EXC", "Reliance with exceptions", "#ffe600"
        verdict = ("Controls are broadly effective but exceptions exist. Reliance is "
                   "supportable with compensating procedures over the noted gaps.")
    elif score >= 50:
        band, label, color = "LIMITED", "Limited reliance", "#ff9d3c"
        verdict = ("Control weaknesses are significant. Reliance is limited; expand "
                   "substantive testing to cover the affected assertions.")
    else:
        band, label, color = "NO_RELIANCE", "Reliance cannot be placed", "#f95d54"
        verdict = ("Pervasive control deficiencies. Controls cannot be relied upon; "
                   "a fully substantive audit approach is required.")
    if not drivers:
        drivers.append("no open exceptions outstanding")

    return {"score": score, "band": band, "band_label": label, "color": color,
            "verdict": verdict, "drivers": drivers, "as_of": now_ts,
            "open_high": open_high, "open_medium": open_med, "high_aged": high_aged}


def exceptions_list(status: str | None = None, control: str | None = None) -> list[dict]:
    c = db.connect()
    q = "SELECT * FROM ccm_exceptions"
    where, params = [], []
    if status and status != "all":
        if status == "open":          # 'open' tab includes acknowledged work-in-progress
            where.append("status IN ('open','acknowledged')")
        else:
            where.append("status=?"); params.append(status)
    if control:
        where.append("control=?"); params.append(control)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY CASE severity WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, first_seen_ts"
    rows = c.execute(q, params).fetchall()
    last = c.execute("SELECT ts FROM ccm_runs ORDER BY run_id DESC LIMIT 1").fetchone()
    c.close()
    now_ts = last["ts"] if last else None
    out = []
    for r in rows:
        d = dict(r)
        d["age_days"] = _aging_days(r["first_seen_ts"],
                                    r["resolved_ts"] or now_ts)
        d["runs_seen"] = (r["last_seen_run"] - r["first_seen_run"]) + 1
        out.append(d)
    return out


def pillar_scores() -> dict:
    """
    Per-pillar health scores (0-100) derived from the latest run metrics and
    open exception counts. Displayed as individual control-area scorecards on
    the CCM dashboard and home page — more actionable than a single blended number.
    """
    k = kpis()
    m = k.get("latest_metrics", {})

    c = db.connect()
    rows = c.execute("""SELECT control, severity, COUNT(*) n FROM ccm_exceptions
                        WHERE status IN ('open','acknowledged')
                        GROUP BY control, severity""").fetchall()
    c.close()

    exc: dict[str, dict] = {}
    for r in rows:
        exc.setdefault(r["control"], {})
        exc[r["control"]][r["severity"]] = r["n"]

    def _band(s):
        if s >= 85: return "strong"
        if s >= 70: return "moderate"
        if s >= 50: return "weak"
        return "critical"

    # SoD score: deduct per open exception
    sod_high = exc.get("SoD", {}).get("High", 0)
    sod_med  = exc.get("SoD", {}).get("Medium", 0)
    sod_score = max(0, round(100 - sod_high * 10 - sod_med * 3))

    # Change score: use compliance rate directly from last run
    change_comp = m.get("change", {}).get("compliance_rate", 100)
    change_score = int(change_comp) if change_comp is not None else 100

    # Recert score: current_rate from last run
    recert_rate = m.get("recert", {}).get("current_rate", 100)
    recert_score = int(recert_rate) if recert_rate is not None else 100

    return {
        "SoD":    {"score": sod_score,    "band": _band(sod_score),
                   "open_high": sod_high, "open_medium": sod_med,
                   "label": "Access · SoD"},
        "Change": {"score": change_score, "band": _band(change_score),
                   "compliance_rate": change_comp,
                   "label": "Change Management"},
        "Recert": {"score": recert_score, "band": _band(recert_score),
                   "current_rate": recert_rate,
                   "label": "Recertification"},
    }


def review_exception(fp: str, reviewed_by: str) -> dict | None:
    """Stamp a reviewer signature + timestamp on a finding — the in-app sign-off."""
    c = db.connect()
    row = c.execute("SELECT fingerprint FROM ccm_exceptions WHERE fingerprint=?", (fp,)).fetchone()
    if not row:
        c.close(); return None
    ts = time.strftime(TS_FMT)
    c.execute("UPDATE ccm_exceptions SET reviewed_by=?, reviewed_ts=? WHERE fingerprint=?",
              (reviewed_by, ts, fp))
    c.commit()
    updated = dict(c.execute("SELECT * FROM ccm_exceptions WHERE fingerprint=?", (fp,)).fetchone())
    c.close()
    return updated


def update_exception(fp: str, status: str | None = None,
                     owner: str | None = None, note: str | None = None) -> dict | None:
    valid = {"open", "acknowledged", "remediated", "risk-accepted", "resolved"}
    c = db.connect()
    row = c.execute("SELECT fingerprint FROM ccm_exceptions WHERE fingerprint=?", (fp,)).fetchone()
    if not row:
        c.close(); return None
    if status in valid:
        # 'remediated' is a terminal manual close — stamp the resolution time for MTTR.
        if status in ("remediated", "resolved"):
            c.execute("UPDATE ccm_exceptions SET status=?, "
                      "resolved_ts=COALESCE(resolved_ts, ?) WHERE fingerprint=?",
                      (status, time.strftime(TS_FMT), fp))
        else:
            c.execute("UPDATE ccm_exceptions SET status=? WHERE fingerprint=?", (status, fp))
    if owner is not None:
        c.execute("UPDATE ccm_exceptions SET owner=? WHERE fingerprint=?", (owner, fp))
    if note is not None:
        c.execute("UPDATE ccm_exceptions SET note=? WHERE fingerprint=?", (note, fp))
    c.commit()
    updated = dict(c.execute("SELECT * FROM ccm_exceptions WHERE fingerprint=?", (fp,)).fetchone())
    c.close()
    return updated


# ---- thresholds ----
def thresholds_list() -> list[dict]:
    c = db.connect()
    rows = [dict(r) for r in c.execute("SELECT * FROM ccm_thresholds ORDER BY id")]
    c.close()
    return rows


def add_threshold(control, metric, operator, value, severity) -> dict:
    c = db.connect()
    cur = c.execute("""INSERT INTO ccm_thresholds
                       (control, metric, operator, value, severity, enabled)
                       VALUES (?,?,?,?,?,1)""",
                    (control, metric, operator, float(value), severity))
    tid = cur.lastrowid
    c.commit()
    row = dict(c.execute("SELECT * FROM ccm_thresholds WHERE id=?", (tid,)).fetchone())
    c.close()
    return row


def delete_threshold(tid: int):
    c = db.connect()
    c.execute("DELETE FROM ccm_thresholds WHERE id=?", (tid,))
    c.commit(); c.close()


# ---- alerts ----
def alerts_list(limit: int = 50) -> list[dict]:
    c = db.connect()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM ccm_alerts ORDER BY id DESC LIMIT ?", (limit,))]
    c.close()
    return rows


def acknowledge_alert(aid: int):
    c = db.connect()
    c.execute("UPDATE ccm_alerts SET acknowledged=1 WHERE id=?", (aid,))
    c.commit(); c.close()


def recent_runs(limit: int = 15) -> list[dict]:
    c = db.connect()
    rows = c.execute("SELECT run_id, ts, triggered_by, summary FROM ccm_runs "
                     "ORDER BY run_id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    out = []
    for r in rows:
        s = json.loads(r["summary"] or "{}")
        out.append({"run_id": r["run_id"], "ts": r["ts"],
                    "triggered_by": r["triggered_by"],
                    "delta": s.get("delta", {}),
                    "metrics": s.get("metrics", {})})
    return out


def current_detail() -> dict:
    """Run the engines on the current population for the report's per-control tabs."""
    ds = _default_datasets()
    return {
        "sod": sod.analyse(ds["sod"]),
        "change": change_audit.analyse(ds["change"]),
        "recert": recert.analyse(ds["recert"]),
    }


def has_history() -> bool:
    c = db.connect()
    n = c.execute("SELECT COUNT(*) n FROM ccm_runs").fetchone()["n"]
    c.close()
    return n > 0
