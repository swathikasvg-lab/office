import os
import tempfile
from datetime import datetime, timedelta, timezone

import matplotlib.pyplot as plt
import requests
from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Table,
    TableStyle,
    Spacer,
    Image,
    PageBreak,
)

# ==========================
#   TIMEZONE DEFINITIONS
# ==========================
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

# ==========================
#   GROUPING INTERVAL MAP
# ==========================
_INTERVAL_SECONDS = {
    "1m": 60,
    "1h": 3600,
    "12h": 43200,
    "1d": 86400,
}


# ==========================
#     VALUE FORMATTERS
# ==========================
def _format_bytes(v):
    if v is None:
        return "0"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(v)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.1f} {units[idx]}"


def _format_speed_kbps(v_kbps):
    if v_kbps is None:
        return "0"
    units = ["kb/s", "Mb/s", "Gb/s"]
    size = float(v_kbps)
    idx = 0
    while size >= 1000 and idx < len(units) - 1:
        size /= 1000.0
        idx += 1
    return f"{size:.2f} {units[idx]}"


# ==========================
#        DATA FETCHER
# ==========================
def _query_interface_timeseries(device, iface, start_iso, end_iso, interval):
    influx_url = current_app.config["INFLUXDB_URL"]
    influx_db = current_app.config["INFLUXDB_DB"]

    interval_sec = _INTERVAL_SECONDS.get(interval, 60)

    q = f"""
    SELECT 
        non_negative_derivative(mean("ifInOctets"), {interval}) AS "in_deriv",
        non_negative_derivative(mean("ifOutOctets"), {interval}) AS "out_deriv"
    FROM "interface"
    WHERE "hostname" = '{device}'
      AND "ifDescr" =~ /{iface}/
      AND time >= '{start_iso}' AND time <= '{end_iso}'
    GROUP BY time({interval}) fill(null)
    """

    resp = requests.get(influx_url, params={"db": influx_db, "q": q})
    data = resp.json()

    rows = []
    series = data.get("results", [{}])[0].get("series", [])

    if not series:
        return rows

    values = series[0].get("values", [])

    for r in values:
        t_utc = datetime.fromisoformat(r[0].replace("Z", "+00:00")).astimezone(IST)

        in_bytes = r[1] or 0
        out_bytes = r[2] or 0

        total_bytes = in_bytes + out_bytes

        in_kbps = (in_bytes * 8.0 / interval_sec) / 1000.0
        out_kbps = (out_bytes * 8.0 / interval_sec) / 1000.0
        total_kbps = in_kbps + out_kbps

        rows.append(
            {
                "time": t_utc,        # <-- IST datetime object
                "in_bytes": in_bytes,
                "out_bytes": out_bytes,
                "total_bytes": total_bytes,
                "in_kbps": in_kbps,
                "out_kbps": out_kbps,
                "total_kbps": total_kbps,
            }
        )

    return rows


# ==========================
#      SPEED CHART
# ==========================
def _build_speed_chart(rows, device, iface, interval):
    if not rows:
        return None

    times = [r["time"] for r in rows]  # already IST
    ins = [r["in_kbps"] for r in rows]
    outs = [r["out_kbps"] for r in rows]

    fig, ax = plt.subplots(figsize=(9, 2.6))

    ax.plot(times, ins, label="Traffic In (kb/s)", linewidth=1.1)
    ax.plot(times, outs, label="Traffic Out (kb/s)", linewidth=1.1)

    ax.set_title(f"{device} / {iface} – In/Out Speed ({interval})", fontsize=9)
    ax.set_ylabel("kb/s", fontsize=8)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="upper right")

    fig.autofmt_xdate()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    fig.savefig(tmp.name, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return tmp.name


# ==========================
#    ENTERPRISE HEADER
# ==========================
def _add_header(story, template_type, device, interfaces, start_iso, end_iso, interval):

    styles = getSampleStyleSheet()
    blue = colors.HexColor("#0052CC")
    dark = colors.HexColor("#001833")

    # Convert header timestamps to IST
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(IST)
    end_dt   = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone(IST)

    meta_style = ParagraphStyle(
        "MetaWhite",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
    )

    # Logo detection
    logo_path = None
    for path in [
        os.path.join(current_app.root_path, "static", "img", "autointelli.png"),
        os.path.join(current_app.root_path, "static", "logo.png"),
    ]:
        if os.path.isfile(path):
            logo_path = path
            break

    if logo_path:
        logo = Image(logo_path, width=32 * mm, height=12 * mm)
    else:
        logo = Paragraph("<b>Autointelli</b>", ParagraphStyle("Logo", textColor=colors.white, fontSize=14))

    title = Paragraph(
        "<b>Bandwidth Utilization Report</b>",
        ParagraphStyle(
            "TitleWhite",
            parent=styles["Heading1"],
            fontSize=16,
            textColor=colors.white,
            spaceAfter=4,
        ),
    )

    meta_lines = [
        f"<b>Template:</b> {template_type or '-'}",
        f"<b>Device:</b> {device}",
        f"<b>Interfaces:</b> {', '.join(interfaces)}",
        f"<b>Time Range:</b> {start_dt.strftime('%Y-%m-%d %H:%M:%S IST')} → {end_dt.strftime('%Y-%m-%d %H:%M:%S IST')}",
        f"<b>Interval:</b> {interval}",
        "<i>(All times shown in IST)</i>",
    ]

    meta = Paragraph(" &nbsp; | &nbsp; ".join(meta_lines), meta_style)

    t = Table(
        [[logo, title], ["", meta]],
        colWidths=[50 * mm, 200 * mm],
    )

    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), blue),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("SPAN", (1, 0), (1, 0)),
                ("SPAN", (1, 1), (1, 1)),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    story.append(t)
    story.append(Spacer(1, 6 * mm))


# ==========================
#      SUMMARY ROW
# ==========================
def _summary_row(rows):
    if not rows:
        return {"min": 0, "max": 0, "avg": 0, "total_vol": 0}

    speeds = [r["total_kbps"] for r in rows]
    vols = [r["total_bytes"] for r in rows]

    return {
        "min": min(speeds),
        "max": max(speeds),
        "avg": sum(speeds) / len(speeds),
        "total_vol": sum(vols),
    }


# ==========================
#        PDF BUILDER
# ==========================
def build_pdf(template_type, device, interfaces, start, end, interval):

    out_dir = tempfile.gettempdir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    outfile = os.path.join(out_dir, f"rpt_1005_bandwidth_{ts}.pdf")

    doc = SimpleDocTemplate(
        outfile,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
    )

    styles = getSampleStyleSheet()
    iface_heading = ParagraphStyle(
        "IfaceHeading",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#001833"),
        spaceAfter=4,
    )

    story = []

    # Header once on first page
    _add_header(story, template_type, device, interfaces, start, end, interval)

    first = True

    for iface in interfaces:
        rows = _query_interface_timeseries(device, iface, start, end, interval)

        if not first:
            story.append(PageBreak())
            _add_header(story, template_type, device, [iface], start, end, interval)

        first = False

        story.append(Paragraph(f"Interface: {iface}", iface_heading))
        story.append(Spacer(1, 3 * mm))

        # Summary bar
        s = _summary_row(rows)

        summary_data = [
            [
                "Min Speed", _format_speed_kbps(s["min"]),
                "Max Speed", _format_speed_kbps(s["max"]),
                "Avg Speed", _format_speed_kbps(s["avg"]),
                "Total Volume", _format_bytes(s["total_vol"]),
            ]
        ]

        summary_tbl = Table(
            summary_data,
            colWidths=[22 * mm, 32 * mm] * 4,
        )

        summary_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF4FF")),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#001833")),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("ALIGN", (1, 0), (-1, -1), "LEFT"),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D9E8")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#B3C4E6")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )

        story.append(summary_tbl)
        story.append(Spacer(1, 5 * mm))

        # Add traffic chart
        chart_path = _build_speed_chart(rows, device, iface, interval)
        if chart_path:
            story.append(Image(chart_path, width=250 * mm, height=60 * mm))
            story.append(Spacer(1, 5 * mm))

        # Table header
        table_data = [
            [
                "Time (IST)",
                "Traffic Total (Volume)",
                "Traffic Total (Speed)",
                "Traffic In (Volume)",
                "Traffic In (Speed)",
                "Traffic Out (Volume)",
                "Traffic Out (Speed)",
            ]
        ]

        for r in rows:
            ts_str = r["time"].strftime("%Y-%m-%d %H:%M:%S")

            table_data.append(
                [
                    ts_str,
                    _format_bytes(r["total_bytes"]),
                    _format_speed_kbps(r["total_kbps"]),
                    _format_bytes(r["in_bytes"]),
                    _format_speed_kbps(r["in_kbps"]),
                    _format_bytes(r["out_bytes"]),
                    _format_speed_kbps(r["out_kbps"]),
                ]
            )

        tbl = Table(table_data, repeatRows=1)

        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F6FB")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#001833")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                    ("FONTSIZE", (0, 1), (-1, -1), 8),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                    ("ALIGN", (0, 0), (0, -1), "LEFT"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D7DFEC")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FBFCFF")]),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )

        story.append(tbl)

    doc.build(story)
    return outfile

