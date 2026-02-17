from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def _parse_html_ts(ts_str: str) -> datetime:
    """
    Convert HTML datetime-local (IST from browser) to UTC datetime.
    """
    if not ts_str:
        raise ValueError("Missing timestamp")

    fmt = "%Y-%m-%dT%H:%M" if len(ts_str) == 16 else "%Y-%m-%dT%H:%M:%S"

    local_dt = datetime.strptime(ts_str, fmt)
    ist_dt   = local_dt.replace(tzinfo=IST)

    return ist_dt.astimezone(timezone.utc)


def _choose_interval(start_dt: datetime, end_dt: datetime) -> str:
    """
    Decide InfluxDB GROUP BY interval based on time-range length.
      <= 24h          -> 1m
      >24h & <=72h    -> 1h
      >72h & <=15d    -> 12h
      >15d            -> 1d
    """
    delta_hours = (end_dt - start_dt).total_seconds() / 3600.0

    if delta_hours <= 24:
        return "1m"
    elif delta_hours <= 72:
        return "1h"
    elif delta_hours <= 24 * 15:
        return "12h"
    else:
        return "1d"


class BandwidthUtilizationReport:
    """
    Report 1005 - Bandwidth Utilization (SNMP)
    """

    def run(self, template_type, device, interfaces, start, end, fmt="pdf"):
        if not device:
            raise ValueError("Device is required for Bandwidth report")
        if not interfaces:
            raise ValueError("At least one interface must be selected")

        # Dedup and preserve order
        iface_list = [i for i in interfaces if i]
        iface_list = list(dict.fromkeys(iface_list))

        start_dt = _parse_html_ts(start)
        end_dt = _parse_html_ts(end)

        interval = _choose_interval(start_dt, end_dt)

        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        if fmt == "excel":
            from .excel_1005 import build_excel

            return build_excel(
                template_type=template_type,
                device=device,
                interfaces=iface_list,
                start=start_iso,
                end=end_iso,
                interval=interval,
            )

        from .pdf_1005 import build_pdf

        return build_pdf(
            template_type=template_type,
            device=device,
            interfaces=iface_list,
            start=start_iso,
            end=end_iso,
            interval=interval,
        )

