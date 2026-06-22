"""SQLite persistence: users, activity audit trail, saved analysis runs."""
from __future__ import annotations
import sqlite3, json, os, time, hashlib

DB_PATH = os.path.join(os.path.dirname(__file__), "itgc.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# Public alias — the CCM service opens its own connections against the same DB.
def connect():
    return _conn()


def init():
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS activity (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT, username TEXT, action TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT, username TEXT, module TEXT, summary TEXT);

    -- ---- Continuous Controls Monitoring (CCM) ----
    -- Each scheduled/manual sweep of all four engines is one timestamped run.
    CREATE TABLE IF NOT EXISTS ccm_runs (
      run_id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT, triggered_by TEXT, summary TEXT);
    -- A finding keeps its identity across runs via a stable fingerprint, so we
    -- can age it and compute new / resolved / persisting between runs.
    CREATE TABLE IF NOT EXISTS ccm_exceptions (
      fingerprint TEXT PRIMARY KEY,
      control TEXT, entity TEXT, rule TEXT, severity TEXT, detail TEXT,
      first_seen_run INTEGER, last_seen_run INTEGER,
      first_seen_ts TEXT, last_seen_ts TEXT, resolved_ts TEXT,
      status TEXT DEFAULT 'open', owner TEXT DEFAULT '', note TEXT DEFAULT '');
    -- Which fingerprints were observed in which run (drives run-to-run deltas).
    CREATE TABLE IF NOT EXISTS ccm_run_findings (
      run_id INTEGER, fingerprint TEXT,
      PRIMARY KEY (run_id, fingerprint));
    -- Alerting rules: breach when a run metric crosses the threshold.
    CREATE TABLE IF NOT EXISTS ccm_thresholds (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      control TEXT, metric TEXT, operator TEXT, value REAL,
      severity TEXT, enabled INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS ccm_alerts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT, run_id INTEGER, control TEXT, metric TEXT,
      message TEXT, severity TEXT, acknowledged INTEGER DEFAULT 0);
    """)
    c.commit()
    # add sign-off columns to ccm_exceptions if upgrading from an older schema
    for col, definition in [("reviewed_by", "TEXT DEFAULT ''"),
                             ("reviewed_ts",  "TEXT DEFAULT NULL")]:
        try:
            c.execute(f"ALTER TABLE ccm_exceptions ADD COLUMN {col} {definition}")
            c.commit()
        except Exception:
            pass  # column already exists
    c.close()


def log(username: str, action: str, detail: str = ""):
    c = _conn()
    c.execute("INSERT INTO activity (ts, username, action, detail) VALUES (?,?,?,?)",
              (time.strftime("%Y-%m-%d %H:%M:%S"), username, action, detail))
    c.commit(); c.close()


def recent_activity(limit: int = 100, user: str = "", action: str = ""):
    c = _conn()
    where, params = [], []
    if user:
        where.append("username=?"); params.append(user)
    if action:
        where.append("action=?"); params.append(action)
    q = "SELECT * FROM activity"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = c.execute(q, params).fetchall()
    c.close()
    return [dict(r) for r in rows]


def activity_users() -> list[str]:
    """Distinct usernames in the activity log — for the filter dropdown."""
    c = _conn()
    rows = c.execute("SELECT DISTINCT username FROM activity ORDER BY username").fetchall()
    c.close()
    return [r["username"] for r in rows]


def activity_actions() -> list[str]:
    """Distinct action types in the activity log — for the filter dropdown."""
    c = _conn()
    rows = c.execute("SELECT DISTINCT action FROM activity ORDER BY action").fetchall()
    c.close()
    return [r["action"] for r in rows]


def save_run(username: str, module: str, summary: dict):
    c = _conn()
    c.execute("INSERT INTO runs (ts, username, module, summary) VALUES (?,?,?,?)",
              (time.strftime("%Y-%m-%d %H:%M:%S"), username, module, json.dumps(summary)))
    c.commit(); c.close()


def recent_runs(limit: int = 20):
    c = _conn()
    rows = c.execute("SELECT id, ts, username, module, summary FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r); d["summary"] = json.loads(d["summary"]); out.append(d)
    return out
