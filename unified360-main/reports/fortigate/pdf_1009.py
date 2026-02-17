# reports/fortigate/pdf_1009.py
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image, Flowable
)
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from datetime import datetime
import os

styles = getSampleStyleSheet()

# Enterprise colours (matching the VPN sample)
PRIMARY_BG = colors.HexColor("#003A63")  # deep blue
HEADER_BG = colors.HexColor("#004e92")
ACCENT = colors.HexColor("#00d4ff")

title_style = ParagraphStyle(
    "Title",
    parent=styles["Heading1"],
    fontSize=16,
    alignment=1,
    spaceAfter=6,
    textColor=colors.white,
)

meta_style = ParagraphStyle(
    "Meta",
    parent=styles["BodyText"],
    fontSize=9,
    spaceAfter=6,
)

section_style = ParagraphStyle(
    "Section",
    parent=styles["Heading2"],
    fontSize=12,
    spaceAfter=4,
)

cell_style = ParagraphStyle(
    "Cell",
    parent=styles["BodyText"],
    fontSize=8,
    spaceAfter=2,
)

logo_path = os.path.join("static", "img", "autointelli.png")  # keep this relative path to app static


# ---------------------------
# Helpers
# ---------------------------

def human_bw(v):
    """
    Convert raw bandwidth number (dynamic based on value)
    - if value looks large, show Gbps / Mbps / Kbps
    v is expected to be numeric (bits or bytes depending on source)
    We'll treat it as BYTES per second if > 10^3 etc — dynamic conversion:
    * if >= 1_000_000_000 -> G
    * if >= 1_000_000 -> M
    * if >= 1_000 -> K
    Show units succinctly (e.g. 123.45 Gbps or 123.45 GB)
    NOTE: We don't assume bits/bytes — just format the numeric value dynamically.
    """
    try:
        v = float(v)
    except Exception:
        return str(v or "")

    # Use thresholds and show appropriate unit labels (GB/MB/KB)
    # We'll assume the raw value represents bytes/seconds or bytes total — user requested dynamic formatting
    # Show in bytes-based units (B / KB / MB / GB)
    if abs(v) >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f} GB"
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.2f} MB"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.2f} KB"
    return f"{v:.0f} B"


def short_time(ts):
    """Return human friendly time if present, or blank"""
    if not ts:
        return ""
    try:
        # if it's a datetime-like string, try to normalize
        if isinstance(ts, str):
            return ts.replace("T", " ")
        return str(ts)
    except Exception:
        return str(ts)


# ---------------------------
# Colour rules
# ---------------------------

def latency_color(val):
    try:
        v = float(val)
    except Exception:
        return None
    if v > 1000:
        return colors.HexColor("#c62828")  # red
    if v > 500:
        return colors.HexColor("#ff8f00")  # amber
    if v > 250:
        return colors.HexColor("#ffb74d")  # light orange
    return None


def loss_color(val):
    try:
        v = float(val)
    except Exception:
        return None
    if v >= 75:
        return colors.HexColor("#9b1d08")
    if v >= 50:
        return colors.HexColor("#d97b00")
    if v >= 25:
        return colors.HexColor("#ff8f00")
    if v >= 5:
        return colors.HexColor("#ffb74d")
    return None


def state_color(state):
    return colors.green if state == "UP" else colors.red


# ---------------------------
# Small Top-N table builder
# ---------------------------

def build_small_table(title, rows, col_widths=None):
    if not col_widths:
        col_widths = [70*mm, 25*mm, 25*mm, 30*mm, 20*mm]

    data = [["Link Name", "Latency(ms)", "Jitter(ms)", "Packet Loss(%)", "State"]]
    for r in rows:
        data.append([
            r.get("link_name", ""),
            f"{r.get('latency_ms', 0):.2f}",
            f"{r.get('jitter_ms', 0):.2f}",
            f"{r.get('packet_loss', 0):.2f}",
            r.get("status", "UP"),
        ])

    table = Table(data, colWidths=col_widths)
    ts = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (1, 1), (-2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d6d6d6")),
        # alternate row backgrounds
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])

    # apply dynamic colors
    for i in range(1, len(data)):
        lat_col = latency_color(float(data[i][1]))
        if lat_col:
            ts.add("TEXTCOLOR", (1, i), (1, i), lat_col)
        loss_col = loss_color(float(data[i][3]))
        if loss_col:
            ts.add("TEXTCOLOR", (3, i), (3, i), loss_col)
        ts.add("TEXTCOLOR", (4, i), (4, i), state_color(data[i][4]))

    table.setStyle(ts)
    return [Paragraph(title, section_style), table, Spacer(1, 6)]


# ---------------------------
# Detailed table builder
# ---------------------------

def build_detailed_table(rows):
    # headers
    headers = [
        "Link Name", "IfName", "Latency(ms)", "Jitter(ms)",
        "Packet Loss(%)", "State", "BW In", "BW Out", "Used In", "Used Out"
    ]
    data = [headers]

    for r in rows:
        data.append([
            r.get("link_name", ""),
            r.get("ifname", ""),
            f"{r.get('latency_ms', 0):.2f}",
            f"{r.get('jitter_ms', 0):.2f}",
            f"{r.get('packet_loss', 0):.2f}",
            r.get("status", "UP"),
            human_bw(r.get("bandwidth_in", 0)),
            human_bw(r.get("bandwidth_out", 0)),
            human_bw(r.get("used_bandwidth_in", 0)),
            human_bw(r.get("used_bandwidth_out", 0)),
        ])

    # use compact but readable widths, landscape A4
    col_widths = [
        55*mm, 28*mm, 18*mm, 18*mm, 22*mm,
        18*mm, 26*mm, 26*mm, 26*mm, 26*mm
    ]

    table = Table(data, colWidths=col_widths, repeatRows=1)

    ts = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d6d6d6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfbfb")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])

    # apply color rules on numeric columns (latency col index 2, packet loss index 4, state index 5)
    for i in range(1, len(data)):
        # latency
        try:
            lat_val = float(data[i][2])
            c = latency_color(lat_val)
            if c:
                ts.add("TEXTCOLOR", (2, i), (2, i), c)
        except Exception:
            pass

        # packet loss (data stored as string like "12.34")
        try:
            loss_val = float(data[i][4])
            c = loss_color(loss_val)
            if c:
                ts.add("TEXTCOLOR", (4, i), (4, i), c)
        except Exception:
            pass

        # state
        ts.add("TEXTCOLOR", (5, i), (5, i), state_color(data[i][5]))

    table.setStyle(ts)

    return [Paragraph("Detailed SD-WAN Link Table", section_style), Spacer(1, 4), table]


# ---------------------------
# Build header with logo and meta line
# ---------------------------

def build_header(meta):
    elems = []
    # Try to include logo (if exists)
    if os.path.exists(logo_path):
        try:
            img = Image(logo_path, width=46, height=22)
            # place logo and title side by side using a small table
            title = Paragraph(f"<b>Fortigate SD-WAN Performance Report</b>", title_style)
            meta_text = Paragraph(
                f"Device: <b>{meta.get('device','')}</b> | Range: {meta.get('start','')} → {meta.get('end','')} | Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
                meta_style
            )
            header_table = Table([
                [img, title],
                ["", meta_text]
            ], colWidths=[48*mm, None])
            header_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]))
            elems.append(header_table)
            elems.append(Spacer(1, 6))
            return elems
        except Exception:
            pass

    # If no logo, simple title + meta
    elems.append(Paragraph(f"<b>Fortigate SD-WAN Performance Report</b>", title_style))
    elems.append(Spacer(1, 6))
    elems.append(Paragraph(
        f"Device: <b>{meta.get('device','')}</b> | Range: {meta.get('start','')} → {meta.get('end','')} | Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        meta_style
    ))
    elems.append(Spacer(1, 6))
    return elems


# ---------------------------
# Build PDF (main)
# ---------------------------

def build_pdf(outfile, meta, top_latency, top_jitter, top_packet_loss, up_links, down_links, all_rows):
    """
    meta: dict with device/start/end/total_links/up_links/down_links
    top_*: lists (already chosen)
    up_links/down_links/all_rows: lists of dicts normalized by rpt_1009
    """

    story = []

    # header (logo + title + meta)
    story += build_header(meta)

    # summary row (big)
    summary = Paragraph(
        f"Total Links: <b>{meta.get('total_links', 0)}</b> | "
        f"UP: <b>{meta.get('up_links', 0)}</b> | "
        f"DOWN: <b>{meta.get('down_links', 0)}</b>",
        styles["BodyText"]
    )
    story.append(summary)
    story.append(Spacer(1, 8))

    # Top N (Latency / Jitter / Packet Loss)
    # NOTE: top_packet_loss should be prepared to include ALL links (see rpt_1009 change)
    if top_latency:
        story += build_small_table("Top 10 — Highest Latency", top_latency)
    if top_jitter:
        story += build_small_table("Top 10 — Highest Jitter", top_jitter)
    #if top_packet_loss:
        #story += build_small_table("Top 10 — Highest Packet Loss", top_packet_loss)

    story.append(PageBreak())

    # Down links summary (if any)
    if down_links:
        # show short down links summary
        down_data = [["Link Name", "IfName", "Packet Loss(%)", "State"]]
        for r in down_links:
            down_data.append([
                r.get("link_name", ""),
                r.get("ifname", ""),
                f"{r.get('packet_loss', 0):.2f}",
                r.get("status", "DOWN")
            ])
        down_table = Table(down_data, colWidths=[70*mm, 35*mm, 30*mm, 20*mm])
        down_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d6d6d6")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fffafa")]),
        ]))
        story.append(Paragraph("Down Links", section_style))
        story.append(down_table)
        story.append(PageBreak())

    # Detailed table (UP + DOWN)
    story += build_detailed_table(all_rows)

    # write PDF
    doc = SimpleDocTemplate(
        outfile,
        pagesize=landscape(A4),
        leftMargin=12,
        rightMargin=12,
        topMargin=12,
        bottomMargin=12,
    )
    doc.build(story)

