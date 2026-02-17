from models.alert_rule_state import AlertRuleState
from extensions import db
from datetime import datetime
import json

class StateManager:
    """
    Manages per-rule AND per-metric (KPI) state tracking.
    """

    def _load_state(self, rs):
        if not rs.extended_state:
            return {}

        # JSON column usually returns dict; old data might be a JSON string
        if isinstance(rs.extended_state, dict):
            return rs.extended_state

        if isinstance(rs.extended_state, str):
            try:
                return json.loads(rs.extended_state)
            except Exception:
                return {}

        return {}

    def _save_state(self, rs, st):
        # Store dict into db.JSON column (not a string)
        rs.extended_state = st
        db.session.add(rs)
        # no commit here; let engine commit once per rule

    def get_state(self, rule, key):
        """Return (entry_dict, AlertRuleState row)"""
        customer_id = rule.customer_id

        rs = AlertRuleState.query.filter_by(
            rule_id=rule.id,
            customer_id=customer_id
        ).first()

        if not rs:
            rs = AlertRuleState(
                rule_id=rule.id,
                customer_id=customer_id,
                is_active=False,
                consecutive=0,
                extended_state={},   # dict
            )
            db.session.add(rs)
            db.session.flush()

        st = self._load_state(rs)

        if key not in st:
            st[key] = {
                "active": False,
                "consecutive": 0,
                "last_triggered": None,
                "last_recovered": None
            }
            self._save_state(rs, st)

        return st[key], rs

    def update_state(self, rule, key, eval_result, threshold):
        """
        eval_result=True  → alert condition matched
        Returns (action, downtime_seconds or None)
        action in {"TRIGGER", "RECOVERY", "NOOP"}
        """
        st_entry, rs = self.get_state(rule, key)
        now = datetime.utcnow().isoformat()

        full = self._load_state(rs)

        if eval_result:
            st_entry["consecutive"] = int(st_entry.get("consecutive", 0)) + 1

            if not st_entry.get("active") and st_entry["consecutive"] >= int(threshold or 1):
                st_entry["active"] = True
                st_entry["last_triggered"] = now
                st_entry["last_recovered"] = None

                full[key] = st_entry
                self._save_state(rs, full)
                return "TRIGGER", None

            full[key] = st_entry
            self._save_state(rs, full)
            return "NOOP", None

        # Recovery / not matched
        if st_entry.get("active"):
            st_entry["active"] = False
            st_entry["consecutive"] = 0
            st_entry["last_recovered"] = now

            downtime = None
            if st_entry.get("last_triggered"):
                try:
                    t0 = datetime.fromisoformat(st_entry["last_triggered"])
                    t1 = datetime.fromisoformat(now)
                    downtime = int((t1 - t0).total_seconds())
                except Exception:
                    downtime = None

            full[key] = st_entry
            self._save_state(rs, full)
            return "RECOVERY", downtime

        st_entry["consecutive"] = 0
        full[key] = st_entry
        self._save_state(rs, full)
        return "NOOP", None

