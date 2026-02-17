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

_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "30m": 1800,
    "1h": 3600,
}


# ---------------------------------------------------
#  DATA FETCH: PING TIMESERIES
# ---------------------------------------------------
def _query_ping_timeseries(url, start_iso, end_iso, interval):
    """
    Query InfluxDB for ping stats for a given URL/IP.

    Returns list of dicts:
      {
        "time": IST datetime,
        "avg_ms": float,
        "min_ms": float,
        "max_ms": float,
        "stddev_ms": float,
        "tx": int,
        "rx": int,
        "loss_pct": float,
      }
    """
    influx_url = current_app.config["INFLUXDB_URL"]
    influx_db = current_app.config["INFLUXDB_DB"]

    q = f"""
    SELECT
        mean("average_response_ms") AS avg_rtt,
        min("minimum_response_ms") AS min_rtt,
        max("maximum_response_ms") AS max_rtt,
        mean("standard_deviation_ms") AS stddev_rtt,
        sum("packets_transmitted") AS tx,
        sum("packets_received") AS rx,
        mean("percent_packet_loss") AS loss_pct
    FROM "ping"
    WHERE "url" = '{url}'
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
        # v: [time, avg_rtt, min_rtt, max_rtt, stddev_rtt, tx, rx, loss_pct]
        t_utc = datetime.fromisoformat(v[0].replace("Z", "+00:00"))
        t_ist = t_utc.astimezone(IST)

        rows.append(
            {
                "time": t_ist,
                "avg_ms": float(v[1] or 0.0),
                "min_ms": float(v[2] or 0.0),
                "max_ms": float(v[3] or 0.0),
                "stddev_ms": float(v[4] or 0.0),
                "tx": int(v[5] or 0),
                "rx": int(v[6] or 0),
                "loss_pct": float(v[7] or 0.0),
            }
        )

    return rows


# ---------------------------------------------------
#  SUMMARY METRICS
# ---------------------------------------------------
def _summary(rows):
    if not rows:
        return {
            "avg": 0.0,
            "min": 0.0,
            "max": 0.0,
            "stddev": 0.0,
            "loss": 0.0,
            "availability": 0.0,
        }

    avg_vals = [r["avg_ms"] for r in rows]
    min_vals = [r["min_ms"] for r in rows]
    max_vals = [r["max_ms"] for r in rows]
    std_vals = [r["stddev_ms"] for r in rows]
    loss_vals = [r["loss_pct"] for r in rows]

    loss_avg = sum(loss_vals) / len(loss_vals)
    availability = max(0.0, 100.0 - loss_avg)

    return {
        "avg": sum(avg_vals) / len(avg_vals),
        "min": min(min_vals),
        "max": max(max_vals),
        "stddev": sum(std_vals) / len(std_vals),
        "loss": loss_avg,
        "availability": availability,
    }


# ---------------------------------------------------
#  HEADER (Enterprise – Autointelli theme)
# ---------------------------------------------------
def _add_header(story, urls, start_iso, end_iso, interval):
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
        "<b>Ping Performance Report</b>",
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
        f"<b>Targets:</b> {', '.join(urls)}",
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
#  LATENCY CHART
# ---------------------------------------------------
def _build_latency_chart(rows, url, interval):
    if not rows:
        return None

    times = [r["time"] for r in rows]  # IST datetimes
    avg = [r["avg_ms"] for r in rows]
    mx = [r["max_ms"] for r in rows]

    fig, ax = plt.subplots(figsize=(9, 2.6))
    ax.plot(times, avg, label="Avg RTT (ms)", linewidth=1.1)
    ax.plot(times, mx, label="Max RTT (ms)", linewidth=1.1)

    ax.set_title(f"{url} – Latency ({interval})", fontsize=9)
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
def build_pdf(urls, start, end, interval):
    """
    Build final Ping Performance PDF with Autointelli branding + color-coded table.
    """
    out_dir = tempfile.gettempdir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    outfile = os.path.join(out_dir, f"rpt_1006_ping_{ts}.pdf")

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

    story = []

    # Main header for all targets
    _add_header(story, urls, start, end, interval)

    first = True
    for url in urls:
        rows = _query_ping_timeseries(url, start, end, interval)

        if not first:
            story.append(PageBreak())
            _add_header(story, [url], start, end, interval)
        first = False

        story.append(Paragraph(f"Target: {url}", target_heading))
        story.append(Spacer(1, 3 * mm))

        # Summary block
        s = _summary(rows)
        summary_data = [
            [
                "Avg RTT", f"{s['avg']:.2f} ms",
                "Min RTT", f"{s['min']:.2f} ms",
                "Max RTT", f"{s['max']:.2f} ms",
                "Std Dev", f"{s['stddev']:.2f} ms",
                "Pkt Loss", f"{s['loss']:.2f} %",
                "Availability", f"{s['availability']:.2f} %",
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

        # Latency chart
        chart_path = _build_latency_chart(rows, url, interval)
        if chart_path:
            story.append(Image(chart_path, width=250 * mm, height=60 * mm))
            story.append(Spacer(1, 5 * mm))

        # --------------------------
        #  Data table + color coding
        # --------------------------
        table_data = [
            [
                "Time (IST)",
                "Avg RTT (ms)",
                "Min RTT (ms)",
                "Max RTT (ms)",
                "Std Dev (ms)",
                "Packets Tx",
                "Packets Rx",
                "Loss (%)",
            ]
        ]

        for r in rows:
            table_data.append(
                [
                    r["time"].strftime("%Y-%m-%d %H:%M:%S"),
                    f"{r['avg_ms']:.2f}",
                    f"{r['min_ms']:.2f}",
                    f"{r['max_ms']:.2f}",
                    f"{r['stddev_ms']:.2f}",
                    r["tx"],
                    r["rx"],
                    f"{r['loss_pct']:.2f}",
                ]
            )

        tbl = Table(table_data, repeatRows=1)

        # Base style
        base_style = [
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

        # Dynamic cell background styles for latency + loss
        dynamic_style = []

        # Colors (matching Excel logic)
        light_orange = colors.HexColor("#FFF7D5")  # >250 ms
        amber = colors.HexColor("#FFBF00")         # >500 ms
        red = colors.HexColor("#FF4C4C")           # >1000 ms

        loss_yellow = colors.HexColor("#FFF8CC")   # ≥25%
        loss_amber = colors.HexColor("#FFD966")    # ≥50%
        loss_red = colors.HexColor("#FF4C4C")      # ≥75%

        # Data rows in the table start at row index 1 (0 is header)
        # cols: 0=Time, 1=Avg, 2=Min, 3=Max, 4=Std, 5=Tx, 6=Rx, 7=Loss
        for row_idx, r in enumerate(rows, start=1):
            # Latency values
            latency_vals = [
                (1, r["avg_ms"]),
                (2, r["min_ms"]),
                (3, r["max_ms"]),
                (4, r["stddev_ms"]),
            ]

            for col_idx, val in latency_vals:
                if val > 1000:
                    dynamic_style.append(
                        ("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), red)
                    )
                elif val > 500:
                    dynamic_style.append(
                        ("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), amber)
                    )
                elif val > 250:
                    dynamic_style.append(
                        ("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), light_orange)
                    )

            # Packet loss column (col 7)
            loss = r["loss_pct"]
            col_loss = 7
            if loss >= 75:
                dynamic_style.append(
                    ("BACKGROUND", (col_loss, row_idx), (col_loss, row_idx), loss_red)
                )
            elif loss >= 50:
                dynamic_style.append(
                    ("BACKGROUND", (col_loss, row_idx), (col_loss, row_idx), loss_amber)
                )
            elif loss >= 25:
                dynamic_style.append(
                    ("BACKGROUND", (col_loss, row_idx), (col_loss, row_idx), loss_yellow)
                )

        tbl.setStyle(TableStyle(base_style + dynamic_style))
        story.append(tbl)

    doc.build(story)
    return outfile

