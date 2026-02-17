# reports/url/url_data.py

from datetime import datetime, timezone
import requests
from flask import current_app
from zoneinfo import ZoneInfo


class UrlDataFetcher:
    """
    Helper to query InfluxDB for URL performance, status codes,
    SSL certificate info and response-time trend.

    - Handles friendly_name as TAG
    - Handles result as TAG
    - Success count computed using COUNT(response_time)
    - Includes response-time series with dynamic grouping
    - Includes caching for repeated URL requests within same run
    """

    def __init__(self):
        self.influx_url = current_app.config.get("INFLUXDB_URL", "http://localhost:8086/query")
        self.influx_db = current_app.config.get("INFLUXDB_DB", "autointelli")

        # in-memory caches
        self._summary_cache = {}
        self._code_cache = {}
        self._ssl_cache = {}
        self._trend_cache = {}

        # Your timezone (VERY IMPORTANT FIX)
        self.local_tz = ZoneInfo("Asia/Kolkata")

    # -----------------------------------------------------
    # Utility Functions
    # -----------------------------------------------------

    def _to_rfc3339(self, ts_str: str) -> str:
        """
        Convert datetime-local input (local IST) → UTC RFC3339.
        """
        dt = datetime.fromisoformat(ts_str)
        dt_local = dt.replace(tzinfo=self.local_tz)
        dt_utc = dt_local.astimezone(timezone.utc)
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _query(self, query: str):
        """
        Run InfluxDB query and return results[0].
        """
        print("INFLUX QUERY → ", query)  # DEBUG
        resp = requests.get(self.influx_url, params={"db": self.influx_db, "q": query}, timeout=30)
        resp.raise_for_status()

        json = resp.json()
        if not json.get("results"):
            return None
        return json["results"][0]

    def _first_value(self, res: dict, column: str):
        if not res or "series" not in res:
            return None
        s = res["series"][0]
        if column not in s["columns"]:
            return None
        idx = s["columns"].index(column)
        return s["values"][0][idx] if s["values"] else None

    def _select_bucket(self, start: str, end: str) -> str:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        diff_days = (e - s).total_seconds() / 86400

        if diff_days <= 1:
            return "1h"
        elif diff_days <= 3:
            return "6h"
        return "1d"

    # -----------------------------------------------------
    # Friendly Name (TAG)
    # -----------------------------------------------------

    def get_friendly_name(self, server: str, start: str, end: str):
        start_utc = self._to_rfc3339(start)
        end_utc = self._to_rfc3339(end)

        q = f"""
        SELECT LAST("response_time")
        FROM http_response
        WHERE time >= '{start_utc}' AND time <= '{end_utc}'
          AND "server" = '{server}'
        GROUP BY "server","friendly_name"
        """

        res = self._query(q)
        if not res or "series" not in res:
            return None

        tags = res["series"][0].get("tags", {})
        return tags.get("friendly_name")

    # -----------------------------------------------------
    # Summary
    # -----------------------------------------------------

    def get_summary(self, server: str, start: str, end: str):
        key = (server, start, end)
        if key in self._summary_cache:
            return self._summary_cache[key]

        start_utc = self._to_rfc3339(start)
        end_utc = self._to_rfc3339(end)

        # Total checks
        q_total = f"""
        SELECT COUNT("response_time") AS total
        FROM http_response
        WHERE time >= '{start_utc}' AND time <= '{end_utc}'
          AND "server" = '{server}'
        """
        total_res = self._query(q_total)
        total = self._first_value(total_res, "total") or 0

        # SUCCESS checks (result is TAG)
        q_success = f"""
        SELECT COUNT("response_time") AS success
        FROM http_response
        WHERE time >= '{start_utc}' AND time <= '{end_utc}'
          AND "server" = '{server}'
          AND "result" = 'success'
        """
        success_res = self._query(q_success)
        success = self._first_value(success_res, "success") or 0

        failed = max(int(total) - int(success), 0)

        availability = 0.0
        if total:
            availability = round(success / float(total) * 100, 2)

        # Response time stats
        q_rt = f"""
        SELECT
            MEAN("response_time") AS avg_rt,
            MIN("response_time") AS min_rt,
            MAX("response_time") AS max_rt
        FROM http_response
        WHERE time >= '{start_utc}' AND time <= '{end_utc}'
          AND "server" = '{server}'
        """

        rt_res = self._query(q_rt)
        avg_rt = self._first_value(rt_res, "avg_rt") or 0.0
        min_rt = self._first_value(rt_res, "min_rt") or 0.0
        max_rt = self._first_value(rt_res, "max_rt") or 0.0

        friendly = self.get_friendly_name(server, start, end) or ""

        summary = {
            "server": server,
            "friendly_name": friendly,
            "total_checks": int(total),
            "success_checks": int(success),
            "failed_checks": int(failed),
            "availability_pct": availability,
            "avg_response_time": round(float(avg_rt), 3),
            "min_response_time": round(float(min_rt), 3),
            "max_response_time": round(float(max_rt), 3),
        }

        self._summary_cache[key] = summary
        return summary

    # -----------------------------------------------------
    # Status Codes
    # -----------------------------------------------------

    def get_status_codes(self, server: str, start: str, end: str):
        key = (server, start, end)
        if key in self._code_cache:
            return self._code_cache[key]

        start_utc = self._to_rfc3339(start)
        end_utc = self._to_rfc3339(end)

        q = f"""
        SELECT COUNT("response_time") AS cnt
        FROM http_response
        WHERE time >= '{start_utc}' AND time <= '{end_utc}'
          AND "server" = '{server}'
        GROUP BY "status_code"
        """

        res = self._query(q)
        if not res or "series" not in res:
            self._code_cache[key] = []
            return []

        output = []
        for s in res["series"]:
            tags = s.get("tags", {})
            status = tags.get("status_code", "unknown")
            idx = s["columns"].index("cnt")
            cnt = s["values"][0][idx] if s["values"] else 0
            output.append({"status_code": status, "count": int(cnt)})

        output.sort(key=lambda x: x["status_code"])
        self._code_cache[key] = output
        return output

    # -----------------------------------------------------
    # SSL Info
    # -----------------------------------------------------
    def get_ssl_info(self, friendly_name: str):
        if not friendly_name:
            return None

        if friendly_name in self._ssl_cache:
            return self._ssl_cache[friendly_name]

        ssl_name = f"{friendly_name} (SSL)"

        # The ONLY reliable way for InfluxDB 1.8 SSL lookups
        q = f"""
        SELECT *
        FROM x509_cert
        WHERE "friendly_name" = '{ssl_name}'
        ORDER BY time DESC
        LIMIT 1
        """

        res = self._query(q)
        if not res or "series" not in res:
            self._ssl_cache[friendly_name] = None
            return None

        s = res["series"][0]
        cols = s["columns"]
        vals = s["values"][0]

        def get(col):
            if col in cols:
                v = vals[cols.index(col)]
                return v if v not in ("", None, "null") else None
            return None

        end_ts = get("enddate")
        expiry_str = None
        days_left = None

        if end_ts:
            dt = datetime.fromtimestamp(float(end_ts), tz=timezone.utc)
            expiry_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            days_left = (dt - datetime.now(timezone.utc)).days

        info = {
            "common_name": get("common_name"),
            "issuer": get("issuer_common_name"),   # IMPORTANT: exact field name
            "verification": get("verification"),
            "expiry_date": expiry_str,
            "days_left": days_left,
        }

        self._ssl_cache[friendly_name] = info
        return info



    # -----------------------------------------------------
    # Response Time Trend
    # -----------------------------------------------------

    def get_response_time_series(self, server: str, start: str, end: str):
        key = (server, start, end)
        if key in self._trend_cache:
            return self._trend_cache[key]

        start_utc = self._to_rfc3339(start)
        end_utc = self._to_rfc3339(end)
        bucket = self._select_bucket(start, end)

        q = f"""
        SELECT MEAN("response_time") AS rt
        FROM http_response
        WHERE time >= '{start_utc}' AND time <= '{end_utc}'
          AND "server" = '{server}'
        GROUP BY time({bucket}) fill(none)
        """

        res = self._query(q)
        out = []

        if res and "series" in res:
            s = res["series"][0]
            t_idx = s["columns"].index("time")
            v_idx = s["columns"].index("rt")

            for row in s["values"]:
                ts = row[t_idx]
                val = row[v_idx]
                if val is not None:
                    out.append((ts, float(val)))

        self._trend_cache[key] = out
        return out

