# reports/desktop/rpt_1003.py
import requests
from datetime import datetime
import os
from .pdf_1003 import build_pdf_1003
from .excel_1003 import build_excel_1003
import time

INFLUX_URL = os.environ.get("DESKTOP_INFLUXDB_URL") or os.environ.get("INFLUXDB_URL") or "http://127.0.0.1:8086/query"
INFLUX_DB = os.environ.get("DESKTOP_INFLUXDB_DB") or "end_user_monitoring"

class DesktopPerformanceReport:
    def __init__(self, influx_url=None, influx_db=None):
        self.influx_url = influx_url or INFLUX_URL
        self.influx_db = influx_db or INFLUX_DB
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _q(self, q):
        resp = self.session.get(self.influx_url, params={"db": self.influx_db, "q": q}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def to_rfc3339(ts):
        try:
            # "2025-12-01T10:50" → "2025-12-01T10:50:00Z"
            dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M")
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except:
            return ts  # fallback

    def _series_list(self, q):
        j = self._q(q)
        results = j.get("results", [])
        if not results:
            return []
        res0 = results[0]
        return res0.get("series", []) or []

    def _single_series(self, q):
        lst = self._series_list(q)
        return lst[0] if lst else None

    # -------------------------
    # Data fetchers
    # -------------------------
    def get_os_info(self, host):
        q = f"select * from os_info where host=~/{host}/ order by time desc limit 1"
        return self._single_series(q)

    def get_cpu(self, host):
        q = f"SELECT 100 - usage_idle FROM cpu WHERE host=~/{host}/ AND cpu='cpu-total' ORDER BY time DESC LIMIT 1"
        return self._single_series(q)

    def get_mem(self, host):
        q = f"SELECT used_percent FROM mem WHERE host=~/{host}/ ORDER BY time DESC LIMIT 1"
        return self._single_series(q)

    def get_disk(self, host, start, end):
        # last(used_percent) grouped by path within the time window
        q = (
            f"SELECT last(used_percent) FROM disk "
            f"WHERE host=~/{host}/ AND time >= '{start}' AND time <= '{end}' GROUP BY path"
        )
        return self._series_list(q)

    def get_speed(self, host, start, end):
        q = (
            f"SELECT download_mbps, upload_mbps FROM speed_test "
            f"WHERE hostname=~/{host}/ AND time >= '{start}' AND time <= '{end}' ORDER BY time DESC LIMIT 1"
        )
        return self._single_series(q)

    def get_gateway(self, host, start, end):
        q = (
            f"SELECT packet_loss_percent, response_time_ms FROM isp_uptime "
            f"WHERE hostname=~/{host}/ AND time >= '{start}' AND time <= '{end}' ORDER BY time DESC LIMIT 1"
        )
        return self._single_series(q)

    def get_updates(self, host):
        pending_q = f"select last(pending_updates) from system_update_status where host=~/{host}/"
        upto_q = f"select last(is_up_to_date) from system_update_status where host=~/{host}/"
        return {
            "pending": self._single_series(pending_q),
            "up_to_date": self._single_series(upto_q)
        }

    def get_urlresponses(self, host, start, end):
        # We'll get latest per-target rows by grouping by target and taking last row per series
        q = (
            f"SELECT urlresponse AS response, status AS status FROM ai_urlresponse "
            f"WHERE host=~/{host}/ AND time >= '{start}' AND time <= '{end}' GROUP BY target"
        )
        return self._series_list(q)


    def get_url_trend(self, host, target, start, end):
        """
        Fetch URL response trend with dynamic time grouping based on duration.
        """
    
        # Convert to datetime if strings
        if isinstance(start, str):
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        else:
            start_dt = start
    
        if isinstance(end, str):
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        else:
            end_dt = end
    
        # Calculate duration in days
        diff_days = (end_dt - start_dt).total_seconds() / 86400
    
        # Determine grouping interval
        if diff_days <= 1:
            interval = "1h"
        elif diff_days <= 3:
            interval = "6h"
        else:
            interval = "1d"
    
        # Build InfluxQL Query
        q = (
            f"SELECT mean(urlresponse) FROM ai_urlresponse "
            f"WHERE host=~/{host}/ AND target=~/{target}/ "
            f"AND time >= '{start}' AND time <= '{end}' "
            f"GROUP BY time({interval}) FILL(0)"
        )
    
        print("TREND QUERY:", q)
        return self._series_list(q)


    def get_mtr_packetloss(self, host, target, start, end, hop_regex="(1|2|3|4|5|6|7|8|9|10|11|12|13|14|15)"):
        q = (
            f"SELECT loss FROM ai_mtr "
            f"WHERE host=~/{host}/ AND target=~/{target}/ AND time >= '{start}' AND time <= '{end}' "
            f"AND hop=~/{hop_regex}/ AND ip != 'unknown' GROUP BY hop, ip order by time desc limit 1"
        )
        print(q)
        return self._series_list(q)

    def get_mtr_latency(self, host, target, start, end):
        q = (
            f"SELECT last(avg) FROM ai_mtr "
            f"WHERE host=~/{host}/ AND target=~/{target}/ AND time >= '{start}' AND time <= '{end}' "
            f"GROUP BY hop, ip, target"
        )
        return self._series_list(q)

    # -------------------------
    # Runner
    # -------------------------
    def run(self, host, start, end, customer, fmt="pdf"):
        """
        host: host string (exact or regex part)
        start, end: ISO timestamps (YYYY-MM-DDTHH:MM[:SS])
        customer: customer name string - included in header
        fmt: "pdf" or "excel"
        """
        # normalize times to RFC3339-ish if necessary
        # Influx accepts "YYYY-MM-DDTHH:MM:SSZ" — user already provides ISO in UI; keep as-is
        start_ts = self.to_rfc3339(start)
        end_ts   = self.to_rfc3339(end)


        # Collect
        os_info = self.get_os_info(host)
        cpu = self.get_cpu(host)
        mem = self.get_mem(host)
        disk = self.get_disk(host, start_ts, end_ts)
        speed = self.get_speed(host, start_ts, end_ts)
        gateway = self.get_gateway(host, start_ts, end_ts)
        updates = self.get_updates(host)
        url_series = self.get_urlresponses(host, start_ts, end_ts)

        # Build urlinfo: list of series (target tag -> latest value row)
        urlinfo_list = []
        for s in url_series:
            tags = s.get("tags", {})
            target = tags.get("target", "")
            print(target)
            # take last value row
            vals = s.get("values", [])
            if not vals:
                continue
            # Influx series columns include time and fields; find field indices
            cols = s.get("columns", [])
            # find indices safely:
            response_idx = None
            status_idx = None
            try:
                response_idx = cols.index("response")
            except ValueError:
                try: response_idx = cols.index("urlresponse")
                except: response_idx = 1
            try:
                status_idx = cols.index("status")
            except ValueError:
                status_idx = None

            lastrow = vals[-1]
            response_val = lastrow[response_idx] if response_idx is not None and response_idx < len(lastrow) else None
            status_val = lastrow[status_idx] if status_idx is not None and status_idx < len(lastrow) else None

            urlinfo_list.append({
                "target": target,
                "response": response_val,
                "status": status_val
            })

        # choose one target for trend plotting (prefer autointelli if present else first)
        trend_target = None
        for u in urlinfo_list:
            if "autointelli" in u["target"]:
                trend_target = u["target"]
                break
        if not trend_target and urlinfo_list:
            trend_target = urlinfo_list[0]["target"]

        trend_series = []
        if trend_target:
            trend_series = self.get_url_trend(host, trend_target, start_ts, end_ts)

        # MTR per target
        mtr_data = {}
        for u in urlinfo_list:
            t = u["target"].replace("https___","")
            print(t)
            pl = self.get_mtr_packetloss(host, t, start_ts, end_ts)
            lat = self.get_mtr_latency(host, t, start_ts, end_ts)
            mtr_data[t] = {"packet_loss": pl, "latency": lat}

        # package results as dicts that PDF/Excel expect (we will reuse structure from previous generator)
        basic = {"os_info": os_info, "cpu": cpu, "mem": mem, "disk": disk}
        net = {"speed": speed, "gateway": gateway}
        # For trend and urlinfo, pass raw series (trend_series may be list)
        # we will pass 'trend' as the first series for trend_target if present
        trend_item = trend_series[0] if trend_series else None

        # Now call generators
        if fmt == "excel":
            return build_excel_1003(host, start_ts, end_ts, basic, net, updates, urlinfo_list, trend_item, mtr_data, customer)
        # default pdf
        return build_pdf_1003(host, start_ts, end_ts, basic, net, updates, urlinfo_list, trend_item, mtr_data, customer)

