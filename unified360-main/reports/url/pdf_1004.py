# reports/url/pdf_1004.py

from datetime import datetime
from io import BytesIO
import os

from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    Image,
    KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm

import matplotlib.pyplot as plt
from matplotlib.dates import date2num, DateFormatter

from .url_data import UrlDataFetcher


class PdfUrlReport:
    """
    Enterprise-Grade URL Performance PDF Report
    Autointelli-branded with:
      - Logo Header (Layout A)
      - Watermark
      - Page Footer
      - Corporate Blue Theme
      - Trend Charts
      - SSL Summary
      - Status Code Summary
      - Overflow Protection
    """

    def __init__(self):
        self.data_fetcher = UrlDataFetcher()

        # Styles
        base = getSampleStyleSheet()
        self.styles = base

        self.brand_blue = colors.HexColor("#0050A0")
        self.brand_dark = colors.HexColor("#00284F")
        self.brand_light = colors.HexColor("#E7F0FA")
        self.text_grey = colors.HexColor("#333333")

        self.styles.add(
            ParagraphStyle(
                name="ReportTitle",
                fontName="Helvetica-Bold",
                fontSize=20,
                leading=24,
                textColor=self.brand_dark,
                spaceAfter=8,
            )
        )

        self.styles.add(
            ParagraphStyle(
                name="SectionHeading",
                fontName="Helvetica-Bold",
                fontSize=14,
                textColor=self.brand_blue,
                spaceBefore=12,
                spaceAfter=6,
            )
        )

        self.styles.add(
            ParagraphStyle(
                name="SubHeading",
                fontName="Helvetica-Bold",
                fontSize=12,
                textColor=self.brand_dark,
                spaceBefore=10,
                spaceAfter=4,
            )
        )

        self.styles.add(
            ParagraphStyle(
                name="Meta",
                fontName="Helvetica",
                fontSize=9,
                textColor=self.text_grey,
                leading=11,
            )
        )

        self.styles.add(
            ParagraphStyle(
                name="Body",
                fontName="Helvetica",
                fontSize=10,
                leading=12,
                textColor=self.text_grey,
            )
        )

        # Primary logo path
        self.logo_path = os.path.abspath(
            "/usr/local/autointelli/opsduty-server/static/img/autointelli.png"
        )

    # ---------------------------------------------------------------------
    # HEADER + FOOTER + WATERMARK
    # ---------------------------------------------------------------------

    def _draw_header_footer(self, canvas, doc, generated_str):
        canvas.saveState()

        width, height = A4

        # Watermark
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 52)
        canvas.setFillColorRGB(0.92, 0.92, 0.92)
        canvas.translate(width / 2, height / 2)
        canvas.rotate(45)
        canvas.drawCentredString(0, 0, "AUTOINTELLI")
        canvas.restoreState()

        # Header light strip
        canvas.setFillColor(self.brand_light)
        canvas.rect(0, height - 55, width, 35, stroke=0, fill=1)

        # Auto-detect logo path (handles Docker & local)
        search_paths = [
            self.logo_path,
            "/app/static/img/autointelli.png",
            "static/img/autointelli.png",
        ]
        final_logo = None
        for p in search_paths:
            if os.path.isfile(p):
                final_logo = p
                break

        # Draw logo if found
        if final_logo:
            try:
                canvas.drawImage(
                    final_logo,
                    15 * mm,
                    height - 48,
                    width=45,     # explicit width (fixes blank image issue)
                    height=18,    # maintain aspect ratio
                    preserveAspectRatio=True,
                )
            except Exception:
                pass

        # Title right side
        canvas.setFillColor(self.brand_dark)
        canvas.setFont("Helvetica-Bold", 15)
        canvas.drawRightString(width - 15 * mm, height - 34, "URL Performance Report")

        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(self.text_grey)
        canvas.drawRightString(
            width - 15 * mm,
            height - 47,
            f"Generated: {generated_str}",
        )

        # Footer line
        canvas.setStrokeColor(self.brand_light)
        canvas.setLineWidth(0.4)
        canvas.line(15 * mm, 28 * mm, width - 15 * mm, 28 * mm)

        # Footer info
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(self.text_grey)
        canvas.drawString(15 * mm, 22 * mm, "Autointelli Confidential")

        page_txt = f"Page {doc.page}"
        canvas.drawRightString(width - 15 * mm, 22 * mm, page_txt)

        canvas.restoreState()

    # ---------------------------------------------------------------------
    # TABLE HELPERS
    # ---------------------------------------------------------------------

    def _summary_table(self, summary):
        data = [
            ["Metric", "Value"],
            ["Server (URL)", summary["server"]],
            ["Friendly Name", summary["friendly_name"] or "-"],
            ["Total Checks", summary["total_checks"]],
            ["Success Checks", summary["success_checks"]],
            ["Failed Checks", summary["failed_checks"]],
            ["Availability", f'{summary["availability_pct"]:.2f}%'],
            ["Avg Response Time (s)", summary["avg_response_time"]],
            ["Min Response Time (s)", summary["min_response_time"]],
            ["Max Response Time (s)", summary["max_response_time"]],
        ]

        t = Table(data, colWidths=[150, 330])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), self.brand_blue),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("ALIGN", (0, 0), (-1, 0), "LEFT"),

                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("TEXTCOLOR", (0, 1), (-1, -1), self.text_grey),
                    ("FONTSIZE", (0, 1), (-1, -1), 9),

                    ("ALIGN", (0, 1), (0, -1), "LEFT"),
                    ("GRID", (0, 0), (-1, -1), 0.35, self.brand_light),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ]
            )
        )
        return t

    def _status_table(self, server, start, end):
        codes = self.data_fetcher.get_status_codes(server, start, end)

        if not codes:
            return Paragraph(
                "No HTTP status code data available for this period.",
                self.styles["Body"],
            )

        rows = [["Status Code", "Count"]]
        for c in codes:
            rows.append([c["status_code"], c["count"]])

        t = Table(rows, colWidths=[150, 150])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), self.brand_blue),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),

                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("TEXTCOLOR", (0, 1), (-1, -1), self.text_grey),

                    ("ALIGN", (0, 1), (-1, -1), "CENTER"),
                    ("GRID", (0, 0), (-1, -1), 0.35, self.brand_light),
                ]
            )
        )
        return t

    # ---------------------------------------------------------------------
    # SSL BLOCK
    # ---------------------------------------------------------------------

    def _ssl_block(self, fname):
        ssl = self.data_fetcher.get_ssl_info(fname)
        if not ssl:
            return Paragraph(
                "<b>SSL Monitoring:</b> Not configured / No matching SSL record.",
                self.styles["Body"],
            )

        html = f"""
        <b>SSL Monitoring:</b><br/>
        Common Name: {ssl.get('common_name', '-') }<br/>
        Issuer: {ssl.get('issuer', '-') }<br/>
        Expiry Date: {ssl.get('expiry_date', '-') }<br/>
        Days Left: {ssl.get('days_left', '-') }<br/>
        Verification: {ssl.get('verification', '-') }<br/>
        """
        return Paragraph(html, self.styles["Body"])

    # ---------------------------------------------------------------------
    # CHART
    # ---------------------------------------------------------------------

    def _trend_chart(self, server, start, end):
        series = self.data_fetcher.get_response_time_series(server, start, end)
        if not series:
            return Paragraph(
                "No response time data available.",
                self.styles["Body"],
            )

        times, vals = [], []
        for ts, v in series:
            try:
                if ts.endswith("Z"):
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(ts)
                times.append(dt)
                vals.append(v)
            except Exception:
                continue

        fig, ax = plt.subplots(figsize=(6.0, 2.2))
        ax.plot_date(
            date2num(times),
            vals,
            linestyle="-",
            linewidth=1.5,
        )
        ax.set_ylabel("Response Time (s)")
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.xaxis.set_major_formatter(DateFormatter("%m-%d\n%H:%M"))
        fig.autofmt_xdate()
        fig.tight_layout()

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)

        return Image(buf, width=450, height=130)

    # ---------------------------------------------------------------------
    # MAIN GENERATOR
    # ---------------------------------------------------------------------

    def generate(self, urls, start, end):
        if isinstance(urls, str):
            urls = [urls]

        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        outfile = f"/tmp/url_performance_{ts}.pdf"

        generated_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        doc = SimpleDocTemplate(
            outfile,
            pagesize=A4,
            topMargin=95,
            bottomMargin=70,
            leftMargin=32,
            rightMargin=32,
        )

        story = []

        # Title block
        story.append(Paragraph("URL Performance Report", self.styles["ReportTitle"]))
        story.append(
            Paragraph(
                f"<b>Report Period:</b> {start} to {end}",
                self.styles["Body"],
            )
        )
        story.append(
            Paragraph(f"<b>Generated On:</b> {generated_str}", self.styles["Body"])
        )
        story.append(Spacer(1, 20))

        first = True
        for server in urls:
            summary = self.data_fetcher.get_summary(server, start, end)

            if not first:
                story.append(PageBreak())
            first = False

            # Section header
            story.append(
                Paragraph(
                    f"<b>Server (URL):</b> {summary['server']}",
                    self.styles["SectionHeading"],
                )
            )
            story.append(
                Paragraph(
                    f"<b>Friendly Name:</b> {summary['friendly_name'] or '-'}",
                    self.styles["Body"],
                )
            )
            story.append(Spacer(1, 8))

            if summary["total_checks"] == 0:
                story.append(
                    Paragraph(
                        "No measurement data found for this URL.",
                        self.styles["Body"],
                    )
                )
                continue

            # Availability Summary
            story.append(
                Paragraph("Availability & Performance Summary", self.styles["SubHeading"])
            )
            story.append(KeepTogether([self._summary_table(summary)]))
            story.append(Spacer(1, 12))

            # Status Codes
            story.append(Paragraph("HTTP Status Codes", self.styles["SubHeading"]))
            story.append(KeepTogether([self._status_table(server, start, end)]))
            story.append(Spacer(1, 12))

            # Trend Chart
            story.append(Paragraph("Response Time Trend", self.styles["SubHeading"]))
            story.append(KeepTogether([self._trend_chart(server, start, end)]))
            story.append(Spacer(1, 12))

            # SSL
            story.append(Paragraph("SSL / Certificate Health", self.styles["SubHeading"]))
            story.append(KeepTogether([self._ssl_block(summary["friendly_name"])]))
            story.append(Spacer(1, 12))

        # Build PDF
        def _cb(canvas, doc_obj):
            self._draw_header_footer(canvas, doc_obj, generated_str)

        doc.build(story, onFirstPage=_cb, onLaterPages=_cb)

        return outfile

