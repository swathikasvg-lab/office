# reports/fortigate/pdf_1008.py

import os
import tempfile
from datetime import datetime, timezone, timedelta
import math
import requests
from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, Image, PageBreak
)

IST = timezone(timedelta(hours=5, minutes=30))


# ===================================================================
# Utility Helpers
# ===================================================================
def _secs_to_rounded_human(s):
    if not s or s <= 0:
        return "0 min"
    minutes = int(round(s / 60.0))
    if minutes < 60:
        return f"{minutes} min"
    h = minutes // 60
    r = minutes % 60
    if r == 0:
        return f"{h} hr"
    return f"{h} hr {r} min"


def _human_bytes_auto(b):
    if b is None:
        return "0 B"
    try:
        b = float(b)
    except:
        return "0 B"

    if b < 1024:
        return f"{int(b)} B"
    kb = b / 1024
    if kb < 1024:
        return f"{kb:.2f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.2f} MB"
    gb = mb / 1024
    if gb < 1024:
        return f"{gb:.2f} GB"
    tb = gb / 1024
    return f"{tb:.2f} TB"


def _parse_bytes_for_sort(s):
    if not isinstance(s, str):
        return 0
    try:
        if s.endswith(" TB"):
            return float(s[:-3]) * 1024 ** 4
        if s.endswith(" GB"):
            return float(s[:-3]) * 1024 ** 3
        if s.endswith(" MB"):
            return float(s[:-3]) * 1024 ** 2
        if s.endswith(" KB"):
            return float(s[:-3]) * 1024
        if s.endswith(" B"):
            return float(s[:-2])
        return float(s)
    except:
        return 0


# ===================================================================
# Influx Queries
# ===================================================================
def _fetch_tunnels_latest(device, start_iso, end_iso):
    influx_url = (
        current_app.config.get("FORTIGATE_INFLUXDB_URL")
        or current_app.config.get("INFLUXDB_URL")
        or os.environ.get("FORTIGATE_INFLUXDB_URL")
        or os.environ.get("INFLUXDB_URL")
        or "http://127.0.0.1:8086/query"
    )
    influx_db = (
        current_app.config.get("FORTIGATE_INFLUXDB_DB")
        or os.environ.get("FORTIGATE_INFLUXDB_DB")
        or "fortigate"
    )

    q = f"""
    SELECT LAST("fgVpnTunEntStatus") AS status,
           LAST("fgVpnTunEntLifeSecs") AS life_secs,
           LAST("fgVpnTunEntRemGwyIp") AS remote_gw,
           LAST("fgVpnTunEntPhase2Name") AS phase2
    FROM "vpn_tunnels"
    WHERE "hostname" = '{device}'
      AND time >= '{start_iso}' AND time <= '{end_iso}'
    GROUP BY "vpn_name"
    """

    resp = requests.get(influx_url, params={"db": influx_db, "q": q}, timeout=30).json()
    series = resp.get("results", [{}])[0].get("series", [])
    out = []

    for s in series:
        tags = s.get("tags", {})
        vpn = tags.get("vpn_name", "unknown")
        vals = s.get("values", [])
        if not vals:
            continue
        v = vals[-1]
        out.append({
            "vpn_name": vpn,
            "status_code": int(v[1] or 0),
            "life_secs": int(v[2] or 0),
            "remote_gw": v[3] or "",
            "phase2": v[4] or ""
        })

    return out


def _fetch_volume_deltas(device, start_iso, end_iso):
    influx_url = (
        current_app.config.get("FORTIGATE_INFLUXDB_URL")
        or current_app.config.get("INFLUXDB_URL")
        or os.environ.get("FORTIGATE_INFLUXDB_URL")
        or os.environ.get("INFLUXDB_URL")
        or "http://127.0.0.1:8086/query"
    )
    influx_db = (
        current_app.config.get("FORTIGATE_INFLUXDB_DB")
        or os.environ.get("FORTIGATE_INFLUXDB_DB")
        or "fortigate"
    )

    q = f"""
    SELECT FIRST("fgVpnTunEntInOctets") AS in_first,
           FIRST("fgVpnTunEntOutOctets") AS out_first,
           LAST("fgVpnTunEntInOctets") AS in_last,
           LAST("fgVpnTunEntOutOctets") AS out_last
    FROM "vpn_tunnels"
    WHERE "hostname" = '{device}'
      AND time >= '{start_iso}' AND time <= '{end_iso}'
    GROUP BY "vpn_name"
    """

    resp = requests.get(influx_url, params={"db": influx_db, "q": q}, timeout=30).json()
    series = resp.get("results", [{}])[0].get("series", [])
    out = {}

    for s in series:
        vpn = s.get("tags", {}).get("vpn_name", "unknown")
        v = s.get("values", [])
        if not v:
            continue
        v = v[-1]

        inf = int(v[1] or 0)
        outf = int(v[2] or 0)
        inl = int(v[3] or 0)
        outl = int(v[4] or 0)

        in_d = max(0, inl - inf)
        out_d = max(0, outl - outf)

        out[vpn] = {"in_delta": in_d, "out_delta": out_d}

    return out


# ===================================================================
# Header Block
# ===================================================================
def _add_header(story, device, start_iso, end_iso, interval, total, up, down):
    styles = getSampleStyleSheet()
    blue = colors.HexColor("#0052CC")

    start_ist = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(IST)
    end_ist = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone(IST)

    # logo
    logo_path = os.path.join(current_app.root_path, "static/img/autointelli.png")
    logo = Image(logo_path, width=38 * mm, height=14 * mm)

    title = Paragraph(
        "<b>Fortigate VPN Performance Report</b>",
        ParagraphStyle("ttl", fontSize=17, textColor=colors.white)
    )

    meta = Paragraph(
        f"<b>Device:</b> {device} &nbsp;&nbsp;|&nbsp;&nbsp;"
        f"<b>Range:</b> {start_ist.strftime('%Y-%m-%d %H:%M:%S')} → {end_ist.strftime('%Y-%m-%d %H:%M:%S')} IST "
        f"&nbsp;&nbsp;|&nbsp;&nbsp; <b>Interval:</b> {interval}",
        ParagraphStyle("meta", fontSize=9, textColor=colors.white)
    )

    t = Table(
        [[logo, title],
         ["", meta]],
        colWidths=[65 * mm, 220 * mm]
    )

    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), blue),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("SPAN", (1, 0), (1, 0)),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    story.append(t)
    story.append(Spacer(1, 6 * mm))

    # KPI row
    k = Table([
        [f"Total Tunnels: {total}", f"UP: {up}", f"DOWN: {down}"]
    ], colWidths=[80 * mm, 50 * mm, 50 * mm])

    k.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#E8F4FF")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#C8FFC8")),
        ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#FFCCCC")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
    ]))

    story.append(k)
    story.append(Spacer(1, 6 * mm))


# ===================================================================
# Main PDF Builder
# ===================================================================
def build_pdf(device, start, end, interval):
    out_file = os.path.join(
        tempfile.gettempdir(),
        f"rpt_1008_fortigate_{device}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    )

    doc = SimpleDocTemplate(
        out_file,
        pagesize=landscape(A4),
        leftMargin=10 * mm,      # <<< wider margin
        rightMargin=10 * mm,     # <<< wider margin
        topMargin=8 * mm,
        bottomMargin=8 * mm
    )

    story = []

    tunnels = _fetch_tunnels_latest(device, start, end)
    deltas = _fetch_volume_deltas(device, start, end)

    # attach delta
    for t in tunnels:
        d = deltas.get(t["vpn_name"], {"in_delta": 0, "out_delta": 0})
        t["in_delta"] = d["in_delta"]
        t["out_delta"] = d["out_delta"]

    # availability calc
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    duration = max(1, (end_dt - start_dt).total_seconds())

    for t in tunnels:
        pct = (t["life_secs"] / duration) * 100
        t["availability_pct"] = min(max(pct, 0), 100)

    total = len(tunnels)
    up_count = sum(1 for t in tunnels if t["status_code"] == 2)
    down_count = sum(1 for t in tunnels if t["status_code"] == 1)

    # header
    _add_header(story, device, start, end, interval, total, up_count, down_count)


    # ======================================================================
    # TOP-10 TABLES (unchanged except improved font/padding)
    # ======================================================================

    def build_top10(title, rows, headers, status_index=None, availability_index=None):
        story.append(Paragraph(f"<b>{title}</b>", ParagraphStyle("h2", fontSize=11)))
        story.append(Spacer(1, 3 * mm))

        col_widths = [98, 45, 110, 98, 75, 75]

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3FF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("FONTSIZE", (0, 1), (-1, -1), 8),        # <<< increased from 7.5
            ("LEFTPADDING", (0, 0), (-1, -1), 3),     # <<< increased padding
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),      # <<< increased padding
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),   # <<< increased padding
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D2D7E0")),
            ("ALIGN", (3, 1), (-1, -1), "RIGHT")
        ]))

        # apply coloring
        dyn = []
        for i, row in enumerate(rows[1:], start=1):
            if status_index is not None:
                s = row[status_index]
                if s == "UP":
                    dyn.append(("BACKGROUND", (status_index, i), (status_index, i), colors.HexColor("#CCFFCC")))
                elif s == "DOWN":
                    dyn.append(("BACKGROUND", (status_index, i), (status_index, i), colors.HexColor("#FFCCCC")))
                else:
                    dyn.append(("BACKGROUND", (status_index, i), (status_index, i), colors.HexColor("#EDEDED")))

            if availability_index is not None:
                pct = float(row[availability_index].replace("%", ""))
                if pct < 50:
                    dyn.append(("BACKGROUND", (availability_index, i), (availability_index, i), colors.HexColor("#FFCCCC")))
                elif pct < 75:
                    dyn.append(("BACKGROUND", (availability_index, i), (availability_index, i), colors.HexColor("#FFE6B3")))
                else:
                    dyn.append(("BACKGROUND", (availability_index, i), (availability_index, i), colors.HexColor("#CCFFCC")))

        tbl.setStyle(TableStyle(dyn))
        story.append(tbl)
        story.append(Spacer(1, 6 * mm))


    # ---- Top 10 In
    t10 = sorted(tunnels, key=lambda x: -x["in_delta"])[:10]
    rows = [["VPN Name", "Status", "Uptime", "In Volume", "Out Volume"]]
    for t in t10:
        rows.append([
            t["vpn_name"],
            "UP" if t["status_code"] == 2 else "DOWN",
            _secs_to_rounded_human(t["life_secs"]),
            _human_bytes_auto(t["in_delta"]),
            _human_bytes_auto(t["out_delta"])
        ])
    build_top10("Top 10 Tunnels — In Volume", rows, rows[0], status_index=1)

    # ---- Top 10 Out
    t10 = sorted(tunnels, key=lambda x: -x["out_delta"])[:10]
    rows = [["VPN Name", "Status", "Uptime", "Out Volume", "In Volume"]]
    for t in t10:
        rows.append([
            t["vpn_name"],
            "UP" if t["status_code"] == 2 else "DOWN",
            _secs_to_rounded_human(t["life_secs"]),
            _human_bytes_auto(t["out_delta"]),
            _human_bytes_auto(t["in_delta"])
        ])
    build_top10("Top 10 Tunnels — Out Volume", rows, rows[0], status_index=1)

    # ---- Top 10 Uptime
    t10 = sorted(tunnels, key=lambda x: -x["life_secs"])[:10]
    rows = [["VPN Name", "Status", "Uptime", "In Volume", "Out Volume"]]
    for t in t10:
        rows.append([
            t["vpn_name"],
            "UP" if t["status_code"] == 2 else "DOWN",
            _secs_to_rounded_human(t["life_secs"]),
            _human_bytes_auto(t["in_delta"]),
            _human_bytes_auto(t["out_delta"])
        ])
    build_top10("Top 10 Tunnels — Uptime", rows, rows[0], status_index=1)

    # ---- Top 10 Worst Availability
    t10 = sorted(tunnels, key=lambda x: x["availability_pct"])[:10]
    rows = [["VPN Name", "Status", "Availability %", "Uptime", "In Volume", "Out Volume"]]
    for t in t10:
        rows.append([
            t["vpn_name"],
            "UP" if t["status_code"] == 2 else "DOWN",
            f"{t['availability_pct']:.1f}%",
            _secs_to_rounded_human(t["life_secs"]),
            _human_bytes_auto(t["in_delta"]),
            _human_bytes_auto(t["out_delta"])
        ])
    build_top10("Top 10 — Worst Availability", rows, rows[0], status_index=1, availability_index=2)


    # ======================================================================
    # DETAILED TABLE (Anti-cramped improvements)
    # ======================================================================

    story.append(Paragraph("<b>VPN Detailed Table</b>", ParagraphStyle("h2", fontSize=12)))
    story.append(Spacer(1, 5 * mm))   # <<< EXTRA SPACING ADDED

    header = ["VPN Name", "Status", "Phase2", "Remote GW", "Uptime", "In Volume", "Out Volume"]

    # wider columns
    col_widths = [98, 45, 110, 98, 75, 75, 75]

    small_style = ParagraphStyle("S", fontSize=8, leading=9.8)  # <<< larger font

    # build rows
    drows = []
    for t in tunnels:
        drows.append([
            Paragraph(t["vpn_name"], small_style),
            "UP" if t["status_code"] == 2 else "DOWN",
            Paragraph(t["phase2"], small_style),
            Paragraph(t["remote_gw"], small_style),
            _secs_to_rounded_human(t["life_secs"]),
            _human_bytes_auto(t["in_delta"]),
            _human_bytes_auto(t["out_delta"])
        ])

    # sort: UP first
    drows = sorted(drows, key=lambda r: (0 if r[1] == "UP" else 1, -_parse_bytes_for_sort(r[5])))

    per_page = 26
    idx = 0
    total_rows = len(drows)

    while idx < total_rows:
        chunk = [header] + drows[idx:idx + per_page]

        tbl = Table(chunk, colWidths=col_widths, repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3FF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCD2DB")),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),    # <<< bigger padding
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("ALIGN", (5, 1), (-1, -1), "RIGHT")
        ]

        # status color
        dyn = []
        for r_idx, row in enumerate(chunk[1:], start=1):
            if row[1] == "UP":
                dyn.append(("BACKGROUND", (1, r_idx), (1, r_idx), colors.HexColor("#CCFFCC")))
            elif row[1] == "DOWN":
                dyn.append(("BACKGROUND", (1, r_idx), (1, r_idx), colors.HexColor("#FFCCCC")))
            else:
                dyn.append(("BACKGROUND", (1, r_idx), (1, r_idx), colors.HexColor("#EDEDED")))

        tbl.setStyle(TableStyle(style + dyn))
        story.append(tbl)

        idx += per_page
        if idx < total_rows:
            story.append(PageBreak())

    doc.build(story)
    return out_file

