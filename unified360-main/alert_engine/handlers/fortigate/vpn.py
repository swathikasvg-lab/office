# handlers/fortigate/vpn.py

import requests
from datetime import datetime, timedelta
from flask import current_app
from alert_engine.trigger.notifier import send_notification
from alert_engine.trigger.state_manager import StateManager

state_mgr = StateManager()

class FortigateVpnHandler:

    # ----------------------------------------------------
    # Fetch latest value per firewall per Tunnel (all KPIs)
    # ----------------------------------------------------
    def fetch_vpn_tunnels(self):
        influx = (
            current_app.config.get("FORTIGATE_INFLUXDB_URL")
            or current_app.config.get("INFLUXDB_URL", "http://127.0.0.1:8086/query")
        )
        dbname = current_app.config.get("FORTIGATE_INFLUXDB_DB", "fortigate")

        q = """
        SELECT 
            LAST(vpn_status) AS vpn_status,
            LAST(vpn_name) AS vpn_name,
            LAST(hostname) AS hostname,
            LAST(fgVpnTunEntInOctets) AS in_octets,
            LAST(fgVpnTunEntOutOctets) AS out_octets,
            LAST(fgVpnTunEntLifeSecs) AS life_secs
        FROM vpn_tunnels
        GROUP BY hostname, vpn_name
        """

        try:
            r = requests.get(influx, params={"db": dbname, "q": q}, timeout=10)
            r.raise_for_status()
            js = r.json()

            if "series" not in js["results"][0]:
                return []

            tunnels = []
            for s in js["results"][0]["series"]:
                row = s["values"][0]
                cols = s["columns"]
                d = dict(zip(cols, row))

                # Fix tags
                d["hostname"] = s["tags"].get("hostname")
                d["vpn_name"] = s["tags"].get("vpn_name")

                tunnels.append(d)

            return tunnels

        except Exception as e:
            print("[FortigateVPN] Influx error:", e)
            return []

    # ----------------------------------------------------
    # KPI extraction (Down, In Traffic Mbps, Out Traffic Mbps)
    # ----------------------------------------------------
    def extract_metrics(self, t):
        # Convert octets to Mbps
        in_mbps = 0
        out_mbps = 0
        try:
            sec = float(t.get("life_secs") or 1)
            in_mbps = (float(t.get("in_octets") or 0) * 8) / (sec * 1024 * 1024)
            out_mbps = (float(t.get("out_octets") or 0) * 8) / (sec * 1024 * 1024)
        except:
            pass

        return {
            "vpn_tunnel_down": "DOWN" if t.get("vpn_status") == 1 else "UP",
            "vpn_status": t.get("vpn_status"),
            "vpn_tunnel_in_mbps": round(in_mbps, 2),
            "vpn_tunnel_out_mbps": round(out_mbps, 2)
        }

    # ----------------------------------------------------
    # JSON Rule Evaluator (supports > < = != for numeric & string)
    # ----------------------------------------------------
    def evaluate(self, logic, metrics):
        try:
            op = logic.get("op")
        except:
            return False

        children = logic.get("children", [])
        results = []

        for cond in children:
            field = cond["field"]
            operator = cond["op"]
            value = cond["value"]
            actual = metrics.get(field)

            # numeric compare
            try:
                actual_f = float(actual)
                value_f = float(value)

                if operator == ">":
                    results.append(actual_f > value_f)
                elif operator == "<":
                    results.append(actual_f < value_f)
                elif operator == "=":
                    results.append(actual_f == value_f)
                elif operator == "!=":
                    results.append(actual_f != value_f)
                else:
                    results.append(False)

            except:
                # string compare
                if operator == "=":
                    results.append(actual == value)
                elif operator == "!=":
                    results.append(actual != value)
                else:
                    results.append(False)

        return all(results) if op == "AND" else any(results)

    # ----------------------------------------------------
    # EXECUTE — supports 3 KPIs: Down, IN, OUT
    # ----------------------------------------------------
    def execute(self, rule, unused_state=None):
        tunnels = self.fetch_vpn_tunnels()
        if not tunnels:
            print("[FortigateVPN] No VPN data found")
            return

        threshold = rule.evaluation_count or 1

        for t in tunnels:

            fw = t.get("hostname") or "UnknownFW"
            vname = t.get("vpn_name") or "UnknownVPN"

            metrics = self.extract_metrics(t)
            passed = self.evaluate(rule.logic_json, metrics)

            # Unique rule + metric key (supports down/in/out)
            metric_key = list(rule.logic_json["children"])[0]["field"]
            key = f"vpn::{fw}::{vname}::{metric_key}"

            action, downtime = state_mgr.update_state(
                rule, key, passed, threshold
            )

            # IST time
            ist_time = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            # ---------- TRIGGER ----------
            if action == "TRIGGER":
                send_notification(
                    template="fortigate_vpn_down" if metric_key == "vpn_tunnel_down" else "fortigate_vpn_alert",
                    rule=rule,
                    hostname=fw,
                    vpn_name=vname,
                    metric_name=metric_key,
                    metric_value=metrics.get(metric_key),
                    alert_time_ist=ist_time,
                )

            # ---------- RECOVERY ----------
            elif action == "RECOVERY":
                human = str(timedelta(seconds=downtime))

                send_notification(
                    template="fortigate_vpn_recovery" if metric_key == "vpn_tunnel_down" else "fortigate_vpn_recovery_traffic",
                    rule=rule,
                    hostname=fw,
                    vpn_name=vname,
                    metric_name=metric_key,
                    metric_value=metrics.get(metric_key),
                    alert_time_ist=ist_time,
                    downtime_seconds=downtime,
                    downtime_human=human
                )

