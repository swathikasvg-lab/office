from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak, Flowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import matplotlib.pyplot as plt
import tempfile
import os
from datetime import datetime

# register a fallback font if available (optional)
# pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))

def _make_bar_chart(data, title, xlabel, ylabel, top_n=10):
    """
    Creates a single bar chart PNG file and returns path.
    data: list of tuples (label, value)
    """
    if not data:
        return None
    # sort descending and pick top_n
    data_sorted = sorted(data, key=lambda x: x[1], reverse=True)[:top_n]
    labels = [x[0] for x in data_sorted]
    values = [x[1] for x in data_sorted]

    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.barh(range(len(values))[::-1], values)  # horizontal bar chart (one plot)
    ax.set_yticks(range(len(values))[::-1])
    ax.set_yticklabels(labels)
    ax.set_xlabel(ylabel)
    ax.set_title(title)
    plt.tight_layout()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    fig.savefig(tmp.name, dpi=160)
    plt.close(fig)
    return tmp.name

class PageNumCanvas(Flowable):
    def __init__(self, width):
        Flowable.__init__(self)
        self.width = width
    def draw(self):
        # page number will be added in footer via onLaterPages in SimpleDocTemplate build
        pass

def build_pdf(results, start_ts, end_ts, cust, report_name="Server Performance"):
    """
    results: list of dicts:
      {
        "instance": str,
        "customer": str,
        "cpu": float,
        "mem": float,
        "disk": "mnt=xx%,... (string)",
        "disk_read": float,
        "disk_write": float,
        "net_in": float,
        "net_out": float
      }
    start_ts, end_ts: datetime objects
    """
    output_dir = "/usr/local/autointelli/opsduty-server/generated_reports"
    os.makedirs(output_dir, exist_ok=True)
    pdf_path = f"{output_dir}/Server_Performance_Report.pdf"
    try:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
    except:
        pass

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        leftMargin=28,
        rightMargin=28,
        topMargin=24,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Title"], fontSize=18, leading=22, textColor=colors.HexColor("#1A73E8"))
    header_style = ParagraphStyle("h", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#1A73E8"))
    normal = styles["Normal"]

    elements = []

    # --- Header: logo + title row ---
    logo_path = "/usr/local/autointelli/opsduty-server/static/img/autointelli.png"
    header_table_data = []
    left = []
    if os.path.exists(logo_path):
        try:
            left.append(Image(logo_path, width=140, height=40))
        except:
            pass
    left.append(Paragraph(f"<b>{report_name}</b>", title_style))
    left.append(Spacer(1, 6))
    left.append(Paragraph(f"<b>Period:</b> {start_ts.strftime('%Y-%m-%d %H:%M')} &nbsp;&nbsp;&nbsp; <b>To:</b> {end_ts.strftime('%Y-%m-%d %H:%M')}", normal))
    right = [Paragraph(f"<b>Customer:</b> {cust if results else 'All Customers'}", normal)]
    header_table_data.append([left, right])
    header_table = Table(header_table_data, colWidths=[doc.width * 0.75, doc.width * 0.25])
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(header_table)
    elements.append(Spacer(1, 12))

    # --- Executive summary ---
    total_servers = len(results)
    avg_cpu = round(sum([r.get("cpu", 0) for r in results]) / total_servers, 2) if total_servers else 0
    avg_mem = round(sum([r.get("mem", 0) for r in results]) / total_servers, 2) if total_servers else 0
    top_cpu = sorted(results, key=lambda x: x.get("cpu", 0), reverse=True)[:5]
    top_mem = sorted(results, key=lambda x: x.get("mem", 0), reverse=True)[:5]

    exec_lines = [
        [Paragraph("<b>Executive Summary</b>", header_style), ""],
        [Paragraph(f"Total Servers: <b>{total_servers}</b>", normal),
         Paragraph(f"Avg CPU: <b>{avg_cpu:.2f}%</b><br/>Avg Memory: <b>{avg_mem:.2f}%</b>", normal)]
    ]
    exec_table = Table(exec_lines, colWidths=[doc.width * 0.6, doc.width * 0.4])
    exec_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(exec_table)
    elements.append(Spacer(1, 8))

    # --- Small lists of top servers (compact) ---
    def make_top_list(title, arr, field):
        lines = [Paragraph(f"<b>{title}</b>", styles["Heading4"])]
        for r in arr:
            lines.append(Paragraph(f"{r['instance']} — {r[field]:.2f}%", normal))
        return lines

    cols = [
        make_top_list("Top CPU", top_cpu, "cpu"),
        make_top_list("Top Memory", top_mem, "mem")
    ]
    # pack into a table for layout
    col_table = Table([[cols[0], cols[1]]], colWidths=[doc.width * 0.5, doc.width * 0.5])
    col_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(col_table)
    elements.append(Spacer(1, 12))

    # --- Charts: Top CPU and Top Disk Read ---
    cpu_chart_data = [(r["instance"], r["cpu"]) for r in sorted(results, key=lambda x: x.get("cpu", 0), reverse=True)]
    diskr_chart_data = [(r["instance"], r.get("disk_read", 0)) for r in sorted(results, key=lambda x: x.get("disk_read", 0), reverse=True)]

    cpu_png = _make_bar_chart(cpu_chart_data, "Top CPU Consumers", "Instance", "CPU (%)", top_n=8)
    diskr_png = _make_bar_chart(diskr_chart_data, "Top Disk Read (KB/s)", "Instance", "KB/s", top_n=8)

    if cpu_png:
        elements.append(Image(cpu_png, width=doc.width * 0.48, height=2.2 * inch))
    if diskr_png:
        elements.append(Image(diskr_png, width=doc.width * 0.48, height=2.2 * inch))
    elements.append(Spacer(1, 10))

    # --- Detailed table header & data ---
    table_data = [
        ["Instance", "CPU %", "Memory %", "Disk Summary", "Read KB/s", "Write KB/s", "Net Rx KB/s", "Net Tx KB/s"]
    ]

    # build table rows with tidy disk multiline
    for r in results:
        disk_summary = r.get("disk") or ""
        # convert comma separated into multi-line
        if isinstance(disk_summary, str) and "," in disk_summary:
            parts = [p.strip() for p in disk_summary.split(",")]
            disk_display = "\n".join(parts)
        else:
            disk_display = str(disk_summary)

        row = [
            r.get("instance"),
            f"{r.get('cpu', 0):.2f}",
            f"{r.get('mem', 0):.2f}",
            disk_display,
            f"{r.get('disk_read', 0):.2f}",
            f"{r.get('disk_write', 0):.2f}",
            f"{r.get('net_in', 0):.2f}",
            f"{r.get('net_out', 0):.2f}"
        ]
        table_data.append(row)

    col_widths = [
        doc.width * 0.18,  # instance
        doc.width * 0.08,  # cpu
        doc.width * 0.08,  # mem
        doc.width * 0.36,  # disk summary (wraps)
        doc.width * 0.08,
        doc.width * 0.08,
        doc.width * 0.07,
        doc.width * 0.07
    ]

    detail_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    # style
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A73E8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("ALIGN", (1, 1), (2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E9FF")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9E9FF")),
    ])

    # alternating rows
    for i in range(1, len(table_data)):
        bg = colors.whitesmoke if i % 2 == 0 else colors.lightgrey
        style.add("BACKGROUND", (0, i), (-1, i), bg)

    detail_table.setStyle(style)
    elements.append(detail_table)
    elements.append(Spacer(1, 12))

    # Footer (small)
    gen = Paragraph(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — Powered by Autointelli Systems", normal)
    elements.append(Spacer(1, 6))
    elements.append(gen)

    # build doc
    doc.build(elements)

    # cleanup chart files
    for p in (cpu_png, diskr_png):
        if p and os.path.exists(p):
            os.remove(p)

    return pdf_path

