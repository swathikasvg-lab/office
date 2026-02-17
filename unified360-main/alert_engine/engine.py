from datetime import datetime, timezone
from extensions import db
from models.alert_rule import AlertRule
from alert_engine.handlers import HANDLER_REGISTRY
import time

class AlertEngine:

    def run(self, rule_filter=None):
        query = AlertRule.query.filter_by(is_enabled=True)

        if rule_filter:
            query = rule_filter(query)

        rules = query.all()
        print("[run_alert_cycle] enabled rules:", [(r.id, r.monitoring_type) for r in rules])

        for rule in rules:
            handler = HANDLER_REGISTRY.get(rule.monitoring_type)
            print(f"[dispatch] rule={rule.id} type={rule.monitoring_type} handler={handler}")
            if not handler:
                continue

            t0=time.time()

            try:
                print(f"[execute:start] rule={rule.id}", flush=True)
                handler.execute(rule)
                print(f"[execute:done]  rule={rule.id} took={time.time()-t0:.2f}s", flush=True)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"[AlertEngine] ERROR: rule {rule.id} ({rule.name}) → {e}", flush=True)


def run_alert_cycle():
    print(
        f"[AlertEngine] Running rule-based cycle at "
        f"{datetime.now(timezone.utc).isoformat()} UTC"
    )
    engine = AlertEngine()
    engine.run()


def run_device_updown_only():
    from alert_engine.handlers.device_updown import run_device_updown_cycle

    run_device_updown_cycle()

