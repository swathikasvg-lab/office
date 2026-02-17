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

# ---------------------------------------------------
#  TIMEZONES
# ---------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


# ---------------------------------------------------
#  DATA FETCH: PORT TIMESERIES
# ---------------------------------------------------
def _query_port_timeseries(server, port, start_iso, end_iso, interval):
    """
    Query InfluxDB for port response performance.

    Assumptions:
      - measurement: net_response
      - response_time is in seconds (float)
      - result_code = 0 means success; non-zero means failure (we use sum(result_code) ~ failures)

    Returns list of dicts:
      {
        "time": IST datetime,
        "mean_ms": float,
        "max_ms": float,
        "attempts": int,
        "failures": int,
        "availability": float,
      }
    """
    influx_url = current_app.config["INFLUXDB_URL"]
    influx_db = current_app.config["INFLUXDB_DB"]

    q = f"""
    SELECT
        mean("response_time") AS mean_rt,
        max("response_time")  AS max_rt,
        count("result_code")  AS attempts,
        sum("result_code")    AS failures
    FROM "net_response"
    WHERE "server" = '{server}'
      AND "port" = '{port}'
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

    for v in values:
        # v: [time, mean_rt(sec), max_rt(sec), attempts, failures_sum]
        t_utc = datetime.fromisoformat(v[0].replace("Z", "+00:00"))
        t_ist = t_utc.astimezone(IST)

        mean_sec = float(v[1] or 0.0)
        max_sec = float(v[2] or 0.0)
        attempts = int(v[3] or 0)
        failures_sum = int(v[4] or 0)

        failures = failures_sum
        availability = 0.0
        if attempts > 0:
            availability = ((attempts - failures) / attempts) * 100.0

        rows.append(
            {
                "time": t_ist,
                "mean_ms": mean_sec * 1000.0,
                "max_ms": max_sec * 1000.0,
                "attempts": attempts,
                "failures": failures,
                "availability": availability,
            }
        )

    return rows


# ---------------------------------------------------
#  SUMMARY METRICS
# ---------------------------------------------------
def _summary(rows):
    if not rows:
        return {
            "avg_mean": 0.0,
            "max_of_max": 0.0,
            "overall_availability": 0.0,
        }

    mean_vals = [r["mean_ms"] for r in rows]
    max_vals = [r["max_ms"] for r in rows]

    total_attempts = sum(r["attempts"] for r in rows)
    total_failures = sum(r["failures"] for r in rows)

    overall_availability = 0.0
    if total_attempts > 0:
        overall_availability = (
            (total_attempts - total_failures) / total_attempts * 100.0
        )

    return {
        "avg_mean": sum(mean_vals) / len(mean_vals),
        "max_of_max": max(max_vals),
        "overall_availability": overall_availability,
    }


# ---------------------------------------------------
#  HEADER (Enterprise – Autointelli theme)
# ---------------------------------------------------
def _add_header(story, targets_labels, start_iso, end_iso, interval):
    styles = getSampleStyleSheet()
    blue = colors.HexColor("#0052CC")

    start_ist = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(IST)
    end_ist = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone(IST)

    # Preferred logo path
    logo_path = None
    for p in [
        os.path.join(current_app.root_path, "static", "img", "autointelli.png"),
        os.path.join(current_app.root_path, "static", "img", "logo.png"),
        os.path.join(current_app.root_path, "static", "logo.png"),
    ]:
        if os.path.isfile(p):
            logo_path = p
            break

    if logo_path:
        logo = Image(logo_path, width=32 * mm, height=12 * mm)
    else:
        logo = Paragraph(
            "<b>Autointelli</b>",
            ParagraphStyle("Logo", textColor=colors.white, fontSize=14),
        )

    title = Paragraph(
        "<b>Port Performance Report</b>",
        ParagraphStyle(
            "TitleWhite",
            parent=styles["Heading1"],
            fontSize=16,
            textColor=colors.white,
        ),
    )

    meta_style = ParagraphStyle(
        "MetaWhite",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
    )

    meta_lines = [
        f"<b>Targets:</b> {', '.join(targets_labels)}",
        f"<b>Time Range:</b> {start_ist.strftime('%Y-%m-%d %H:%M:%S IST')} → {end_ist.strftime('%Y-%m-%d %H:%M:%S IST')}",
        f"<b>Interval:</b> {interval}",
        f"<b>Generated:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "<i>(All times shown in IST)</i>",
    ]

    meta = Paragraph(" &nbsp; | &nbsp; ".join(meta_lines), meta_style)

    t = Table([[logo, title], ["", meta]], colWidths=[50 * mm, 200 * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), blue),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("SPAN", (1, 0), (1, 0)),
                ("SPAN", (1, 1), (1, 1)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )

    story.append(t)
    story.append(Spacer(1, 6 * mm))


# ---------------------------------------------------
#  RESPONSE TIME CHART
# ---------------------------------------------------
def _build_response_chart(rows, label, interval):
    if not rows:
        return None

    times = [r["time"] for r in rows]
    mean_vals = [r["mean_ms"] for r in rows]
    max_vals = [r["max_ms"] for r in rows]

    fig, ax = plt.subplots(figsize=(9, 2.6))
    ax.plot(times, mean_vals, label="Mean Response (ms)", linewidth=1.1)
    ax.plot(times, max_vals, label="Max Response (ms)", linewidth=1.1)

    ax.set_title(f"{label} – Response Time ({interval})", fontsize=9)
    ax.set_ylabel("ms")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="upper right")
    fig.autofmt_xdate()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    fig.savefig(tmp.name, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return tmp.name


# ---------------------------------------------------
#  BUILD PDF
# ---------------------------------------------------
def build_pdf(targets, start, end, interval):
    """
    Build Port Performance PDF (Report 1007) with Autointelli branding.

    targets: list of dicts: {"server": str, "port": int}
    start, end: UTC ISO strings (e.g. "2025-12-02T06:00:00Z")
    interval: InfluxDB GROUP BY interval (e.g. "1m", "5m")
    """
    out_dir = tempfile.gettempdir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    outfile = os.path.join(out_dir, f"rpt_1007_port_{ts}.pdf")

    doc = SimpleDocTemplate(
        outfile,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
    )

    styles = getSampleStyleSheet()
    target_heading = ParagraphStyle(
        "TargetHeading",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#001833"),
        spaceAfter=4,
    )

    # Labels like "mail.autointelli.com:587"
    labels = []
    for t in targets:
        server = t.get("server") or ""
        port = t.get("port")
        if port is not None:
            labels.append(f"{server}:{port}")
        else:
            labels.append(server)

    story = []

    # Global header
    _add_header(story, labels, start, end, interval)

    first = True
    for t in targets:
        server = t["server"]
        port = t["port"]
        if port is None:
            continue

        label = f"{server}:{port}"
        rows = _query_port_timeseries(server, port, start, end, interval)

        if not first:
            story.append(PageBreak())
            _add_header(story, [label], start, end, interval)
        first = False

        story.append(Paragraph(f"Target: {label}", target_heading))
        story.append(Spacer(1, 3 * mm))

        # Summary
        s = _summary(rows)
        summary_data = [
            [
                "Avg Response", f"{s['avg_mean']:.2f} ms",
                "Max Response", f"{s['max_of_max']:.2f} ms",
                "Availability", f"{s['overall_availability']:.2f} %",
            ]
        ]

        summary_tbl = Table(summary_data)
        summary_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF4FF")),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#001833")),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D9E8")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#B3C4E6")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(summary_tbl)
        story.append(Spacer(1, 5 * mm))

        # Chart
        chart_path = _build_response_chart(rows, label, interval)
        if chart_path:
            story.append(Image(chart_path, width=250 * mm, height=60 * mm))
            story.append(Spacer(1, 5 * mm))

        # Table header + data
        table_data = [
            [
                "Time (IST)",
                "Mean Resp (ms)",
                "Max Resp (ms)",
                "Attempts",
                "Failures",
                "Availability (%)",
            ]
        ]

        for r in rows:
            table_data.append(
                [
                    r["time"].strftime("%Y-%m-%d %H:%M:%S"),
                    f"{r['mean_ms']:.2f}",
                    f"{r['max_ms']:.2f}",
                    r["attempts"],
                    r["failures"],
                    f"{r['availability']:.2f}",
                ]
            )

        tbl = Table(table_data, repeatRows=1)

        # --------------------------
        #  Dynamic color coding
        # --------------------------
        row_styles = []

        # Data rows in table_data start at index 1 (0 is header)
        # Columns: 0=Time, 1=Mean, 2=Max, 3=Attempts, 4=Failures, 5=Availability
        for idx, r in enumerate(rows, start=1):
            mean = r["mean_ms"]
            avail = r["availability"]
            bg = colors.white

            # Response time – based on Mean
            if mean > 1000:
                bg = colors.HexColor("#FFCCCC")  # Red
            elif mean > 500:
                bg = colors.HexColor("#FFDD99")  # Amber
            elif mean > 250:
                bg = colors.HexColor("#FFF2CC")  # Light Orange

            # Availability overrides latency
            if avail < 50:
                bg = colors.HexColor("#FFCCCC")  # Red
            elif avail < 75:
                bg = colors.HexColor("#FFDD99")  # Amber
            else:
                bg = colors.HexColor("#CCFFCC")  # Green

            row_styles.append(("BACKGROUND", (0, idx), (-1, idx), bg))

        base_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F6FB")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#001833")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D7DFEC")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]

        tbl.setStyle(TableStyle(base_style + row_styles))
        story.append(tbl)

    doc.build(story)
    return outfile

