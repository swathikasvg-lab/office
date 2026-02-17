# alert_engine/handlers/oracle_handler.py

import requests
from datetime import datetime
from flask import current_app

from extensions import db
from models.alert_rule_state import AlertRuleState
from models.oracle_db_monitor import OracleDbMonitor
from alert_engine.trigger.notifier import send_notification


class OracleHandler:
    """
    Supports Oracle alert rules with:
      - db_status (string): UP/DOWN (derived from oracledb_up)
      - tablespace_usage_pct (number): from oracledb_tablespace_used_percent
      - active_sessions (number): from oracledb_sessions_value (status="ACTIVE", type="USER")

    Key behavior:
      - If rule.oracle_tablespace == "__ALL__" (or empty):
          evaluate the rule PER TABLESPACE and alert with the actual tablespace that breached.
      - If rule.oracle_tablespace is a specific tablespace:
          evaluate only that tablespace.
    """

    # ----------------------------
    def _prom_url(self):
        return current_app.config.get("PROMETHEUS_URL", "http://localhost:9090")

    def prom_query(self, query: str):
        r = requests.get(
            f"{self._prom_url()}/api/v1/query",
            params={"query": query},
            timeout=10
        )
        r.raise_for_status()
        js = r.json()
        return (js.get("data") or {}).get("result") or []

    def _first_value(self, result):
        """
        result item format:
          {"metric": {...}, "value": [ts, "1"]}
        """
        if not result:
            return None
        try:
            return float(result[0]["value"][1])
        except Exception:
            return None

    # ----------------------------
    def _get_rule_template(self, rule):
        # If someone uses db_status DOWN, prefer db down template
        logic = rule.logic_json or {}
        for c in (logic.get("children") or []):
            if isinstance(c, dict) and c.get("field") == "db_status":
                return "oracle_db_down"
        return "oracle_threshold_alert"

    def _get_or_create_state(self, rule, target_key: str):
        state = AlertRuleState.query.filter_by(
            rule_id=rule.id,
            customer_id=rule.customer_id,
            target_value=target_key
        ).first()

        if not state:
            state = AlertRuleState(
                rule_id=rule.id,
                customer_id=rule.customer_id,
                target_value=target_key,
                is_active=False,
                consecutive=0
            )
            db.session.add(state)
            db.session.flush()

        return state

    # ----------------------------
    def _compare(self, actual, op, expected):
        # Normalize "=" to "=="
        if op == "=":
            op = "=="

        # String compares
        if isinstance(expected, str) or isinstance(actual, str):
            if op == "==":
                return str(actual) == str(expected)
            if op == "!=":
                return str(actual) != str(expected)
            # unsupported numeric ops for strings
            return False

        # Numeric compares
        if actual is None:
            return False

        try:
            a = float(actual)
            e = float(expected)
        except Exception:
            return False

        if op == ">":
            return a > e
        if op == ">=":
            return a >= e
        if op == "<":
            return a < e
        if op == "<=":
            return a <= e
        if op == "==":
            return a == e
        if op == "!=":
            return a != e
        return False

    def evaluate_logic(self, logic, metrics):
        op = (logic or {}).get("op", "AND")
        results = []

        for cond in (logic or {}).get("children", []):
            # Nested groups support
            if isinstance(cond, dict) and "children" in cond and "op" in cond:
                results.append(self.evaluate_logic(cond, metrics))
                continue

            if not isinstance(cond, dict):
                continue

            field = cond.get("field")
            operator = cond.get("op", "==")
            expected = cond.get("value")
            actual = metrics.get(field)

            results.append(self._compare(actual, operator, expected))

        return all(results) if op == "AND" else any(results)

    # ----------------------------
    def _fetch_db_status(self, monitor: OracleDbMonitor):
        mid = str(monitor.id)
        # Prefer scoped to DBNAME too (safer if multiple DBs share MonitorID patterns)
        # If your exporter doesn't include DBNAME in oracledb_up, this still works with MonitorID only.
        up_q = f'oracledb_up{{MonitorID="{mid}"}}'
        up_res = self.prom_query(up_q)
        up_val = self._first_value(up_res)
        return "UP" if up_val == 1 else "DOWN"

    def _fetch_active_sessions(self, monitor: OracleDbMonitor):
        mid = str(monitor.id)
        sess_q = f'sum(oracledb_sessions_value{{MonitorID="{mid}",status="ACTIVE",type="USER"}})'
        sess_res = self.prom_query(sess_q)
        return self._first_value(sess_res)

    def _fetch_tablespace_usage(self, monitor: OracleDbMonitor, tablespace: str):
        mid = str(monitor.id)
        ts_q = f'oracledb_tablespace_used_percent{{MonitorID="{mid}",tablespace="{tablespace}"}}'
        ts_res = self.prom_query(ts_q)
        return self._first_value(ts_res)

    def _fetch_all_tablespaces(self, monitor: OracleDbMonitor):
        """
        Returns list of dicts: [{"tablespace": "SYSTEM", "usage_pct": 12.3}, ...]
        """
        mid = str(monitor.id)
        q = f'oracledb_tablespace_used_percent{{MonitorID="{mid}"}}'
        res = self.prom_query(q)

        out = []
        for item in res:
            try:
                labels = item.get("metric") or {}
                ts = labels.get("tablespace")
                val = float(item["value"][1])
                if ts:
                    out.append({"tablespace": ts, "usage_pct": val})
            except Exception:
                continue

        return out

    # ----------------------------
    def execute(self, rule):
        """
        For __ALL__ tablespaces:
          - evaluate per tablespace
          - maintain independent state per tablespace (so recovery works per TS)
        """
        monitor = OracleDbMonitor.query.get(rule.oracle_monitor_id)
        if not monitor:
            return

        logic = rule.logic_json or {"op": "AND", "children": []}
        now = datetime.utcnow()

        hostport = f"{monitor.host}:{monitor.port}"
        dbname = monitor.service_name  # (XE / XEPDB1 etc.)

        # Shared metrics across tablespaces
        db_status = self._fetch_db_status(monitor)
        active_sessions = self._fetch_active_sessions(monitor)

        selected_ts = (rule.oracle_tablespace or "__ALL__").strip() or "__ALL__"

        # ----------------------------
        # Case 1: Specific tablespace
        # ----------------------------
        if selected_ts != "__ALL__":
            ts_val = self._fetch_tablespace_usage(monitor, selected_ts)

            metrics = {
                "db_status": db_status,
                "tablespace_usage_pct": ts_val,
                "active_sessions": active_sessions,
            }

            matched = self.evaluate_logic(logic, metrics)
            target_key = f"oracle:{monitor.id}:{dbname}:{selected_ts}"
            state = self._get_or_create_state(rule, target_key)

            if matched:
                state.consecutive += 1
                if state.consecutive >= rule.evaluation_count and not state.is_active:
                    state.is_active = True
                    state.last_triggered = now

                    send_notification(
                        template=self._get_rule_template(rule),
                        rule=rule,
                        hostname=hostport,
                        dbname=dbname,
                        oracle_monitor_id=monitor.id,
                        oracle_tablespace=selected_ts,
                        db_status=db_status,
                        tablespace_usage_pct=ts_val,
                        active_sessions=active_sessions,
                        alert_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                    )
            else:
                if state.is_active:
                    send_notification(
                        template="oracle_recovery",
                        rule=rule,
                        hostname=hostport,
                        dbname=dbname,
                        oracle_monitor_id=monitor.id,
                        oracle_tablespace=selected_ts,
                        recovery_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                    )

                state.is_active = False
                state.consecutive = 0
                state.last_recovered = now

            db.session.commit()
            return

        # ----------------------------
        # Case 2: __ALL__ tablespaces (evaluate PER TS)
        # ----------------------------
        tablespaces = self._fetch_all_tablespaces(monitor)

        # If no tablespace metrics at all, we can still allow db_status-only rules to trigger,
        # but we cannot do per tablespace evaluation. In that case, use a single key.
        if not tablespaces:
            metrics = {
                "db_status": db_status,
                "tablespace_usage_pct": None,
                "active_sessions": active_sessions,
            }
            matched = self.evaluate_logic(logic, metrics)

            target_key = f"oracle:{monitor.id}:{dbname}:__ALL__"
            state = self._get_or_create_state(rule, target_key)

            if matched:
                state.consecutive += 1
                if state.consecutive >= rule.evaluation_count and not state.is_active:
                    state.is_active = True
                    state.last_triggered = now
                    send_notification(
                        template=self._get_rule_template(rule),
                        rule=rule,
                        hostname=hostport,
                        dbname=dbname,
                        oracle_monitor_id=monitor.id,
                        oracle_tablespace="__ALL__",
                        db_status=db_status,
                        tablespace_usage_pct=None,
                        active_sessions=active_sessions,
                        alert_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                    )
            else:
                if state.is_active:
                    send_notification(
                        template="oracle_recovery",
                        rule=rule,
                        hostname=hostport,
                        dbname=dbname,
                        oracle_monitor_id=monitor.id,
                        oracle_tablespace="__ALL__",
                        recovery_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                state.is_active = False
                state.consecutive = 0
                state.last_recovered = now

            db.session.commit()
            return

        # Normal per-tablespace evaluation
        for ts in tablespaces:
            ts_name = ts["tablespace"]
            ts_val = ts["usage_pct"]

            metrics = {
                "db_status": db_status,
                "tablespace_usage_pct": ts_val,
                "active_sessions": active_sessions,
            }

            matched = self.evaluate_logic(logic, metrics)

            # IMPORTANT: per tablespace state key
            target_key = f"oracle:{monitor.id}:{dbname}:{ts_name}"
            state = self._get_or_create_state(rule, target_key)

            if matched:
                state.consecutive += 1
                if state.consecutive >= rule.evaluation_count and not state.is_active:
                    state.is_active = True
                    state.last_triggered = now

                    send_notification(
                        template=self._get_rule_template(rule),
                        rule=rule,
                        hostname=hostport,
                        dbname=dbname,
                        oracle_monitor_id=monitor.id,
                        oracle_tablespace=ts_name,  # ✅ actual tablespace that crossed
                        db_status=db_status,
                        tablespace_usage_pct=ts_val,
                        active_sessions=active_sessions,
                        alert_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                    )
            else:
                if state.is_active:
                    send_notification(
                        template="oracle_recovery",
                        rule=rule,
                        hostname=hostport,
                        dbname=dbname,
                        oracle_monitor_id=monitor.id,
                        oracle_tablespace=ts_name,
                        recovery_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                    )

                state.is_active = False
                state.consecutive = 0
                state.last_recovered = now

        db.session.commit()

