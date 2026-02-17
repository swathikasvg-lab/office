"""
SNMP Interface handler (UP/DOWN only, scalable)
- Stores state PER INTERFACE in alert_rule_state (like PortHandler).
- Baseline behavior: first time an interface is seen -> create row and do NOT alert.
- Triggers when ifOperStatus == 2 (DOWN) for evaluation_count consecutive cycles.
- Recovers when ifOperStatus == 1 (UP).
- Pages Influx results using SLIMIT/SOFFSET to handle very large interface counts.
"""

from __future__ import annotations

import time
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional
from flask import current_app

from .base import BaseMonitoringHandler
from alert_engine.trigger.notifier import send_notification
from models.alert_rule_state import AlertRuleState
from extensions import db

DOWN_VALUE = 2
UP_VALUE = 1

DEFAULT_INFLUX_URL = None
DEFAULT_INFLUX_DB = None


class SNMPInterfaceHandler(BaseMonitoringHandler):
    monitoring_type = "SNMP_Interface"

    # Tune these for your environment
    SERIES_PAGE_SIZE = 1000     # how many grouped series per Influx page
    DB_COMMIT_EVERY = 500       # commit every N interfaces processed
    INFLUX_TIMEOUT = 12         # seconds

    def __init__(self):
        pass

    # -------------------------
    # Influx fetch (paged)
    # -------------------------
    def _influx_query(self, q: str) -> Optional[dict]:
        global DEFAULT_INFLUX_URL, DEFAULT_INFLUX_DB

        if DEFAULT_INFLUX_URL is None:
            DEFAULT_INFLUX_URL = current_app.config.get("INFLUXDB_URL")
        if DEFAULT_INFLUX_DB is None:
            DEFAULT_INFLUX_DB = current_app.config.get("INFLUXDB_DB")

        influx = getattr(self.rule, "influx_url", None) or DEFAULT_INFLUX_URL
        dbname = getattr(self.rule, "influx_db", None) or DEFAULT_INFLUX_DB

        if not influx or not dbname:
            print(f"[SNMPInterfaceHandler:{self.rule_id}] Missing INFLUXDB_URL/INFLUXDB_DB", flush=True)
            return None

        try:
            r = requests.get(influx, params={"db": dbname, "q": q}, timeout=self.INFLUX_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[SNMPInterfaceHandler:{self.rule_id}] Influx error: {e}", flush=True)
            return None

    def fetch_interfaces_paged(self, soffset: int) -> Dict[str, Dict[str, Any]]:
        """
        Returns dict keyed by "<hostname>::<ifDescr>" for one page of series.
        Uses SLIMIT/SOFFSET so we page by series (not rows).
        """
        q = f"""
        SELECT LAST(ifOperStatus) AS ifOperStatus, LAST(ifDescr) AS ifDescr, LAST(hostname) AS hostname
        FROM interface
        WHERE customer_name!=''
        GROUP BY hostname, ifDescr
        SLIMIT {self.SERIES_PAGE_SIZE} SOFFSET {soffset}
        """

        js = self._influx_query(q)
        if not js or "results" not in js or not js["results"]:
            return {}

        res0 = js["results"][0]
        series = res0.get("series", [])
        if not series:
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        for s in series:
            cols = s.get("columns", [])
            vals = s.get("values", [])
            if not vals:
                continue
            row = vals[-1]
            d = dict(zip(cols, row))

            hostname = (s.get("tags", {}) or {}).get("hostname") or d.get("hostname") or "unknown"
            ifDescr = d.get("ifDescr") or ((s.get("tags", {}) or {}).get("ifDescr")) or "unknown"

            try:
                status_raw = d.get("ifOperStatus", None)
                status = int(status_raw) if status_raw is not None else UP_VALUE
            except Exception:
                status = UP_VALUE

            key = f"{hostname}::{ifDescr}"
            out[key] = {
                "hostname": hostname,
                "ifDescr": ifDescr,
                "ifOperStatus": status,
            }

        return out

    # -------------------------
    # Logic: per-interface state
    # -------------------------
    def _get_or_create_state(self, key_simple: str) -> Tuple[AlertRuleState, bool]:
        """
        Returns (state_row, created_bool).
        target_value stores the interface key: "<hostname>::<ifDescr>"
        """
        st = AlertRuleState.query.filter_by(
            rule_id=self.rule.id,
            customer_id=self.rule.customer_id,
            target_value=key_simple
        ).first()

        if st:
            return st, False

        st = AlertRuleState(
            rule_id=self.rule.id,
            customer_id=self.rule.customer_id,
            target_value=key_simple,
            is_active=False,
            consecutive=0,
            extended_state={"status": None}  # keep last known status small
        )
        db.session.add(st)
        db.session.flush()
        return st, True

    def execute(self, rule, state=None):
        self.rule = rule
        self.rule_id = rule.id
        self.threshold = int(rule.evaluation_count or 3)

        # optional filters (you were reusing bw_hostname/bw_interface)
        self.filter_hostname = (getattr(rule, "bw_hostname", None) or "").strip()
        self.filter_interface = (getattr(rule, "bw_interface", None) or "").strip()

        t_start = time.time()
        processed = 0
        created_baseline = 0
        soffset = 0
        page_num = 0

        print(f"[SNMPInterfaceHandler:{self.rule_id}] start threshold={self.threshold} page_size={self.SERIES_PAGE_SIZE}", flush=True)

        while True:
            page_num += 1
            page = self.fetch_interfaces_paged(soffset)
            if not page:
                break

            # apply optional filters
            if self.filter_hostname:
                page = {k: v for k, v in page.items() if v.get("hostname") == self.filter_hostname}
            if self.filter_interface:
                page = {k: v for k, v in page.items() if v.get("ifDescr") == self.filter_interface}

            for key_simple, m in page.items():
                processed += 1
                hostname = m.get("hostname")
                ifDescr = m.get("ifDescr")
                status = int(m.get("ifOperStatus", UP_VALUE))

                st, created = self._get_or_create_state(key_simple)

                # Baseline: if created now, store status and DO NOT alert this cycle
                if created:
                    created_baseline += 1
                    st.extended_state = (st.extended_state or {})
                    st.extended_state["status"] = status
                    st.is_active = False
                    st.consecutive = 0
                    continue

                prev_status = None
                try:
                    prev_status = (st.extended_state or {}).get("status", None)
                except Exception:
                    prev_status = None

                # store latest status always
                st.extended_state = (st.extended_state or {})
                st.extended_state["status"] = status

                now = datetime.utcnow()
                ist_time = (now + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")

                # Evaluate DOWN/UP with consecutive threshold
                if status == DOWN_VALUE:
                    st.consecutive = int(st.consecutive or 0) + 1

                    if st.consecutive >= self.threshold and not st.is_active:
                        st.is_active = True
                        st.last_triggered = now

                        send_notification(
                            template="snmp_interface_alert",
                            rule=self.rule,
                            hostname=hostname,
                            interface=ifDescr,
                            metric_name="ifOperStatus",
                            metric_value=status,
                            alert_time_ist=ist_time
                        )
                elif status == UP_VALUE:
                    # recovery path
                    if st.is_active:
                        downtime = None
                        if st.last_triggered:
                            downtime = int((now - st.last_triggered).total_seconds())

                        send_notification(
                            template="snmp_interface_recovery",
                            rule=self.rule,
                            hostname=hostname,
                            interface=ifDescr,
                            metric_name="ifOperStatus",
                            metric_value=status,
                            alert_time_ist=ist_time,
                            downtime_seconds=downtime,
                            downtime_human=str(timedelta(seconds=downtime)) if downtime is not None else None
                        )

                    st.is_active = False
                    st.consecutive = 0
                    st.last_recovered = now
                else:
                    # other statuses -> do not trigger, do not increment; optionally reset consecutive
                    st.consecutive = 0

                # commit in batches
                if processed % self.DB_COMMIT_EVERY == 0:
                    db.session.commit()

            # next page
            soffset += self.SERIES_PAGE_SIZE

            # lightweight progress log
            if page_num % 5 == 0:
                print(
                    f"[SNMPInterfaceHandler:{self.rule_id}] progress pages={page_num} processed={processed} baseline_new={created_baseline}",
                    flush=True
                )

        db.session.commit()
        print(
            f"[SNMPInterfaceHandler:{self.rule_id}] done processed={processed} baseline_new={created_baseline} took={time.time()-t_start:.2f}s",
            flush=True
        )

