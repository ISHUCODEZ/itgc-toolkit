"""
Audit deliverables — turn live CCM data into the artefacts an auditor hands over.

Two outputs are generated in-process and streamed to the browser:

  • an Excel **workpaper** — a tab per control, the full exception register with
    blank sign-off columns, run history and thresholds; the working file an
    auditor annotates and files as evidence, and
  • a PDF **ITGC continuous-monitoring report** — reliance opinion, control
    scorecard and prioritised findings; the summary that goes to management /
    the audit committee.

Both are built from the same CCM read models (reliance opinion, KPIs, trend,
exception register) so the on-screen dashboard and the filed evidence never
disagree.
"""
from __future__ import annotations
import io, time

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether)

from . import ccm

# EY-ish palette so the deliverable looks deliberate, not default.
INK = "1A1A24"
YELLOW = "FFE600"
HEAD_FILL = "262633"
SEV_FILL = {"High": "F95D54", "Medium": "FFE600", "Low": "2DB757"}
SEV_FONT = {"High": "FFFFFF", "Medium": "1A1A24", "Low": "FFFFFF"}

TITLE = "IT General Controls — Continuous Monitoring"


# =========================================================================== #
#  Excel workpaper
# =========================================================================== #
def _hdr(ws, headers, row=1):
    fill = PatternFill("solid", fgColor=HEAD_FILL)
    font = Font(bold=True, color=YELLOW, size=10, name="Calibri")
    align = Alignment(horizontal="left", vertical="center")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.fill = fill; cell.font = font; cell.alignment = align
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    ws.row_dimensions[row].height = 20


def _autosize(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _sev_cell(cell, sev):
    if sev in SEV_FILL:
        cell.fill = PatternFill("solid", fgColor=SEV_FILL[sev])
        cell.font = Font(bold=True, color=SEV_FONT[sev], size=9)
        cell.alignment = Alignment(horizontal="center")


def build_workpaper(generated_by: str = "system") -> io.BytesIO:
    opinion = ccm.reliance_opinion()
    k = ccm.kpis()
    trend = ccm.trend(limit=60)
    excs = ccm.exceptions_list(status="all")
    thresholds = ccm.thresholds_list()
    detail = ccm.current_detail()
    now = time.strftime("%Y-%m-%d %H:%M")

    wb = Workbook()
    thin = Side(style="thin", color="DDDDDD")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # ---- Summary ----
    ws = wb.active; ws.title = "Summary"
    ws["A1"] = TITLE
    ws["A1"].font = Font(bold=True, size=15, color=INK)
    ws["A2"] = "Continuous Controls Monitoring — audit workpaper"
    ws["A2"].font = Font(italic=True, size=10, color="666666")
    meta = [("Report date", now), ("Prepared by", generated_by),
            ("Monitoring runs", k["total_runs"]), ("Last run", k["last_run"] or "—"),
            ("Scope", "Access (SoD), Change Management, Access Recertification")]
    r = 4
    for label, val in meta:
        ws.cell(row=r, column=1, value=label).font = Font(bold=True, size=10)
        ws.cell(row=r, column=2, value=val).font = Font(size=10)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="CONTROLS RELIANCE OPINION").font = Font(bold=True, size=11, color=INK)
    r += 1
    op_cell = ws.cell(row=r, column=1, value=f"{opinion['band_label']}  ·  score {opinion['score']}/100")
    op_cell.font = Font(bold=True, size=12, color="FFFFFF")
    op_cell.fill = PatternFill("solid", fgColor=opinion["color"].lstrip("#"))
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    op_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[r].height = 22
    r += 1
    vc = ws.cell(row=r, column=1, value=opinion["verdict"])
    vc.font = Font(size=10, italic=True); vc.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    ws.row_dimensions[r].height = 42
    r += 1
    ws.cell(row=r, column=1, value="Drivers: " + "; ".join(
        d.replace("&gt;", ">") for d in opinion["drivers"])).font = Font(size=9, color="666666")
    r += 2

    # control scorecard
    ws.cell(row=r, column=1, value="CONTROL SCORECARD").font = Font(bold=True, size=11)
    r += 1
    _hdr(ws, ["Control", "Key metric", "Value", "Status"], row=r)
    m = k.get("latest_metrics", {})
    scard = [
        ("Segregation of Duties", "Open conflicts", m.get("sod", {}).get("conflict_count", "—"),
         "Exceptions" if m.get("sod", {}).get("conflict_count") else "Clean"),
        ("Change Management", "Compliance rate", f'{m.get("change", {}).get("compliance_rate", "—")}%',
         "Effective" if (m.get("change", {}).get("compliance_rate") or 0) >= 95 else "Exceptions"),
        ("Access Recertification", "At-risk grants", m.get("recert", {}).get("at_risk", "—"),
         "Effective" if not m.get("recert", {}).get("at_risk") else "Backlog"),
        ("Exception management", "Open / oldest (days)",
         f'{k["open_exceptions"]} / {k["oldest_open_days"]}', f'MTTR {k["mttr_days"] or "—"}d'),
    ]
    for control, metric, val, status in scard:
        r += 1
        ws.cell(row=r, column=1, value=control)
        ws.cell(row=r, column=2, value=metric)
        ws.cell(row=r, column=3, value=val)
        ws.cell(row=r, column=4, value=status)
    _autosize(ws, [30, 22, 14, 16])

    # ---- Exception Register (the heart of the workpaper) ----
    ws = wb.create_sheet("Exception Register")
    cols = ["Fingerprint", "Control", "Entity", "Rule / finding", "Severity", "Detail",
            "First seen", "Last seen", "Age (days)", "Runs seen", "Status", "Owner",
            "Remediation note", "Auditor conclusion", "Reviewed by", "Date"]
    _hdr(ws, cols)
    for i, e in enumerate(excs, start=2):
        vals = [e["fingerprint"], e["control"], e["entity"], e["rule"], e["severity"],
                e["detail"], e["first_seen_ts"], e["last_seen_ts"], e["age_days"],
                e["runs_seen"], e["status"], e.get("owner", ""), e.get("note", ""),
                "", "", ""]
        for j, v in enumerate(vals, 1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.font = Font(size=9)
            cell.alignment = Alignment(vertical="top", wrap_text=(j in (4, 6, 13)))
            cell.border = border
        _sev_cell(ws.cell(row=i, column=5), e["severity"])
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(2, len(excs)+1)}"
    _autosize(ws, [13, 11, 20, 26, 10, 34, 17, 17, 9, 9, 13, 14, 28, 24, 14, 12])

    # ---- per-control detail tabs ----
    _sod_tab(wb, detail["sod"], border)
    _change_tab(wb, detail["change"], border)
    _recert_tab(wb, detail["recert"], border)

    # ---- Run history ----
    ws = wb.create_sheet("Run History")
    _hdr(ws, ["Run", "Timestamp", "Triggered by", "SoD conflicts", "Change compliance %",
              "Recert at-risk", "New", "Resolved", "Persisting"])
    for i, t in enumerate(trend, start=2):
        for j, v in enumerate([t["run_id"], t["ts"], t["triggered_by"], t["sod_conflicts"],
                               t["change_compliance"], t["recert_at_risk"], t["new"],
                               t["resolved"], t["persisting"]], 1):
            ws.cell(row=i, column=j, value=v).font = Font(size=9)
    _autosize(ws, [7, 20, 13, 14, 18, 14, 8, 10, 11])

    # ---- Thresholds ----
    ws = wb.create_sheet("Alert Thresholds")
    _hdr(ws, ["ID", "Control", "Metric", "Operator", "Value", "Severity", "Enabled"])
    for i, t in enumerate(thresholds, start=2):
        for j, v in enumerate([t["id"], t["control"], t["metric"], t["operator"],
                               t["value"], t["severity"], "Yes" if t["enabled"] else "No"], 1):
            ws.cell(row=i, column=j, value=v).font = Font(size=9)
    _autosize(ws, [6, 12, 20, 10, 10, 10, 9])

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf


def _sod_tab(wb, sod_r, border):
    ws = wb.create_sheet("SoD Conflicts")
    _hdr(ws, ["User", "Rule ID", "Conflict", "Severity", "Conflicting roles", "Risk", "Control ref"])
    for i, f in enumerate(sod_r["conflicts"], start=2):
        for j, v in enumerate([f["user"], f["rule_id"], f["rule"], f["severity"],
                               " + ".join(f["conflicting_roles"]), f["risk"], f["control"]], 1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.font = Font(size=9); cell.alignment = Alignment(vertical="top", wrap_text=(j in (6,)))
            cell.border = border
        _sev_cell(ws.cell(row=i, column=4), f["severity"])
    _autosize(ws, [16, 9, 28, 10, 26, 50, 30])


def _change_tab(wb, change_r, border):
    ws = wb.create_sheet("Change Exceptions")
    _hdr(ws, ["Change ID", "Developer", "Deployer", "Type", "Approved", "Tested",
              "Compliant", "Exceptions"])
    for i, row in enumerate(change_r["results"], start=2):
        exc = "; ".join(f'{e["title"]} ({e["severity"]})' for e in row["exceptions"]) or "—"
        for j, v in enumerate([row["change_id"], row["developer"], row["deployer"], row["type"],
                               "Y" if row["approved"] else "N", "Y" if row["tested"] else "N",
                               "Yes" if row["compliant"] else "No", exc], 1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.font = Font(size=9); cell.alignment = Alignment(vertical="top", wrap_text=(j == 8))
            cell.border = border
            if j == 7 and not row["compliant"]:
                cell.fill = PatternFill("solid", fgColor="FDE7E6")
    _autosize(ws, [14, 16, 16, 11, 9, 8, 10, 44])


def _recert_tab(wb, recert_r, border):
    ws = wb.create_sheet("Recert Backlog")
    _hdr(ws, ["User", "System", "Access level", "Last reviewed", "Cadence (days)", "Status", "Days"])
    fill_map = {"Overdue": "FDE7E6", "Never reviewed": "FDE7E6", "Due soon": "FFF7D6"}
    for i, it in enumerate(recert_r["items"], start=2):
        for j, v in enumerate([it["user"], it["system"], it["access_level"], it["last_reviewed"],
                               it["cadence_days"], it["status"], it["days"]], 1):
            cell = ws.cell(row=i, column=j, value=v)
            cell.font = Font(size=9); cell.border = border
            if j == 6 and it["status"] in fill_map:
                cell.fill = PatternFill("solid", fgColor=fill_map[it["status"]])
    _autosize(ws, [16, 18, 20, 14, 14, 16, 8])


# =========================================================================== #
#  PDF report
# =========================================================================== #
def _page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(18 * mm, 12 * mm,
                      f"{TITLE} · generated {time.strftime('%Y-%m-%d %H:%M')}")
    canvas.drawRightString(192 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#FFE600"))
    canvas.setLineWidth(2)
    canvas.line(18 * mm, 15 * mm, 192 * mm, 15 * mm)
    canvas.restoreState()


def build_pdf(generated_by: str = "system") -> io.BytesIO:
    opinion = ccm.reliance_opinion()
    k = ccm.kpis()
    trend = ccm.trend(limit=60)
    excs = ccm.exceptions_list(status="open")
    m = k.get("latest_metrics", {})

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=22 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm, title=TITLE)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=18, textColor=colors.HexColor("#1A1A24"), spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=9.5, textColor=colors.HexColor("#666666"), spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=colors.HexColor("#1A1A24"),
                        spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("body", parent=ss["Normal"], fontSize=9.5, leading=14)
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#666666"))

    el = []
    el.append(Paragraph(TITLE, h1))
    el.append(Paragraph("Continuous Controls Monitoring report &nbsp;·&nbsp; "
                        f"prepared by <b>{generated_by}</b> &nbsp;·&nbsp; {time.strftime('%d %B %Y')}", sub))

    # opinion banner
    oc = colors.HexColor(opinion["color"])
    op_tbl = Table([[Paragraph(f'<font color="white"><b>CONTROLS RELIANCE OPINION</b></font>', body)],
                    [Paragraph(f'<font color="white" size="14"><b>{opinion["band_label"]}</b>'
                               f' &nbsp; (score {opinion["score"]}/100)</font>', body)]],
                   colWidths=[174 * mm])
    op_tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), oc),
                                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    el.append(op_tbl)
    el.append(Spacer(1, 4))
    el.append(Paragraph(opinion["verdict"], body))
    el.append(Paragraph("<b>Basis for opinion:</b> " + "; ".join(
        d.replace("&gt;", "&gt;") for d in opinion["drivers"]) + ".", small))

    # scorecard
    el.append(Paragraph("Control health scorecard", h2))
    sc_data = [["Control", "Key metric", "Value", "Assessment"],
               ["Segregation of Duties", "Open conflicts",
                str(m.get("sod", {}).get("conflict_count", "—")),
                "Exceptions noted" if m.get("sod", {}).get("conflict_count") else "Clean"],
               ["Change Management", "Compliance rate",
                f'{m.get("change", {}).get("compliance_rate", "—")}%',
                "Effective" if (m.get("change", {}).get("compliance_rate") or 0) >= 95 else "Exceptions noted"],
               ["Access Recertification", "At-risk grants",
                str(m.get("recert", {}).get("at_risk", "—")),
                "Effective" if not m.get("recert", {}).get("at_risk") else "Backlog"]]
    sc = Table(sc_data, colWidths=[48 * mm, 44 * mm, 36 * mm, 46 * mm])
    sc.setStyle(_grey_table())
    el.append(sc)

    # KPI strip
    el.append(Paragraph("Monitoring metrics", h2))
    kpi_data = [["Open exceptions", "High severity", "Oldest open (days)", "MTTR (days)", "Runs"],
                [str(k["open_exceptions"]), str(k["open_by_severity"]["High"]),
                 str(k["oldest_open_days"]), str(k["mttr_days"] or "—"), str(k["total_runs"])]]
    kt = Table(kpi_data, colWidths=[35 * mm] * 5)
    kt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#262633")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#FFE600")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"), ("FONTSIZE", (0, 1), (-1, 1), 13),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD"))]))
    el.append(kt)

    # trend summary line
    if len(trend) >= 2:
        first, last = trend[0], trend[-1]
        el.append(Spacer(1, 6))
        el.append(Paragraph(
            f"Over {len(trend)} monitoring runs ({first['ts'][:10]} → {last['ts'][:10]}): "
            f"SoD conflicts {first['sod_conflicts']} → {last['sod_conflicts']}, "
            f"change compliance {first['change_compliance']}% → {last['change_compliance']}%, "
            f"recert backlog {first['recert_at_risk']} → {last['recert_at_risk']} at-risk grants.", small))

    # findings
    el.append(Paragraph(f"Open findings — prioritised ({len(excs)})", h2))
    if not excs:
        el.append(Paragraph("No open exceptions outstanding.", body))
    else:
        rows = [["#", "Control", "Finding", "Sev", "Age", "Owner / status"]]
        for i, e in enumerate(excs[:25], 1):
            rows.append([str(i), e["control"],
                         Paragraph(f'<b>{e["entity"]}</b> — {e["rule"]}<br/>'
                                   f'<font size="7" color="#666666">{e["detail"]}</font>', small),
                         e["severity"], f'{e["age_days"]}d',
                         Paragraph(f'{e.get("owner") or "—"}<br/>'
                                   f'<font size="7" color="#666666">{e["status"]}</font>', small)])
        ft = Table(rows, colWidths=[7 * mm, 22 * mm, 92 * mm, 14 * mm, 13 * mm, 26 * mm], repeatRows=1)
        st = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#262633")),
              ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#FFE600")),
              ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        for i, e in enumerate(excs[:25], 1):
            c = SEV_FILL.get(e["severity"])
            if c:
                st.append(("BACKGROUND", (3, i), (3, i), colors.HexColor("#" + c)))
                st.append(("TEXTCOLOR", (3, i), (3, i),
                           colors.white if e["severity"] != "Medium" else colors.HexColor("#1A1A24")))
                st.append(("ALIGN", (3, i), (3, i), "CENTER"))
        ft.setStyle(TableStyle(st))
        el.append(ft)
        if len(excs) > 25:
            el.append(Paragraph(f"… and {len(excs) - 25} further open findings (see Excel workpaper).", small))

    el.append(Spacer(1, 10))
    el.append(Paragraph("This report is generated from continuous control tests run against the full "
                        "population. Findings are deterministic and trace to a named rule, risk and "
                        "control reference. Prepared as audit evidence; subject to reviewer sign-off.", small))

    doc.build(el, onFirstPage=_page_footer, onLaterPages=_page_footer)
    buf.seek(0)
    return buf


def _grey_table():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#262633")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#FFE600")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F6F8")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7)])
