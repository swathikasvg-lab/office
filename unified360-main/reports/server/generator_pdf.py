"""
FINAL generator_pdf.py
-----------------------
✔ Perfect round Pie Chart
✔ Pie Chart centered (no table wrapping)
✔ Summary table below pie chart
✔ Customer name shown below period
✔ File name always: Server_Availability_Report.pdf
✔ Blue theme tables, alternating rows
✔ Logo Position 1, Size B
"""

from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle,
    Paragraph, Spacer, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import matplotlib.pyplot as plt
from reportlab.lib.units import inch
import tempfile
import os
import re
from datetime import datetime


# ============================================================
# Humanize minutes
# ============================================================
def humanize_minutes(m):
    try:
        m = int(m)
    except:
        return "0 mins"
    days = m // 1440
    m %= 1440
    hours = m // 60
    minutes = m % 60
    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hr{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} min{'s' if minutes != 1 else ''}")
    return " ".join(parts) if parts else "0 mins"


# ============================================================
# Parse "1 day 2 hrs 10 mins"
# ============================================================
def parse_human_downtime_to_minutes(s):
    if not s or not isinstance(s, str):
        return 0
    s = s.lower()
    total = 0

    m = re.search(r"(\d+)\s*day", s)
    if m: total += int(m.group(1)) * 1440
    m = re.search(r"(\d+)\s*hr", s)
    if m: total += int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*min", s)
    if m: total += int(m.group(1))
    return total


# ============================================================
# Build Pie Chart PNG (square → circle)
def build_pie_chart_file(availability_pct):
    up = float(availability_pct)
    down = max(0.0, 100.0 - up)

    labels = ["Available", "Downtime"]
    sizes = [up, down]
    colors_list = ["#28A745", "#DC3545"]  # Green / Red

    # Square figure ONLY
    fig, ax = plt.subplots(figsize=(4, 4))

    ax.pie(
        sizes,
        labels=labels,
        autopct="%1.1f%%",
        startangle=90,
        colors=colors_list,
        textprops={"fontsize": 12}
    )

    ax.axis("equal")  # ensures circle

    # ⭐ IMPORTANT ⭐
    # Remove automatic cropping that makes it non-square
    plt.tight_layout(pad=1.5)

    # Save exactly as 4x4 inches, NO auto-crop
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    fig.savefig(tmp.name, dpi=180)   # <-- NO bbox_inches="tight"

    plt.close(fig)
    return tmp.name

# ============================================================
# Logo Location
# ============================================================
PREFERRED_LOGOS = [
    "/usr/local/autointelli/opsduty-server/static/img/autointelli.png",
    "/usr/local/autointelli/opsduty-server/static/autointelli_logo.png",
]

def select_logo():
    for p in PREFERRED_LOGOS:
        if os.path.exists(p):
            return p
    return None


# ============================================================
# Main PDF Builder
# ============================================================
def build_pdf(results, start_ts, end_ts, report_name="Server Availability Report"):

    output_dir = "/usr/local/autointelli/opsduty-server/generated_reports"
    os.makedirs(output_dir, exist_ok=True)

    pdf_path = f"{output_dir}/Server_Availability_Report.pdf"

    # Remove old file if exists
    try:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
    except:
        pass

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=28,
        rightMargin=28,
        topMargin=28,
        bottomMargin=28
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]

    title_style = ParagraphStyle(
        "title",
        parent=styles["Title"],
        fontSize=16,
        textColor=colors.HexColor("#1A73E8")
    )

    header_style = ParagraphStyle(
        "header",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=colors.HexColor("#1A73E8")
    )

    elements = []

    # ----------------------------------------------------------
    # Logo
    # ----------------------------------------------------------
    logo_path = select_logo()
    if logo_path:
        try:
            logo = Image(logo_path, width=130, height=45)
            elements.append(logo)
        except:
            pass

    elements.append(Spacer(1, 10))

    # ----------------------------------------------------------
    # Title
    # ----------------------------------------------------------
    elements.append(Paragraph(f"<b>{report_name}</b>", title_style))

    def fmt(dt):
        if isinstance(dt, str):
            try:
                return datetime.fromisoformat(dt).strftime("%Y-%m-%d %H:%M")
            except:
                return dt
        if isinstance(dt, datetime):
            return dt.strftime("%Y-%m-%d %H:%M")
        return str(dt)

    # ----------------------------------------------------------
    # Period
    # ----------------------------------------------------------
    elements.append(Paragraph(
        f"<b>Period:</b> {fmt(start_ts)}  &nbsp;&nbsp; <b>To:</b> {fmt(end_ts)}",
        normal
    ))

    # ----------------------------------------------------------
    # Customer Name
    # ----------------------------------------------------------
    customer_name = results[0].get("customer", "All Customers") if results else "All Customers"
    elements.append(Paragraph(f"<b>Customer:</b> {customer_name}", normal))
    elements.append(Spacer(1, 16))

    # ----------------------------------------------------------
    # Summary Calculations
    # ----------------------------------------------------------
    total_servers = len(results)

    avg_avail = 0.0
    if total_servers:
        avg_avail = sum([float(r.get("availability") or 0) for r in results]) / total_servers

    # ----------------------------------------------------------
    # PERFECT ROUND PIE CHART (Center, Direct Insert)
    # ----------------------------------------------------------
    pie_file = build_pie_chart_file(avg_avail)
    pie_img = Image(pie_file)
    pie_img.hAlign = "CENTER"

    # perfect circle (square)
    pie_img.drawWidth = 180
    pie_img.drawHeight = 180

    elements.append(pie_img)
    elements.append(Spacer(1, 20))

    # ----------------------------------------------------------
    # Summary Table BELOW Pie Chart
    # ----------------------------------------------------------
    summary_data = [
        ["Metric", "Value"],
        ["Total Servers", str(total_servers)],
        ["Average Availability (%)", f"{avg_avail:.2f}%"],
    ]

    summary_table = Table(summary_data, colWidths=[doc.width * 0.4, doc.width * 0.6])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A73E8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#1A73E8")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1A73E8")),
    ]))

    elements.append(summary_table)
    elements.append(Spacer(1, 20))

    # ----------------------------------------------------------
    # Detailed Table
    # ----------------------------------------------------------
    elements.append(Paragraph("<b>Detailed Server Availability</b>", header_style))
    elements.append(Spacer(1, 8))

    table_data = [["Instance", "Customer", "Availability (%)", "Total Downtime"]]

    for r in results:
        table_data.append([
            r.get("instance"),
            r.get("customer"),
            f"{float(r.get('availability') or 0):.2f}%",
            r.get("downtime")
        ])

    col_widths = [
        doc.width * 0.33,
        doc.width * 0.22,
        doc.width * 0.20,
        doc.width * 0.25
    ]

    detail = Table(table_data, colWidths=col_widths, repeatRows=1)

    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A73E8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("ALIGN", (2, 1), (2, -1), "CENTER"),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#1A73E8")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1A73E8")),
    ])

    # Alternating rows
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            style.add("BACKGROUND", (0, i), (-1, i), colors.whitesmoke)
        else:
            style.add("BACKGROUND", (0, i), (-1, i), colors.lightgrey)

    detail.setStyle(style)
    elements.append(detail)
    elements.append(Spacer(1, 20))

    # ----------------------------------------------------------
    # Footer
    # ----------------------------------------------------------
    footer = Paragraph(
        "<para align='center' fontSize='9'>Powered by Autointelli Systems | www.autointelli.com</para>",
        normal
    )
    elements.append(footer)

    # ----------------------------------------------------------
    # Build PDF
    # ----------------------------------------------------------
    doc.build(elements)

    return pdf_path

