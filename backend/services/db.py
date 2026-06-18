"""SQLite persistence: users, activity audit trail, saved analysis runs."""
from __future__ import annotations
import sqlite3, json, os, time, hashlib

DB_PATH = os.path.join(os.path.dirname(__file__), "itgc.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS activity (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT, username TEXT, action TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT, username TEXT, module TEXT, summary TEXT);
    """)
    c.commit(); c.close()


def log(username: str, action: str, detail: str = ""):
    c = _conn()
    c.execute("INSERT INTO activity (ts, username, action, detail) VALUES (?,?,?,?)",
              (time.strftime("%Y-%m-%d %H:%M:%S"), username, action, detail))
    c.commit(); c.close()


def recent_activity(limit: int = 100):
    c = _conn()
    rows = c.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


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
