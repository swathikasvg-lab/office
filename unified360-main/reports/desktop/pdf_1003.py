# reports/desktop/pdf_1003.py
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, Image, Flowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
import matplotlib.pyplot as plt
import tempfile
import os
from datetime import datetime, timezone
import math

# ---------------------------
# Helpers (parsing / formatting)
# ---------------------------
def _safe_get_series_value(series, field_name=None):
    """Return latest field value for named field in a series dict (Influx style).
    series: {'columns': [...], 'values': [[time, val], ...], 'tags': {...}}
    If field_name not found, fallback to last numeric column or last value.
    """
    if not series:
        return None
    cols = series.get("columns", [])
    vals = series.get("values", [])
    if not vals:
        return None

    # try explicit field name
    if field_name and field_name in cols:
        idx = cols.index(field_name)
        return vals[-1][idx] if idx < len(vals[-1]) else None

    # fallback: common names
    for try_name in ("value", "last", "used_percent", "download_mbps", "upload_mbps", "response_time_ms", "packet_loss_percent", "urlresponse"):
        if try_name in cols:
            idx = cols.index(try_name)
            return vals[-1][idx] if idx < len(vals[-1]) else None

    # last fallback: return second column if present
    if len(vals[-1]) >= 2:
        return vals[-1][1]
    return None

def _series_values_as_xy(series, field_name):
    """Return (x_list, y_list) for plotting timeline series.
    Accepts numeric epoch, RFC3339 string or plain strings for X.
    """
    xs = []
    ys = []
    if not series:
        return xs, ys

    cols = series.get("columns", [])
    vals = series.get("values", [])

    # time index usually 0
    try:
        t_idx = cols.index("time")
    except ValueError:
        t_idx = 0

    # y index
    if field_name in cols:
        y_idx = cols.index(field_name)
    else:
        y_idx = 1 if len(cols) > 1 else None

    for row in vals:
        if t_idx >= len(row):
            xt = None
        else:
            xt = row[t_idx]

        if y_idx is None or y_idx >= len(row):
            yt = None
        else:
            yt = row[y_idx]

        xt_conv = _try_parse_time(xt)
        xs.append(xt_conv)
        try:
            yt_num = float(yt) if yt is not None else None
            if math.isfinite(yt_num):
                ys.append(yt_num)
            else:
                ys.append(None)
        except Exception:
            ys.append(None)
    return xs, ys

def _try_parse_time(t):
    """Try to parse Influx time formats into datetime."""
    if t is None:
        return None
    if isinstance(t, datetime):
        return t
    if isinstance(t, (int, float)):
        # guess ms vs s vs ns
        if t > 1e12:
            try:
                return datetime.fromtimestamp(t/1e9, tz=timezone.utc)
            except:
                try:
                    return datetime.fromtimestamp(t/1e6, tz=timezone.utc)
                except:
                    return datetime.fromtimestamp(t/1000, tz=timezone.utc)
        elif t > 1e9:
            return datetime.fromtimestamp(t/1000, tz=timezone.utc)
        else:
            return datetime.fromtimestamp(t, tz=timezone.utc)
    if isinstance(t, str):
        try:
            s = t.strip()
            if s.endswith("Z"):
                s2 = s.replace("Z", "+00:00")
            else:
                s2 = s
            return datetime.fromisoformat(s2)
        except Exception:
            try:
                num = float(t)
                return _try_parse_time(num)
            except Exception:
                return t
    return t

def _format_value(v, precision=1):
    if v is None:
        return "-"
    try:
        if isinstance(v, bool):
            return str(v)
        f = float(v)
        if abs(f) >= 100 or abs(f) < 0.01:
            return f"{f:.{precision}f}"
        return f"{f:.{precision}f}"
    except Exception:
        return str(v)

# ---------------------------
# Color / Status helpers
# ---------------------------
def color_threshold(value, warn, crit):
    """Return color based on thresholds (higher is worse)."""
    try:
        v = float(value)
    except:
        return None
    if v >= crit:
        return colors.red
    if v >= warn:
        return colors.orange
    return None

def color_threshold_reverse(value, warn, crit):
    """Lower is worse (for speeds)."""
    try:
        v = float(value)
    except:
        return None
    if v <= crit:
        return colors.red
    if v <= warn:
        return colors.orange
    return None

def status_badge_text(value, warn, crit, invert=False):
    """Return badge text and color: (symbol, color). invert=True means lower is worse."""
    try:
        v = float(value)
    except:
        return ("-", None)
    if invert:
        if v <= crit:
            return ("✖", colors.red)
        if v <= warn:
            return ("⚠", colors.orange)
        return ("✓", colors.green)
    else:
        if v >= crit:
            return ("✖", colors.red)
        if v >= warn:
            return ("⚠", colors.orange)
        return ("✓", colors.green)

# small flowable for badge cell (text with bg)
class Badge(Flowable):
    def __init__(self, symbol, bgcolor, size=10):
        Flowable.__init__(self)
        self.symbol = symbol or ""
        self.bg = bgcolor
        self.size = size
        self.w = 18
        self.h = 12

    def draw(self):
        if self.bg:
            self.canv.setFillColor(self.bg)
            self.canv.roundRect(0, 0, self.w, self.h, 2, fill=1, stroke=0)
            self.canv.setFillColor(colors.white)
        else:
            self.canv.setFillColor(colors.black)
        self.canv.setFont("Helvetica-Bold", self.size)
        # center symbol roughly
        self.canv.drawCentredString(self.w/2, 1.5, str(self.symbol))

# ---------------------------
# Main PDF builder (enterprise style)
# ---------------------------
def build_pdf_1003(host, start, end, basic, net, updates, urlinfo_list, trend_series, mtr_data, customer):
    """
    host: string
    start, end: strings or datetimes (for header)
    basic: dict { os_info, cpu, mem, disk(list series) }
    net: dict { speed, gateway }
    updates: dict { pending, up_to_date }
    urlinfo_list: list of dicts (each has target/response/status)
    trend_series: series dict for one URL (optional)
    mtr_data: dict keyed by target: { packet_loss: [series], latency: [series] }
    customer: string
    """
    output_dir = "/usr/local/autointelli/opsduty-server/generated_reports"
    os.makedirs(output_dir, exist_ok=True)
    safe_host = str(host).replace("/", "_").replace(" ", "_")
    outfile = f"{output_dir}/Desktop_Performance_{safe_host}.pdf"
    try:
        if os.path.exists(outfile):
            os.remove(outfile)
    except:
        pass

    doc = SimpleDocTemplate(outfile, pagesize=landscape(A4),
                            rightMargin=20, leftMargin=20, topMargin=16, bottomMargin=16)

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    title_style = ParagraphStyle("title", parent=styles["Title"], fontSize=18, textColor=colors.HexColor("#0b61d6"), spaceAfter=6)
    header_style = ParagraphStyle("header", parent=styles["Heading2"], fontSize=11, textColor=colors.HexColor("#0b61d6"), spaceAfter=4)
    section_style = ParagraphStyle("section", parent=styles["Heading2"], fontSize=13, textColor=colors.HexColor("#0b61d6"), spaceAfter=6)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9)

    elements = []

    # top logo + title row (two columns)
    preferred_logos = [
        "/usr/local/autointelli/opsduty-server/static/logo.png",
        "/usr/local/autointelli/opsduty-server/static/img/autointelli.png"
    ]
    logo_used = None
    for p in preferred_logos:
        if os.path.exists(p):
            logo_used = p
            break

    # header area
    header_row = []
    if logo_used:
        try:
            header_row.append(Image(logo_used, width=140, height=40))
        except:
            header_row.append(Paragraph("<b>Autointelli</b>", title_style))
    else:
        header_row.append(Paragraph("<b>Autointelli</b>", title_style))

    meta_text = f"<b>Desktop Performance Report</b><br/><font size=10><b>Customer:</b> {customer} &nbsp;&nbsp; <b>Host:</b> {host}<br/><b>Period:</b> {start} — {end}</font>"
    header_row.append(Paragraph(meta_text, small))

    header_table = Table([header_row], colWidths=[200, doc.width-200])
    header_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE")]))
    elements.append(header_table)
    elements.append(Spacer(1, 8))

    # SECTION: BASIC SYSTEM INFO (card style)
    elements.append(Paragraph("Basic System Information", section_style))
    os_info = basic.get("os_info")
    cpu = basic.get("cpu")
    mem = basic.get("mem")
    disk_series = basic.get("disk", [])

    # OS name
    osname = "-"
    if os_info and os_info.get("values"):
        cols = os_info.get("columns", [])
        if "os_name_1" in cols:
            osname = os_info["values"][0][cols.index("os_name_1")]
        else:
            osname = os_info["values"][0][1] if len(os_info["values"][0]) > 1 else "-"

    cpu_val = _safe_get_series_value(cpu, None)
    # If CPU was returned as 100-usage_idle, it might already be numeric
    mem_val = _safe_get_series_value(mem, "used_percent")

    # Disk: gather each mount last value
    disk_parts = []
    for s in (disk_series or []):
        tags = s.get("tags", {})
        path = tags.get("path") or tags.get("mountpoint") or tags.get("device") or "unknown"
        vals = s.get("values", [])
        last_val = vals[-1][1] if vals else None
        if last_val is not None:
            disk_parts.append(f"{path}: {_format_value(last_val, precision=1)}%")
    disk_str = ", ".join(disk_parts) if disk_parts else "-"

    # Build a two-column table where left column is labels, right is values, with card-like background
    basic_table = Table([
        ["Hostname", host],
        ["Operating System", osname],
        ["CPU Usage (%)", _format_value(cpu_val)],
        ["Memory Usage (%)", _format_value(mem_val)],
        ["Disk Usage (%)", disk_str]
    ], colWidths=[220, doc.width - 240])

    basic_table_style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f5f9ff")),
        ("BACKGROUND", (0,1), (-1,-1), colors.white),
        ("BOX", (0,0), (-1,-1), 0.6, colors.HexColor("#d9e4ff")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#eef6ff")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
    ])
    basic_table.setStyle(basic_table_style)

    # Color thresholds & badges for CPU and MEM
    cpu_col = color_threshold(cpu_val, 70, 90)
    mem_col = color_threshold(mem_val, 70, 90)
    # apply text color if needed
    if cpu_col:
        basic_table.setStyle([("TEXTCOLOR", (1,2), (1,2), cpu_col)])
    if mem_col:
        basic_table.setStyle([("TEXTCOLOR", (1,3), (1,3), mem_col)])

    # Add small badge column (symbol) as a separate mini-table below the info to show status summary
    # Create status badges summary
    cpu_sym, cpu_sym_col = status_badge_text(cpu_val, 70, 90, invert=False)
    mem_sym, mem_sym_col = status_badge_text(mem_val, 70, 90, invert=False)

    # show table
    elements.append(basic_table)
    elements.append(Spacer(1, 8))

    # STATUS SUMMARY (compact badges)
    badge_rows = [
        ["Metric", "Status"],
        ["CPU", cpu_sym],
        ["Memory", mem_sym]
    ]
    badge_table = Table(badge_rows, colWidths=[120, 60])
    badge_style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eaf3ff")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN", (1,1), (1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOX", (0,0), (-1,-1), 0.4, colors.HexColor("#d9e4ff")),
    ])
    badge_table.setStyle(badge_style)
    # color badges
    if cpu_sym_col:
        badge_table.setStyle([("TEXTCOLOR", (1,1), (1,1), cpu_sym_col)])
    if mem_sym_col:
        badge_table.setStyle([("TEXTCOLOR", (1,2), (1,2), mem_sym_col)])
    elements.append(badge_table)
    elements.append(Spacer(1, 12))

    # -------------------------
    # NETWORK CARD
    # -------------------------
    elements.append(Paragraph("Network Information", section_style))
    speed = net.get("speed")
    gw = net.get("gateway")
    download = _safe_get_series_value(speed, "download_mbps") or _safe_get_series_value(speed, "download")
    upload = _safe_get_series_value(speed, "upload_mbps") or _safe_get_series_value(speed, "upload")
    loss = _safe_get_series_value(gw, "packet_loss_percent") or _safe_get_series_value(gw, "packet_loss")
    rtime = _safe_get_series_value(gw, "response_time_ms") or _safe_get_series_value(gw, "response_time")

    net_table = Table([
        ["Download Mbps", _format_value(download)],
        ["Upload Mbps", _format_value(upload)],
        ["Gateway Packet Loss (%)", _format_value(loss)],
        ["Gateway Response Time (ms)", _format_value(rtime)]
    ], colWidths=[260, doc.width - 280])

    net_style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f5f9ff")),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#d9e4ff")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#eef6ff")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
    ])
    net_table.setStyle(net_style)

    # color thresholds for network
    dl_col = color_threshold_reverse(download, 10, 5)
    ul_col = color_threshold_reverse(upload, 10, 5)
    loss_col = color_threshold(loss, 30, 70)
    rtime_col = color_threshold(rtime, 100, 250)

    if dl_col:
        net_table.setStyle([("TEXTCOLOR", (1,0), (1,0), dl_col)])
    if ul_col:
        net_table.setStyle([("TEXTCOLOR", (1,1), (1,1), ul_col)])
    if loss_col:
        net_table.setStyle([("TEXTCOLOR", (1,2), (1,2), loss_col)])
    if rtime_col:
        net_table.setStyle([("TEXTCOLOR", (1,3), (1,3), rtime_col)])

    elements.append(net_table)
    elements.append(Spacer(1, 12))

    # -------------------------
    # SYSTEM UPDATE CARD
    # -------------------------
    elements.append(Paragraph("System Update Status", section_style))
    pending = _safe_get_series_value(updates.get("pending"), "last") or _safe_get_series_value(updates.get("pending"), "pending_updates")
    upto = _safe_get_series_value(updates.get("up_to_date"), "last") or _safe_get_series_value(updates.get("up_to_date"), "is_up_to_date")

    upd_table = Table([
        ["Is Up-to-Date", _format_value(upto)],
        ["Pending Updates", _format_value(pending)]
    ], colWidths=[260, doc.width - 280])

    upd_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f5f9ff")),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#d9e4ff")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#eef6ff")),
    ]))
    elements.append(upd_table)
    elements.append(Spacer(1, 12))

    # -------------------------
    # CRITICAL URLS (table with badges)
    # -------------------------
    elements.append(Paragraph("Critical URL Reachability", section_style))
    url_rows = []
    for u in (urlinfo_list or []):
        target = u.get("target") or u.get("tags", {}).get("target") or "-"
        response = _safe_get_series_value(u, "urlresponse") or u.get("response") or u.get("Response Time") or u.get("value")
        status = u.get("status") or u.get("Status Code") or "-"
        url_rows.append([target, _format_value(response, precision=0), status])

    if not url_rows:
        url_rows = [["-", "-", "-"]]

    url_table_rows = [["URL", "Avg Response (ms)", "Status", "State"]]
    for r in url_rows:
        badge_sym, badge_col = status_badge_text(r[1], 100, 500, invert=False)
        url_table_rows.append([r[0], r[1], r[2], badge_sym])

    url_table = Table(url_table_rows, colWidths=[340, 110, 80, 60])
    url_table_style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#eaf3ff")),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#d9e4ff")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#eef6ff")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ])
    url_table.setStyle(url_table_style)
    # color response and badge column
    for i, row in enumerate(url_table_rows[1:], start=1):
        resp = row[1]
        col = color_threshold(resp, 100, 500)
        if col:
            url_table.setStyle([("TEXTCOLOR", (1,i), (1,i), col)])
        # badge color
        _, badge_col = status_badge_text(row[1], 100, 500, invert=False)
        if badge_col:
            url_table.setStyle([("TEXTCOLOR", (3,i), (3,i), badge_col)])
    elements.append(url_table)
    elements.append(Spacer(1, 12))

    # -------------------------
    # TREND CHART (if provided)
    # -------------------------
    if trend_series:
        try:
            elements.append(Paragraph("Response Time Trend (sample)", section_style))
            xs, ys = _series_values_as_xy(trend_series, "urlresponse")
            # convert xs into readable labels if datetime
            labels = []
            plot_x = []
            for x in xs:
                if isinstance(x, datetime):
                    labels.append(x.strftime("%H:%M"))
                    plot_x.append(x)
                else:
                    labels.append(str(x))
                    plot_x.append(x)
            if ys:
                fig, ax = plt.subplots(figsize=(7, 2.2))
                ax.plot(range(len(ys)), ys, marker='o', linewidth=1)
                ax.set_ylabel("ms")
                ax.set_xlabel("Time")
                ax.grid(axis='y', linestyle='--', alpha=0.4)
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=30, fontsize=7)
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                fig.tight_layout()
                fig.savefig(tmp.name, dpi=140, bbox_inches="tight")
                plt.close(fig)
                elements.append(Image(tmp.name, width=720, height=180))
                elements.append(Spacer(1, 12))
        except Exception:
            pass

    # -------------------------
    # TRACE ROUTE (Packet Loss & Latency) per target
    # -------------------------
    elements.append(Paragraph("Trace Route Information", section_style))

    # helper to format and color table given rows and thresholds
    def _build_and_color_table(rows, col_widths, header_bg=colors.HexColor("#eaf3ff"), color_col_idx=None, warn=None, crit=None, invert=False):
        # rows includes header at index 0
        tbl = Table(rows, colWidths=col_widths)
        style = [
            ("BACKGROUND", (0,0), (-1,0), header_bg),
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#d9e4ff")),
            ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#eef6ff")),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ]
        tbl.setStyle(TableStyle(style))
        # apply coloring per data row if requested
        if color_col_idx is not None and warn is not None and crit is not None:
            for i, r in enumerate(rows[1:], start=1):
                val = r[color_col_idx]
                # try numeric
                try:
                    fv = float(val)
                except:
                    # non numeric - skip
                    fv = None
                if fv is not None:
                    if invert:
                        # lower is worse
                        if fv <= crit:
                            tbl.setStyle([("TEXTCOLOR", (color_col_idx,i), (color_col_idx,i), colors.red)])
                        elif fv <= warn:
                            tbl.setStyle([("TEXTCOLOR", (color_col_idx,i), (color_col_idx,i), colors.orange)])
                    else:
                        if fv >= crit:
                            tbl.setStyle([("TEXTCOLOR", (color_col_idx,i), (color_col_idx,i), colors.red)])
                        elif fv >= warn:
                            tbl.setStyle([("TEXTCOLOR", (color_col_idx,i), (color_col_idx,i), colors.orange)])
        return tbl

    # iterate targets
    for target, item in (mtr_data or {}).items():
        elements.append(Paragraph(f"Target: {target}", header_style))

        # Packet Loss: pl_series_list is list of series (tags include hop, ip), each series contains mean() in values
        pl_series_list = item.get("packet_loss", []) or []
        pl_data = []
        for s in pl_series_list:
            tags = s.get("tags", {})
            hop = tags.get("hop", "-")
            ip = tags.get("ip", "-")
            vals = s.get("values", [])
            mean_val = vals[-1][1] if vals else None
            try:
                hop_num = int(str(hop).strip())
            except:
                hop_num = 99999
            pl_data.append([hop_num, ip, mean_val])

        if not pl_data:
            pl_rows = [["Hop", "IP", "Packet Loss (%)"], ["-", "-", "-"]]
        else:
            pl_data.sort(key=lambda x: x[0])
            pl_rows = [["Hop", "IP", "Packet Loss (%)"]] + [[str(r[0]), r[1], _format_value(r[2], precision=1)] for r in pl_data]

        pl_table = _build_and_color_table(pl_rows, col_widths=[60, 320, 160], color_col_idx=2, warn=30, crit=70, invert=False)
        elements.append(pl_table)
        elements.append(Spacer(1, 8))

        # Latency: similar approach
        lat_series_list = item.get("latency", []) or []
        lat_data = []
        for s in lat_series_list:
            tags = s.get("tags", {})
            hop = tags.get("hop", "-")
            ip = tags.get("ip", "-")
            vals = s.get("values", [])
            last_val = vals[-1][1] if vals else None
            try:
                hop_num = int(str(hop).strip())
            except:
                hop_num = 99999
            lat_data.append([hop_num, ip, last_val])

        if not lat_data:
            lat_rows = [["Hop", "IP", "Latency (ms)"], ["-", "-", "-"]]
        else:
            lat_data.sort(key=lambda x: x[0])
            lat_rows = [["Hop", "IP", "Latency (ms)"]] + [[str(r[0]), r[1], _format_value(r[2], precision=1)] for r in lat_data]

        lat_table = _build_and_color_table(lat_rows, col_widths=[60, 320, 160], color_col_idx=2, warn=100, crit=250, invert=False)
        elements.append(lat_table)
        elements.append(Spacer(1, 12))

    # footer with generation time
    elements.append(Paragraph(f"Report generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%SZ')}", small))

    # Build PDF
    doc.build(elements)
    return outfile

