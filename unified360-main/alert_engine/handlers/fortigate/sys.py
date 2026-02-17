import requests
from datetime import datetime, timedelta
from flask import current_app

from alert_engine.trigger.notifier import send_notification
from alert_engine.trigger.state_manager import StateManager

state_mgr = StateManager()

class FortigateSystemHandler:

    # ----------------------------------------------------
    # Fetch LAST() memory + session count grouped per FW
    # ----------------------------------------------------
    def fetch_system_stats(self):
        influx = (
            current_app.config.get("FORTIGATE_INFLUXDB_URL")
            or current_app.config.get("INFLUXDB_URL", "http://127.0.0.1:8086/query")
        )
        dbname = current_app.config.get("FORTIGATE_INFLUXDB_DB", "fortigate")

        q = """
        SELECT 
            LAST(memory_usage) AS mem_usage,
            LAST(session_count) AS session_count
        FROM snmpdevice
        WHERE template_type='Fortigate'
        GROUP BY hostname
        """

        try:
            r = requests.get(influx, params={"db": dbname, "q": q}, timeout=10)
            r.raise_for_status()
            js = r.json()

            if "series" not in js["results"][0]:
                return []

            results = []
            for s in js["results"][0]["series"]:
                cols = s.get("columns")
                vals = s.get("values")[0]
                d = dict(zip(cols, vals))

                d["hostname"] = s["tags"].get("hostname", "UnknownFW")
                results.append(d)

            return results
        except Exception as e:
            print("[SystemHandler] Influx error:", e)
            return []

    # ----------------------------------------------------
    # Extract metrics for rule evaluation
    # ----------------------------------------------------
    def extract_metrics(self, rec):
        def norm(v):
            try:
                return float(v)
            except:
                return None

        return {
            "mem_usage": norm(rec.get("mem_usage")),
            "session_count": norm(rec.get("session_count"))
        }

    # ----------------------------------------------------
    # Generic evaluator
    # ----------------------------------------------------
    def evaluate(self, logic, metrics):
        try:
            op = logic.get("op", "AND")
            children = logic.get("children", [])
        except:
            return False

        results = []

        for cond in children:
            field = cond["field"]
            operator = cond["op"]
            value = float(cond["value"])

            actual = metrics.get(field)
            if actual is None:
                results.append(False)
                continue

            if operator == ">":
                results.append(actual > value)
            elif operator == "<":
                results.append(actual < value)
            elif operator == "=":
                results.append(actual == value)
            elif operator == "!=":
                results.append(actual != value)
            else:
                results.append(False)

        return all(results) if op == "AND" else any(results)

    # ----------------------------------------------------
    # EXECUTE – evaluate each Fortigate device
    # ----------------------------------------------------
    def execute(self, rule, state=None):
        rows = self.fetch_system_stats()
        if not rows:
            print("[SystemHandler] No Fortigate system data found")
            return

        threshold = rule.evaluation_count or 1

        for rec in rows:
            fw = rec["hostname"]
            metrics = self.extract_metrics(rec)

            key = f"fortigate_sys::{fw}"

            passed = self.evaluate(rule.logic_json, metrics)

            action, downtime = state_mgr.update_state(rule, key, passed, threshold)

            # FIXED HERE
            ist_time = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")

            metric_name = rule.logic_json["children"][0]["field"]
            metric_value = metrics.get(metric_name)

            # ---------------- ALERT ----------------
            if action == "TRIGGER":
                send_notification(
                    template="fortigate_sys_alert",
                    rule=rule,
                    hostname=fw,
                    metric_name=metric_name,
                    metric_value=metric_value,
                    alert_time_ist=ist_time
                )

            # --------------- RECOVERY ---------------
            elif action == "RECOVERY":
                send_notification(
                    template="fortigate_sys_recovery",
                    rule=rule,
                    hostname=fw,
                    metric_name=metric_name,
                    metric_value=metric_value,
                    alert_time_ist=ist_time,
                    downtime_seconds=downtime,
                    downtime_human=str(timedelta(seconds=downtime))
                )

