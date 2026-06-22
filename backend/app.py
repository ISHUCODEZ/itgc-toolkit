"""
ITGC Controls Toolkit — Flask backend.

A toolkit for testing IT General Controls across the four ITGC pillars:
  Access Management  -> SoD analyzer + recertification tracker
  Change Management  -> change-log auditor
  Operations         -> (recertification cadence + monitoring view)
  Framework mapping  -> control crosswalk (cross-cutting)

Role-based access (admin / auditor / viewer) is enforced server-side, and every
action is written to an activity audit trail — the toolkit practises the
controls it tests.
"""
from __future__ import annotations
import csv, io, functools, os, time
from flask import (Flask, jsonify, request, session, send_from_directory,
                   send_file, Response)
from flask_cors import CORS

from services import sod, change_audit, recert, framework_map, db, ccm, reporting, jml, gam, fait

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("ITGC_SECRET", "itgc-demo-secret-key-change-me")
CORS(app, supports_credentials=True)
db.init()
ccm.seed_default_thresholds()

# ---- users / roles ----
USERS = {
    "admin":   {"password": "admin123",   "role": "admin",   "name": "Admin"},
    "auditor": {"password": "auditor123", "role": "auditor", "name": "Auditor"},
    "viewer":  {"password": "viewer123",  "role": "viewer",  "name": "Viewer"},
}
ROLE_RANK = {"viewer": 0, "auditor": 1, "admin": 2}


def current_user():
    u = session.get("user")
    return USERS.get(u) | {"username": u} if u in USERS else None


def require_role(min_role):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **k):
            u = current_user()
            if not u:
                return jsonify({"error": "login required"}), 401
            if ROLE_RANK[u["role"]] < ROLE_RANK[min_role]:
                return jsonify({"error": f"{min_role} role required"}), 403
            return fn(*a, **k)
        return wrap
    return deco


# ---------- auth ----------
@app.post("/api/login")
def login():
    b = request.get_json(silent=True) or {}
    u = USERS.get(b.get("username"))
    if u and u["password"] == b.get("password"):
        session["user"] = b["username"]
        db.log(b["username"], "login", f"role={u['role']}")
        return jsonify({"username": b["username"], "role": u["role"], "name": u["name"]})
    return jsonify({"error": "invalid credentials"}), 401


@app.post("/api/logout")
def logout():
    u = session.get("user")
    if u:
        db.log(u, "logout", "")
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    u = current_user()
    if not u:
        return jsonify({"user": None})
    return jsonify({"username": u["username"], "role": u["role"], "name": u["name"]})


# ---------- helpers ----------
def _rows_from_upload_or_sample(sample_name):
    """Read CSV rows from an uploaded file, else fall back to the bundled sample."""
    f = request.files.get("file")
    if f and f.filename:
        text = f.read().decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(text))), f.filename
    path = os.path.join(os.path.dirname(__file__), "data", sample_name)
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh)), sample_name + " (sample)"


# ---------- Access: SoD ----------
@app.post("/api/sod")
@require_role("viewer")
def api_sod():
    rows, src = _rows_from_upload_or_sample("sample_entitlements.csv")
    result = sod.analyse(rows)
    result["source"] = src
    db.log(current_user()["username"], "sod_analysis", f"{src}: {result['conflict_count']} conflicts")
    db.save_run(current_user()["username"], "SoD", {"conflicts": result["conflict_count"], "source": src})
    return jsonify(result)


@app.get("/api/sod/rules")
@require_role("viewer")
def api_sod_rules():
    return jsonify(sod.ruleset_summary())


# ---------- Change Management ----------
@app.post("/api/changes")
@require_role("viewer")
def api_changes():
    rows, src = _rows_from_upload_or_sample("sample_changes.csv")
    result = change_audit.analyse(rows)
    result["source"] = src
    db.log(current_user()["username"], "change_audit", f"{src}: {result['flagged_changes']} flagged")
    db.save_run(current_user()["username"], "Change", {"flagged": result["flagged_changes"], "source": src})
    return jsonify(result)


# ---------- Recertification ----------
@app.post("/api/recert")
@require_role("viewer")
def api_recert():
    rows, src = _rows_from_upload_or_sample("sample_recert.csv")
    cadence = int(request.form.get("cadence", 90))
    result = recert.analyse(rows, cadence_days=cadence)
    result["source"] = src
    db.log(current_user()["username"], "recert_review", f"{src}: {result['at_risk']} at risk")
    return jsonify(result)


# ---------- JML: Terminated User Access ----------
@app.post("/api/jml")
@require_role("viewer")
def api_jml():
    term_f = request.files.get("terminated")
    ent_f  = request.files.get("entitlements")

    if term_f and term_f.filename:
        term_rows = list(csv.DictReader(io.StringIO(
            term_f.read().decode("utf-8-sig", errors="replace"))))
        term_src = term_f.filename
    else:
        path = os.path.join(os.path.dirname(__file__), "data", "sample_terminated.csv")
        with open(path, encoding="utf-8") as fh:
            term_rows = list(csv.DictReader(fh))
        term_src = "sample_terminated.csv (sample)"

    if ent_f and ent_f.filename:
        ent_rows = list(csv.DictReader(io.StringIO(
            ent_f.read().decode("utf-8-sig", errors="replace"))))
        ent_src = ent_f.filename
    else:
        path = os.path.join(os.path.dirname(__file__), "data", "sample_entitlements.csv")
        with open(path, encoding="utf-8") as fh:
            ent_rows = list(csv.DictReader(fh))
        ent_src = "sample_entitlements.csv (sample)"

    result = jml.analyse(term_rows, ent_rows)
    result["term_source"] = term_src
    result["ent_source"] = ent_src
    user = current_user()["username"]
    db.log(user, "jml_review",
           f"{term_src}: {result['users_with_active_access']} users with orphaned access")
    db.save_run(user, "JML", {"affected": result["users_with_active_access"],
                               "findings": result["finding_count"]})
    return jsonify(result)


# ---------- Framework mapping ----------
@app.get("/api/framework")
@require_role("viewer")
def api_framework():
    q = request.args.get("q", "")
    data = framework_map.search(q) if q else framework_map.all_mappings()
    return jsonify({"frameworks": framework_map.FRAMEWORKS, "mappings": data, "query": q})


# ---------- activity ----------
@app.get("/api/activity")
@require_role("viewer")
def api_activity():
    user_f   = request.args.get("user", "")
    action_f = request.args.get("action", "")
    return jsonify(db.recent_activity(limit=200, user=user_f, action=action_f))


@app.get("/api/activity/filters")
@require_role("viewer")
def api_activity_filters():
    return jsonify({"users": db.activity_users(), "actions": db.activity_actions()})


@app.get("/api/activity/export")
@require_role("viewer")
def api_activity_export():
    user_f   = request.args.get("user", "")
    action_f = request.args.get("action", "")
    rows = db.recent_activity(limit=5000, user=user_f, action=action_f)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["id", "ts", "username", "action", "detail"])
    w.writeheader(); w.writerows(rows)
    db.log(current_user()["username"], "activity_export",
           f"filter user={user_f or '*'} action={action_f or '*'} rows={len(rows)}")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=ITGC_AuditTrail_{time.strftime('%Y%m%d')}.csv"})


# ---------- Continuous Controls Monitoring (CCM) ----------
@app.post("/api/ccm/run")
@require_role("auditor")
def api_ccm_run():
    """Manual 'Run now' — sweep all four controls and store a snapshot."""
    user = current_user()["username"]
    summary = ccm.run_all_controls(triggered_by=user)
    db.log(user, "ccm_run", f"run #{summary['run_id']}: "
           f"{summary['delta']['new']} new, {summary['delta']['resolved']} resolved, "
           f"{summary.get('alerts_raised', 0)} alerts")
    return jsonify(summary)


@app.get("/api/ccm/dashboard")
@require_role("viewer")
def api_ccm_dashboard():
    has = ccm.has_history()
    return jsonify({
        "has_history": has,
        "opinion": ccm.reliance_opinion() if has else None,
        "kpis": ccm.kpis(),
        "trend": ccm.trend(limit=30),
        "recent_runs": ccm.recent_runs(limit=12),
        "alerts": ccm.alerts_list(limit=20),
    })


@app.get("/api/ccm/exceptions")
@require_role("viewer")
def api_ccm_exceptions():
    status = request.args.get("status", "open")
    control = request.args.get("control") or None
    return jsonify(ccm.exceptions_list(status=status, control=control))


@app.post("/api/ccm/exceptions/<fingerprint>")
@require_role("auditor")
def api_ccm_update_exception(fingerprint):
    b = request.get_json(silent=True) or {}
    updated = ccm.update_exception(fingerprint, status=b.get("status"),
                                   owner=b.get("owner"), note=b.get("note"))
    if not updated:
        return jsonify({"error": "exception not found"}), 404
    db.log(current_user()["username"], "ccm_exception_update",
           f"{fingerprint[:8]} -> {b.get('status') or updated['status']}")
    return jsonify(updated)


@app.get("/api/ccm/thresholds")
@require_role("viewer")
def api_ccm_thresholds():
    return jsonify(ccm.thresholds_list())


@app.post("/api/ccm/thresholds")
@require_role("admin")
def api_ccm_add_threshold():
    b = request.get_json(silent=True) or {}
    try:
        row = ccm.add_threshold(b["control"], b["metric"], b["operator"],
                                b["value"], b.get("severity", "Medium"))
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "control, metric, operator, value required"}), 400
    db.log(current_user()["username"], "ccm_threshold_add",
           f"{row['control']}.{row['metric']} {row['operator']} {row['value']:g}")
    return jsonify(row)


@app.delete("/api/ccm/thresholds/<int:tid>")
@require_role("admin")
def api_ccm_delete_threshold(tid):
    ccm.delete_threshold(tid)
    db.log(current_user()["username"], "ccm_threshold_delete", f"#{tid}")
    return jsonify({"ok": True})


@app.post("/api/ccm/alerts/<int:aid>/ack")
@require_role("auditor")
def api_ccm_ack_alert(aid):
    ccm.acknowledge_alert(aid)
    db.log(current_user()["username"], "ccm_alert_ack", f"#{aid}")
    return jsonify({"ok": True})


@app.get("/api/ccm/opinion")
@require_role("viewer")
def api_ccm_opinion():
    return jsonify(ccm.reliance_opinion())


@app.get("/api/ccm/pillar-scores")
@require_role("viewer")
def api_ccm_pillar_scores():
    return jsonify(ccm.pillar_scores())


@app.post("/api/ccm/exceptions/<fingerprint>/review")
@require_role("auditor")
def api_ccm_review_exception(fingerprint):
    user = current_user()["username"]
    updated = ccm.review_exception(fingerprint, reviewed_by=user)
    if not updated:
        return jsonify({"error": "exception not found"}), 404
    db.log(user, "ccm_exception_review", f"{fingerprint[:8]} reviewed by {user}")
    return jsonify(updated)


# ---------- home dashboard ----------
@app.get("/api/dashboard")
@require_role("viewer")
def api_dashboard():
    has = ccm.has_history()
    result = {"has_history": has}
    if has:
        result["opinion"] = ccm.reliance_opinion()
        result["kpis"]    = ccm.kpis()
        result["pillar_scores"] = ccm.pillar_scores()
    return jsonify(result)


# ---------- audit deliverables (Excel workpaper / PDF report) ----------
@app.get("/api/report/xlsx")
@require_role("viewer")
def api_report_xlsx():
    if not ccm.has_history():
        return jsonify({"error": "no monitoring runs yet — run controls first"}), 400
    user = current_user()["username"]
    buf = reporting.build_workpaper(generated_by=user)
    db.log(user, "report_export", "Excel workpaper")
    fname = f"ITGC_CCM_Workpaper_{time.strftime('%Y%m%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/report/pdf")
@require_role("viewer")
def api_report_pdf():
    if not ccm.has_history():
        return jsonify({"error": "no monitoring runs yet — run controls first"}), 400
    user = current_user()["username"]
    buf = reporting.build_pdf(generated_by=user)
    db.log(user, "report_export", "PDF report")
    fname = f"ITGC_CCM_Report_{time.strftime('%Y%m%d')}.pdf"
    return send_file(buf, as_attachment=True, download_name=fname, mimetype="application/pdf")


@app.get("/api/report/gam/pdf")
@require_role("viewer")
def api_report_gam_pdf():
    if not ccm.has_history():
        return jsonify({"error": "no monitoring runs yet — run controls first"}), 400
    user = current_user()["username"]
    buf = reporting.build_gam_pdf(generated_by=user)
    db.log(user, "report_export", "GAM GITC Summary Memo PDF")
    fname = f"GITC_Summary_Memo_{time.strftime('%Y%m%d')}.pdf"
    return send_file(buf, as_attachment=True, download_name=fname, mimetype="application/pdf")


@app.get("/api/report/gam/xlsx")
@require_role("viewer")
def api_report_gam_xlsx():
    if not ccm.has_history():
        return jsonify({"error": "no monitoring runs yet — run controls first"}), 400
    user = current_user()["username"]
    buf = reporting.build_gam_workpaper(generated_by=user)
    db.log(user, "report_export", "GAM GITC Workpaper XLSX")
    fname = f"GITC_Workpaper_{time.strftime('%Y%m%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/report/fait/pdf")
@require_role("viewer")
def api_report_fait_pdf():
    if not ccm.has_history():
        return jsonify({"error": "no monitoring runs yet — run controls first"}), 400
    user = current_user()["username"]
    buf = reporting.build_fait_pdf(generated_by=user)
    db.log(user, "report_export", "FAIT Management Letter PDF")
    fname = f"FAIT_Management_Letter_{time.strftime('%Y%m%d')}.pdf"
    return send_file(buf, as_attachment=True, download_name=fname, mimetype="application/pdf")


@app.get("/api/report/fait/xlsx")
@require_role("viewer")
def api_report_fait_xlsx():
    if not ccm.has_history():
        return jsonify({"error": "no monitoring runs yet — run controls first"}), 400
    user = current_user()["username"]
    buf = reporting.build_fait_workpaper(generated_by=user)
    db.log(user, "report_export", "FAIT Workpaper XLSX")
    fname = f"FAIT_Workpaper_{time.strftime('%Y%m%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# =========================================================================== #
#  GAM — Global Audit Methodology routes
# =========================================================================== #
@app.get("/api/gam/risk-assessment")
@require_role("viewer")
def api_gam_risk():
    return jsonify({
        "factors": gam.risk_assessment(),
        "overall_risk": gam.overall_risk(),
    })


@app.post("/api/gam/risk-assessment")
@require_role("auditor")
def api_gam_risk_save():
    b = request.get_json(silent=True) or {}
    row = gam.save_risk_factor(b.get("factor",""), b.get("assessment","Moderate"),
                               b.get("implication",""), b.get("notes",""))
    db.log(current_user()["username"], "gam_risk_save", b.get("factor",""))
    return jsonify(row)


@app.get("/api/gam/systems")
@require_role("viewer")
def api_gam_systems():
    return jsonify(gam.systems_list())


@app.post("/api/gam/systems")
@require_role("auditor")
def api_gam_systems_add():
    b = request.get_json(silent=True) or {}
    row = gam.add_system(b.get("name",""), b.get("type",""), b.get("process",""),
                         b.get("hosting",""), b.get("in_scope", True),
                         b.get("domains",""), b.get("cuecs",""), b.get("notes",""))
    db.log(current_user()["username"], "gam_system_add", row["name"])
    return jsonify(row)


@app.put("/api/gam/systems/<int:sid>")
@require_role("auditor")
def api_gam_systems_update(sid):
    b = request.get_json(silent=True) or {}
    row = gam.update_system(sid, **b)
    return jsonify(row or {"error": "not found"}), (200 if row else 404)


@app.delete("/api/gam/systems/<int:sid>")
@require_role("auditor")
def api_gam_systems_delete(sid):
    gam.delete_system(sid)
    db.log(current_user()["username"], "gam_system_delete", str(sid))
    return jsonify({"ok": True})


@app.get("/api/gam/domains")
@require_role("viewer")
def api_gam_domains():
    return jsonify(gam.domain_overview())


@app.get("/api/gam/ipeitor")
@require_role("viewer")
def api_gam_ipeitor():
    return jsonify(gam.ipeitor_list())


@app.post("/api/gam/ipeitor")
@require_role("auditor")
def api_gam_ipeitor_add():
    b = request.get_json(silent=True) or {}
    row = gam.add_ipeitor(b.get("name",""), b.get("system",""),
                          b.get("control_supported",""),
                          b.get("completeness_tested", False),
                          b.get("accuracy_tested", False),
                          b.get("tested_by",""), b.get("test_date",""), b.get("notes",""))
    db.log(current_user()["username"], "gam_ipeitor_add", row["name"])
    return jsonify(row)


@app.delete("/api/gam/ipeitor/<int:iid>")
@require_role("auditor")
def api_gam_ipeitor_delete(iid):
    gam.delete_ipeitor(iid)
    return jsonify({"ok": True})


@app.get("/api/gam/rollforward")
@require_role("viewer")
def api_gam_rollforward():
    return jsonify(gam.rollforward_list())


@app.post("/api/gam/rollforward")
@require_role("auditor")
def api_gam_rollforward_save():
    b = request.get_json(silent=True) or {}
    row = gam.save_rollforward(b.get("control",""), b.get("interim_date",""),
                               b.get("yearend_date",""), b.get("changes",""),
                               b.get("procedures",""), b.get("conclusion",""),
                               b.get("performed_by",""))
    db.log(current_user()["username"], "gam_rollforward_save", b.get("control",""))
    return jsonify(row)


@app.get("/api/gam/app-controls")
@require_role("viewer")
def api_gam_appcontrols():
    return jsonify(gam.app_controls_list())


@app.post("/api/gam/app-controls")
@require_role("auditor")
def api_gam_appcontrols_add():
    b = request.get_json(silent=True) or {}
    row = gam.add_app_control(b.get("name",""), b.get("application",""),
                              b.get("control_type",""), b.get("gitc_dependent", True),
                              b.get("notes",""))
    db.log(current_user()["username"], "gam_appcontrol_add", row["name"])
    return jsonify(row)


@app.delete("/api/gam/app-controls/<int:aid>")
@require_role("auditor")
def api_gam_appcontrols_delete(aid):
    gam.delete_app_control(aid)
    return jsonify({"ok": True})


# =========================================================================== #
#  FAIT — Financial Audit IT routes
# =========================================================================== #
@app.get("/api/fait/deficiencies")
@require_role("viewer")
def api_fait_deficiencies():
    return jsonify(fait.deficiency_summary())


@app.post("/api/fait/exceptions/<fingerprint>/override")
@require_role("auditor")
def api_fait_exc_override(fingerprint):
    b = request.get_json(silent=True) or {}
    row = fait.save_exception_override(fingerprint, **b)
    db.log(current_user()["username"], "fait_exc_override", fingerprint[:8])
    return jsonify(row)


@app.get("/api/fait/racm")
@require_role("viewer")
def api_fait_racm():
    return jsonify(fait.racm_list())


@app.post("/api/fait/racm")
@require_role("auditor")
def api_fait_racm_add():
    b = request.get_json(silent=True) or {}
    row = fait.add_racm(b.get("control_ref",""), b.get("domain",""),
                        b.get("objective",""), b.get("risk",""),
                        b.get("assertion",""), b.get("approach",""),
                        b.get("population",""), b.get("conclusion",""))
    db.log(current_user()["username"], "fait_racm_add", row["control_ref"])
    return jsonify(row)


@app.put("/api/fait/racm/<int:rid>")
@require_role("auditor")
def api_fait_racm_update(rid):
    b = request.get_json(silent=True) or {}
    row = fait.update_racm(rid, **b)
    return jsonify(row or {"error": "not found"}), (200 if row else 404)


@app.delete("/api/fait/racm/<int:rid>")
@require_role("auditor")
def api_fait_racm_delete(rid):
    fait.delete_racm(rid)
    return jsonify({"ok": True})


@app.get("/api/fait/walkthroughs")
@require_role("viewer")
def api_fait_walkthroughs():
    return jsonify(fait.walkthroughs_list())


@app.post("/api/fait/walkthroughs")
@require_role("auditor")
def api_fait_walkthroughs_save():
    b = request.get_json(silent=True) or {}
    row = fait.save_walkthrough(b.get("control",""), b.get("description",""),
                                b.get("evidence",""), b.get("design_ok", True),
                                b.get("conclusion",""), b.get("performed_by",""),
                                b.get("walkthrough_date",""))
    db.log(current_user()["username"], "fait_walkthrough_save", b.get("control",""))
    return jsonify(row)


@app.get("/api/fait/prior-year")
@require_role("viewer")
def api_fait_prior_year():
    return jsonify(fait.prior_year_list())


@app.post("/api/fait/prior-year")
@require_role("auditor")
def api_fait_prior_year_add():
    b = request.get_json(silent=True) or {}
    row = fait.add_prior_year(b.get("finding",""), b.get("control",""),
                              b.get("classification","Control Deficiency"),
                              b.get("prior_status","Open"), b.get("current_status","Open"),
                              b.get("repeat", True), b.get("notes",""))
    db.log(current_user()["username"], "fait_prior_year_add", row["finding"])
    return jsonify(row)


@app.put("/api/fait/prior-year/<int:pid>")
@require_role("auditor")
def api_fait_prior_year_update(pid):
    b = request.get_json(silent=True) or {}
    row = fait.update_prior_year(pid, **b)
    return jsonify(row or {"error": "not found"}), (200 if row else 404)


@app.delete("/api/fait/prior-year/<int:pid>")
@require_role("auditor")
def api_fait_prior_year_delete(pid):
    fait.delete_prior_year(pid)
    return jsonify({"ok": True})


@app.get("/api/fait/prog-dev")
@require_role("viewer")
def api_fait_prog_dev():
    return jsonify(fait.prog_dev_checklist())


# ---------- static frontend ----------
@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/<path:page>")
def pages(page):
    if page.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    candidate = os.path.join(FRONTEND, page)
    if os.path.isfile(candidate):
        return send_from_directory(FRONTEND, page)
    html = page + ".html"
    if os.path.isfile(os.path.join(FRONTEND, html)):
        return send_from_directory(FRONTEND, html)
    return send_from_directory(FRONTEND, "index.html")


# ---------- CCM scheduler ----------
# The heartbeat: re-run every control test on a cadence. APScheduler is
# optional — if it isn't installed the app still works (manual "Run now" only).
def _start_scheduler():
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("[ccm] APScheduler not installed — scheduled runs disabled "
              "(manual 'Run now' still works). pip install apscheduler to enable.")
        return
    interval_hours = float(os.environ.get("CCM_INTERVAL_HOURS", "24"))
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(lambda: ccm.run_all_controls(triggered_by="scheduler"),
                  "interval", hours=interval_hours, id="ccm_sweep",
                  next_run_time=None)
    sched.start()
    print(f"[ccm] scheduler started — controls sweep every {interval_hours:g}h")


if __name__ == "__main__":
    # only start the scheduler in the main reloader process, not the watcher
    if os.environ.get("WERKZEUG_RUN_MAIN") != "false":
        _start_scheduler()
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
