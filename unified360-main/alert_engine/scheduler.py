# alert_engine/scheduler.py

import time
from alert_engine.engine import run_alert_cycle

def start_scheduler(app, interval=60):
    """Runs the engine every X seconds."""
    print(f"[AlertEngine] Scheduler started every {interval} seconds")
    while True:
        try:
            with app.app_context():
                run_alert_cycle()
        except Exception as e:
            print("[AlertEngine] ERROR:", e)

        time.sleep(interval)

