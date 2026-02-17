#!/usr/bin/env python3
import os
import time
from datetime import datetime

from app import app
from services.itam.scheduler import run_scheduler_tick


POLL_SECONDS = max(10, int(os.environ.get("ITAM_DISCOVERY_POLL_SECONDS", "30")))

print("=" * 56)
print("   Autointelli ITAM Discovery Scheduler Started")
print("=" * 56)
print(f"Polling every {POLL_SECONDS} seconds\n")


while True:
    try:
        with app.app_context():
            result = run_scheduler_tick()
            if result.get("ran"):
                print(f"[{datetime.utcnow().isoformat()}Z] ITAM scheduled discovery executed")
            else:
                reason = result.get("reason", "idle")
                print(f"[{datetime.utcnow().isoformat()}Z] ITAM scheduler idle ({reason})")
    except KeyboardInterrupt:
        print("\n[ITAM Scheduler] Stopped by user")
        break
    except Exception as ex:
        print(f"[ITAM Scheduler] Unexpected crash: {ex}")

    time.sleep(POLL_SECONDS)
