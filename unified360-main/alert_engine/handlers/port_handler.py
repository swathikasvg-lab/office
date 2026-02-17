import requests
from datetime import datetime
from flask import current_app

from extensions import db
from models.port_monitor import PortMonitor
from models.alert_rule_state import AlertRuleState
from alert_engine.trigger.notifier import send_notification


class PortHandler:

    # ---------------------------------------------------------
    def fetch_latest(self, host, port):
        influx_url = current_app.config["INFLUXDB_URL"]
        db_name = current_app.config["INFLUXDB_DB"]

        q = (
            'SELECT * FROM "net_response" '
            f'WHERE server = \'{host}\' AND port = \'{port}\' '
            'ORDER BY time DESC LIMIT 1'
        )

        try:
            r = requests.get(
                influx_url,
                params={"db": db_name, "q": q},
                timeout=5
            )
            r.raise_for_status()
            js = r.json()

            if "series" not in js["results"][0]:
                return None

            columns = js["results"][0]["series"][0]["columns"]
            values = js["results"][0]["series"][0]["values"][0]
            return dict(zip(columns, values))

        except Exception:
            return None

    # ---------------------------------------------------------
    def extract_metrics(self, row):
        if not row:
            return {"port_status": "DOWN", "response_time_ms": None}

        result = str(row.get("result", "")).lower()
        status = "UP" if result in ("success", "ok") else "DOWN"

        return {
            "port_status": status,
            "response_time_ms": row.get("response_time"),
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

            if operator == "=":
                results.append(actual == value)
            elif operator == "!=":
                results.append(actual != value)

        return all(results) if op == "AND" else any(results)

    # ---------------------------------------------------------
    def evaluate_port(self, rule, monitor, port):
        host = monitor.host_ip
        key = f"{host}:{port}"

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

        latest = self.fetch_latest(host, port)
        metrics = self.extract_metrics(latest)
        matched = self.evaluate_logic(rule.logic_json, metrics)

        now = datetime.utcnow()

        if matched:
            state.consecutive += 1

            if (
                state.consecutive >= rule.evaluation_count
                and not state.is_active
            ):
                state.is_active = True
                state.last_triggered = now

                template = (
                    "port_alert"
                    if metrics["response_time_ms"] is None
                    else "port_slow"
                )

                send_notification(
                    template=template,
                    rule=rule,
                    hostname=host,
                    port=port,
                    response_time_ms=metrics.get("response_time_ms"),
                    alert_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                )

        else:
            if state.is_active:
                send_notification(
                    template="port_recovery",
                    rule=rule,
                    hostname=host,
                    port=port,
                    recovery_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                )

            state.is_active = False
            state.consecutive = 0
            state.last_recovered = now

        db.session.commit()

    # ---------------------------------------------------------
    def execute(self, rule):
        monitors = (
            PortMonitor.query
            .filter_by(customer_id=rule.customer_id, active=True)
            .all()
        )

        for monitor in monitors:
            ports = str(monitor.ports).split(",")

            for port in ports:
                port = port.strip()
                if port:
                    self.evaluate_port(rule, monitor, port)

