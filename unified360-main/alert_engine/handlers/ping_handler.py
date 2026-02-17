import requests
from datetime import datetime
from flask import current_app

from extensions import db
from models.ping import PingConfig
from models.alert_rule_state import AlertRuleState
from alert_engine.trigger.notifier import send_notification


class PingHandler:

    # ---------------------------------------------------------
    def fetch_latest(self, host):
        influx_url = current_app.config["INFLUXDB_URL"]
        dbname = current_app.config["INFLUXDB_DB"]

        q = (
            'SELECT * FROM "ping" '
            f"WHERE url = '{host}' "
            "ORDER BY time DESC LIMIT 1"
        )

        try:
            r = requests.get(
                influx_url,
                params={"db": dbname, "q": q},
                timeout=5
            )
            r.raise_for_status()
            js = r.json()

            if "series" not in js["results"][0]:
                return None

            cols = js["results"][0]["series"][0]["columns"]
            vals = js["results"][0]["series"][0]["values"][0]
            return dict(zip(cols, vals))

        except Exception:
            return None

    # ---------------------------------------------------------
    def extract_metrics(self, row):
        if not row:
            return {
                "latency_ms": None,
                "packet_loss": None,
                "result": "timeout"
            }

        return {
            "latency_ms": row.get("average_response_ms"),
            "packet_loss": row.get("percent_packet_loss"),
            "result": row.get("result_code")
        }

    # ---------------------------------------------------------
    def evaluate_logic(self, logic, metrics):
        op = logic.get("op")
        results = []

        for cond in logic.get("children", []):
            field = cond["field"]
            operator = cond["op"]
            value = cond["value"]
            actual = metrics.get(field)

            try:
                actual = float(actual)
                value = float(value)
            except Exception:
                results.append(False)
                continue

            if operator == ">": results.append(actual > value)
            elif operator == "<": results.append(actual < value)
            elif operator == ">=": results.append(actual >= value)
            elif operator == "<=": results.append(actual <= value)
            elif operator in ("=", "=="): results.append(actual == value)
            elif operator == "!=": results.append(actual != value)

        return all(results) if op == "AND" else any(results)

    # ---------------------------------------------------------
    def evaluate_host(self, rule, monitor):
        host = monitor.host
        key = host

        state = AlertRuleState.query.filter_by(
            rule_id=rule.id,
            customer_id=rule.customer_id,
            target_value=key
        ).first()

        if not state:
            state = AlertRuleState(
                rule_id=rule.id,
                customer_id=rule.customer_id,
                target_value=key,
                is_active=False,
                consecutive=0
            )
            db.session.add(state)
            db.session.flush()

        latest = self.fetch_latest(host)
        metrics = self.extract_metrics(latest)
        matched = self.evaluate_logic(rule.logic_json, metrics)

        now = datetime.utcnow().astimezone()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        if matched:
            state.consecutive += 1

            if state.consecutive >= rule.evaluation_count and not state.is_active:
                state.is_active = True
                state.last_triggered = now

                template = (
                    "ping_packetloss"
                    if "packet" in rule.name.lower()
                    else "ping_latency"
                )

                send_notification(
                    template=template,
                    rule=rule,
                    hostname=host,
                    latency_ms=metrics.get("latency_ms"),
                    packet_loss=metrics.get("packet_loss"),
                    alert_time=now_str
                )

        else:
            if state.is_active:
                send_notification(
                    template="ping_recovery",
                    rule=rule,
                    hostname=host,
                    latency_ms=metrics.get("latency_ms"),
                    packet_loss=metrics.get("packet_loss"),
                    recovery_time=now_str
                )

            state.is_active = False
            state.consecutive = 0
            state.last_recovered = now

        db.session.commit()

    # ---------------------------------------------------------
    def execute(self, rule):
        monitors = (
            PingConfig.query
            .filter_by(customer_id=rule.customer_id)
            .all()
        )

        for monitor in monitors:
            self.evaluate_host(rule, monitor)

