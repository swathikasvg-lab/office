# alert_engine/trigger/event_store.py

from extensions import db
from models.alert_rule_state import AlertRuleState
from datetime import datetime

def log_event(rule, metrics, status):
    """
    Optional: store events
    status: DOWN / RECOVERY
    """
    print(f"[EVENT] Rule {rule.id} → {status}: {metrics}")
    # If needed later, add DB log table.

