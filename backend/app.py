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
import csv, io, functools, os
from flask import (Flask, jsonify, request, session, send_from_directory,
                   send_file, Response)
from flask_cors import CORS

from services import sod, change_audit, recert, framework_map, db

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("ITGC_SECRET", "itgc-demo-secret-key-change-me")
CORS(app, supports_credentials=True)
db.init()

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
    return jsonify(db.recent_activity())


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
