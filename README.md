# ITGC Controls Toolkit

A working toolkit for testing **IT General Controls (ITGC)** across the core
domains a Technology Risk / Assurance team covers every engagement: access
management, change management, operations & monitoring, and the control
frameworks that tie them together.

Built as a companion to the Equity Technology Risk Lab — where that project is
about *model risk*, this one is about *IT general controls*.

## The four tools

1. **SoD Analyzer** (Access) — upload a user-entitlement export; flags toxic
   role combinations (e.g. create-vendor + approve-payment) against an explicit
   conflict ruleset. Each conflict cites the rule, the fraud risk, and the control.
2. **Change Auditor** (Change) — reads a change-ticket log and tests each change
   for authorisation, test evidence, and developer-vs-deployer segregation.
3. **Recertification Tracker** (Operations) — takes access grants with last-review
   dates and shows which are current, due soon, overdue, or never reviewed.
4. **Framework Map** (cross-cutting) — crosswalks one control across SOX ITGC,
   COBIT 2019, ISO 27001:2022, NIST CSF 2.0 and SOC 2.

Every engine is **deterministic and rule-based**: each finding traces to a named
rule with a risk rationale and a control reference, the way a real audit
exception is documented.

## Run it

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows  (source venv/bin/activate on mac/linux)
pip install -r requirements.txt
python app.py
```

Open http://localhost:5001 and sign in.

| Username | Password | Role | Can |
|----------|----------|------|-----|
| admin | admin123 | Admin | everything |
| auditor | auditor123 | Auditor | run analyses |
| viewer | viewer123 | Viewer | read-only |

Each tool ships with a realistic **sample CSV** (in `backend/data/`) so you can
run it instantly, or upload your own export.

## Architecture

- **Backend** — Flask REST API; one service module per engine
  (`sod.py`, `change_audit.py`, `recert.py`, `framework_map.py`), SQLite for the
  activity audit trail (`db.py`).
- **Frontend** — six pages (home + four tools + audit trail), plain HTML/CSS/JS,
  EY-styled dark theme.
- **Controls the toolkit practises** — role-based access enforced server-side,
  and a full activity audit trail. It practises the controls it tests.

*Illustrative tooling for demonstration — not a production GRC system.*
