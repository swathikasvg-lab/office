import requests
from datetime import datetime, timedelta
from flask import current_app
from alert_engine.trigger.notifier import send_notification
from alert_engine.trigger.state_manager import StateManager

state_mgr = StateManager()

class FortigateSdwanHandler:
    """
    Handler to evaluate per-link SDWAN KPIs:
      - Link Down (fgVWLHealthCheckLinkState)
      - Latency (fgVWLHealthCheckLinkLatency or hc_latency)
      - Jitter (fgVWLHealthCheckLinkJitter or hc_jitter)
      - Packet Loss (fgVWLHealthCheckLinkPacketLoss or hc_packet_loss)

    This fetches LAST() for each KPI grouped by hostname + link name so each link is evaluated independently.
    """

    def fetch_sdwan_links(self):
        influx = (
            current_app.config.get("FORTIGATE_INFLUXDB_URL")
            or current_app.config.get("INFLUXDB_URL", "http://127.0.0.1:8086/query")
        )
        dbname = current_app.config.get("FORTIGATE_INFLUXDB_DB", "fortigate")

        # Use LAST() on key fields and group by hostname + link name (hc_name / fgVWLHealthCheckLinkName)
        q = """
        SELECT
          LAST(fgVWLHealthCheckLinkState) AS link_state,
          LAST(fgVWLHealthCheckLinkLatency) AS latency,
          LAST(fgVWLHealthCheckLinkJitter) AS jitter,
          LAST(fgVWLHealthCheckLinkPacketLoss) AS packet_loss,
          LAST(hc_latency) AS hc_latency,
          LAST(hc_jitter) AS hc_jitter,
          LAST(hc_packet_loss) AS hc_packet_loss,
          LAST(fgVWLHealthCheckLinkName) AS link_name
        FROM sdwan_health
        GROUP BY hostname, hc_name
        """

        try:
            r = requests.get(influx, params={"db": dbname, "q": q}, timeout=10)
            r.raise_for_status()
            js = r.json()
            #print(js)
            if "results" not in js or "series" not in js["results"][0]:
                return []
            tunnels = []
            for s in js["results"][0]["series"]:
                cols = s.get("columns", [])
                vals = s.get("values", [])
                if not vals:
                    continue
                row = vals[0]
                d = dict(zip(cols, row))
                # tags might contain hostname or hc_name
                tags = s.get("tags", {}) or {}
                d["hostname"] = tags.get("hostname") or d.get("hostname") or "UnknownFW"
                # prefer the explicit FG link name tag, fall back to fgVWL... field or hc_name tag
                d["link_name"] = tags.get("fgVWLHealthCheckLinkName") or tags.get("hc_name") or d.get("link_name") or "UnknownLink"
                tunnels.append(d)
            return tunnels
        except Exception as e:
            print("[FortigateSDWAN] Influx error:", e)
            return []

    def extract_metrics(self, rec):
        # pick the most reliable latency/jitter/packet_loss fields (use fgVWL first then hc_ fields)
        def pick(*keys):
            for k in keys:
                v = rec.get(k)
                if v is not None:
                    return v
            return None

        latency = pick("latency", "hc_latency")
        jitter  = pick("jitter", "hc_jitter")
        ploss   = pick("packet_loss", "hc_packet_loss")
        state   = rec.get("link_state")

        # normalize numeric strings to floats where possible
        def norm(x):
            if x is None:
                return None
            try:
                return float(x)
            except Exception:
                # sometimes field is a string containing numeric -> try cleaning
                try:
                    return float(str(x).strip())
                except:
                    return None

        return {
            "sdwan_link_state": int(state) if state is not None else None,   # 0/1 etc
            "sdwan_link_down": "DOWN" if state is not None and int(state) != 1 else "UP",
            "sdwan_link_latency_ms": norm(latency),
            "sdwan_link_jitter_ms": norm(jitter),
            "sdwan_link_packet_loss": norm(ploss)
        }

    def evaluate(self, logic, metrics):
        # supports simple operators for numeric and string fields (=, !=, >, <)
        try:
            op = logic.get("op", "AND")
            children = logic.get("children", []) or []
        except Exception:
            return False

        results = []
        for cond in children:
            field = cond.get("field")
            operator = cond.get("op")
            value = cond.get("value")

            actual = metrics.get(field)
            # string comparison if value is string
            if isinstance(value, str):
                if operator == "=":
                    results.append(str(actual) == value)
                elif operator == "!=":
                    results.append(str(actual) != value)
                else:
                    results.append(False)
                continue

            # numeric compare
            try:
                actual_n = float(actual) if actual is not None else None
                value_n = float(value)
                if actual_n is None:
                    results.append(False)
                    continue
                if operator == ">":
                    results.append(actual_n > value_n)
                elif operator == "<":
                    results.append(actual_n < value_n)
                elif operator == "=":
                    results.append(actual_n == value_n)
                elif operator == "!=":
                    results.append(actual_n != value_n)
                else:
                    results.append(False)
            except Exception:
                results.append(False)

        return all(results) if op == "AND" else any(results)

    def execute(self, rule, unused_state=None):
        links = self.fetch_sdwan_links()
        if not links:
            print("[FortigateSDWAN] No sdwan link data")
            return

        threshold = rule.evaluation_count or 1

        for rec in links:
            fw = rec.get("hostname", "UnknownFW")
            link = rec.get("link_name", "UnknownLink")
            key_base = f"sdwan::{fw}::{link}"

            metrics = self.extract_metrics(rec)

            # Determine which field the rule logic refers to — we pass same rule.logic_json used by UI
            passed = self.evaluate(rule.logic_json, metrics)

            action, downtime = state_mgr.update_state(rule, f"{key_base}::kpi", passed, threshold)

            ist_time = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S IST")

            if action == "TRIGGER":
                # pick a human readable metric/value for the email (try to be helpful)
                # If the rule logic used packet loss/latency/jitter choose appropriate label
                metric_name = None
                metric_value = None
                for prefer in ("sdwan_link_packet_loss", "sdwan_link_latency_ms", "sdwan_link_jitter_ms", "sdwan_link_down"):
                    if prefer in rule_logic_fields(rule):
                        metric_name = prefer
                        metric_value = metrics.get(prefer)
                        break
                # fallback: choose first non-none metric
                if metric_name is None:
                    for k, v in metrics.items():
                        if v is not None:
                            metric_name = k
                            metric_value = v
                            break

                send_notification(
                    template="fortigate_sdwan_alert",
                    rule=rule,
                    hostname=fw,
                    link_name=link,
                    metric=metric_name or "metric",
                    value=metric_value,
                    alert_time_ist=ist_time
                )

            elif action == "RECOVERY":
                human = str(timedelta(seconds=downtime)) if downtime is not None else "0s"
            
                # pick which metric was used in the rule
                recovered_metric = None
                recovered_value = None
                for field in rule_logic_fields(rule):
                    if field in metrics:
                        recovered_metric = field
                        recovered_value = metrics[field]
                        break
            
                send_notification(
                    template="fortigate_sdwan_recovery",
                    rule=rule,
                    hostname=fw,
                    link_name=link,
                    alert_time_ist=ist_time,
                    downtime_seconds=downtime,
                    downtime_human=human,
                    recovered_metric=recovered_metric,
                    recovered_value=recovered_value
                )

# Helper: finds referenced fields inside rule.logic_json to pick a metric for subject/body
def rule_logic_fields(rule):
    try:
        children = (rule.logic_json or {}).get("children", []) or []
        return [c.get("field") for c in children if "field" in c]
    except Exception:
        return []

