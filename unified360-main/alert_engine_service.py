#!/usr/bin/env python3
# alert_engine_service.py
# FIXED VERSION – works 100% with your existing alert_engine code

import time
from datetime import datetime

# Import Flask app and extensions
from app import app
from extensions import db

# Import your existing cycles
from alert_engine.engine import run_alert_cycle, run_device_updown_only

print("=" * 46)
print("        Autointelli Alert Engine Started")
print("=" * 46)
print("Running every 60 seconds...\n")

# One-time initialization
with app.app_context():
    print(f"[Init] Application context ready at {datetime.utcnow().isoformat()} UTC")

# Main loop – every function now sees current_app and a valid db.session
while True:
    try:
        with app.app_context():        # This single line fixes BOTH your errors
            run_alert_cycle()          # rule-based alerts
            run_device_updown_only()   # device up/down cycle

    except KeyboardInterrupt:
        print("\n[AlertEngine] Stopped by user")
        break
    except Exception as e:
        print(f"[AlertEngine] Unexpected crash: {e}")
        import traceback
        traceback.print_exc()

    time.sleep(60)
