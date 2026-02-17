# reports/port/rpt_1007.py
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
    ist_dt = local_dt.replace(tzinfo=IST)
    return ist_dt.astimezone(timezone.utc)


def _choose_interval(start_dt: datetime, end_dt: datetime) -> str:
    """
    Decide InfluxDB GROUP BY interval based on time range length.
      <= 24h          -> 1m
      >24h & <=72h    -> 5m
      >72h & <=15d    -> 30m
      >15d            -> 1h
    """
    delta_hours = (end_dt - start_dt).total_seconds() / 3600.0

    if delta_hours <= 24:
        return "1m"
    elif delta_hours <= 72:
        return "5m"
    elif delta_hours <= 24 * 15:
        return "30m"
    else:
        return "1h"


class PortPerformanceReport:
    """
    Report 1007 - Port Performance (net_response)
    """

    def _parse_targets(self, raw_targets):
        """
        raw_targets is a list of strings from the form, like:
            "mail.autointelli.com|587"
            "136.185.13.31|9222"

        Returns list of dicts:
            [{"server": "...", "port": 587}, ...]
        """
        parsed = []
        seen = set()

        for t in raw_targets:
            if not t:
                continue
            if "|" not in t:
                # fallback – treat entire string as server, port unknown
                server = t.strip()
                key = (server, None)
                if key in seen:
                    continue
                seen.add(key)
                parsed.append({"server": server, "port": None})
                continue

            server, port_str = t.split("|", 1)
            server = server.strip()
            port_str = port_str.strip()
            try:
                port = int(port_str)
            except ValueError:
                port = None

            key = (server, port)
            if key in seen:
                continue
            seen.add(key)
            parsed.append({"server": server, "port": port})

        return parsed

    def run(self, targets, start, end, fmt="pdf"):
        """
        targets: list of strings "server|port" from the HTML multi-select.
        """
        if not targets:
            raise ValueError("At least one server/port must be selected")

        parsed_targets = self._parse_targets(targets)
        if not parsed_targets:
            raise ValueError("No valid server/port combinations provided")

        start_dt = _parse_html_ts(start)
        end_dt = _parse_html_ts(end)
        interval = _choose_interval(start_dt, end_dt)

        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        if fmt == "excel":
            from .excel_1007 import build_excel

            return build_excel(
                targets=parsed_targets,
                start=start_iso,
                end=end_iso,
                interval=interval,
            )

        from .pdf_1007 import build_pdf

        return build_pdf(
            targets=parsed_targets,
            start=start_iso,
            end=end_iso,
            interval=interval,
        )

