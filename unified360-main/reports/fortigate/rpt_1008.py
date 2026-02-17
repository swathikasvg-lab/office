# reports/fortigate/rpt_1008.py
from datetime import datetime, timezone, timedelta
import os

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

def _parse_html_ts(ts_str: str) -> datetime:
    """
    Convert HTML datetime-local string (no timezone) -> assume IST -> return UTC-aware datetime
    """
    if not ts_str:
        raise ValueError("Missing timestamp")
    fmt = "%Y-%m-%dT%H:%M" if len(ts_str) == 16 else "%Y-%m-%dT%H:%M:%S"
    local_dt = datetime.strptime(ts_str, fmt)
    ist_dt = local_dt.replace(tzinfo=IST)
    return ist_dt.astimezone(UTC)

def _choose_interval(start_dt: datetime, end_dt: datetime) -> str:
    hours = (end_dt - start_dt).total_seconds() / 3600.0
    if hours <= 24:
        return "1m"
    if hours <= 72:
        return "5m"
    if hours <= 24*15:
        return "30m"
    return "1h"

class FortigateVpnReport:
    """
    Orchestrator for 1008 report.
    device: hostname string (Fortigate)
    start/end: HTML datetime-local strings (assumed IST)
    fmt: 'pdf' or 'excel'
    """
    def run(self, device: str, start: str, end: str, fmt: str = "pdf"):
        if not device:
            raise ValueError("device required")
        start_dt = _parse_html_ts(start)
        end_dt = _parse_html_ts(end)
        interval = _choose_interval(start_dt, end_dt)

        start_iso = start_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        if fmt == "excel":
            from .excel_1008 import build_excel
            return build_excel(device=device, start=start_iso, end=end_iso, interval=interval)
        else:
            from .pdf_1008 import build_pdf
            return build_pdf(device=device, start=start_iso, end=end_iso, interval=interval)

