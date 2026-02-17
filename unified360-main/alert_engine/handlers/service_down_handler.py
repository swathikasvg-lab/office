# alert_engine/handlers/service_down_handler.py
import requests
from datetime import datetime, timezone
from flask import current_app

from extensions import db
from models.alert_rule_state import AlertRuleState
from alert_engine.trigger.notifier import send_notification


class ServiceDownHandler:
    """
    service_down rule:
      - rule.svc_instance is the Windows target (e.g., WIN-1MK4LLUIVC9)
      - logic_json contains: {"children":[{"field":"service_name","op":"=","value":"W32Time"}]}
    """

    # ----------------------------
    def _prom_url(self):
        # Prefer app config, fallback to localhost
        return current_app.config.get("PROMETHEUS_URL", "http://localhost:9090")

    def prom_query(self, query: str):
        try:
            r = requests.get(
                f"{self._prom_url()}/api/v1/query",
                params={"query": query},
                timeout=10
            )
            if not r.ok:
                return []
            return r.json().get("data", {}).get("result", []) or []
        except Exception:
            return []

    # ----------------------------
    def _get_service_name_from_logic(self, logic_json):
        logic = logic_json or {}
        for c in logic.get("children", []) or []:
            if (c.get("field") == "service_name") and (c.get("op") in ("=", "==")):
                v = (c.get("value") or "").strip()
                if v:
                    return v
        return None

    # ----------------------------
    def _normalize_linux_unit(self, service_name: str) -> str:
        s = (service_name or "").strip()
        if not s:
            return s
        # common convenience: allow "sshd" and convert to "sshd.service"
        if "." not in s:
            return s + ".service"
        return s

    def _service_running(self, instance: str, service_name: str):
        """
        Cross-platform service state resolver.
    
        Returns: (running: bool | None, meta: dict)
          - True/False when confidently known
          - None when no metric matched
        """
    
        # -------------------------
        # 1) WINDOWS (your existing logic)
        # -------------------------
        svc = (service_name or "").strip().lower()
    
        q1 = f'windows_service_state{{instance="{instance}", name="{svc}", state="running"}}'
        r1 = self.prom_query(q1)
        if r1:
            try:
                val = float(r1[0]["value"][1])
                return (val >= 0.5), {"os": "windows", "metric": "windows_service_state", "query": q1, "value": val}
            except Exception:
                pass
    
        q2 = f'windows_service_status{{instance="{instance}", name="{svc}", status="running"}}'
        r2 = self.prom_query(q2)
        if r2:
            try:
                val = float(r2[0]["value"][1])
                return (val >= 0.5), {"os": "windows", "metric": "windows_service_status", "query": q2, "value": val}
            except Exception:
                pass
    
        # If we have windows_service_state samples for this instance+name, but not running label, return unknown
        q3 = f'windows_service_state{{instance="{instance}", name="{svc}"}}'
        r3 = self.prom_query(q3)
        if r3:
            best = None
            for s in r3:
                st = (s.get("metric", {}).get("state") or "").lower()
                if st == "running":
                    best = s
                    break
            if best:
                try:
                    val = float(best["value"][1])
                    return (val >= 0.5), {"os": "windows", "metric": "windows_service_state", "query": q3, "value": val, "picked_state": "running"}
                except Exception:
                    pass
            return (None, {"os": "windows", "metric": "windows_service_state", "query": q3, "note": "samples_found_but_no_running_label"})
    
        # -------------------------
        # LINUX (systemd)
        # -------------------------
        unit = self._normalize_linux_unit(service_name)
        
        # Running
        q_active = (
            f'node_systemd_unit_state{{instance="{instance}", name="{unit}", state="active"}}'
        )
        r_active = self.prom_query(q_active)
        if r_active:
            try:
                val = float(r_active[0]["value"][1])
                return (val >= 0.5), {
                    "os": "linux",
                    "metric": "node_systemd_unit_state",
                    "query": q_active,
                    "value": val,
                    "state": "active"
                }
            except Exception:
                pass
        
        # Explicit DOWN detection (any non-active state)
        q_down = (
            f'node_systemd_unit_state{{instance="{instance}", name="{unit}", state!="active"}}'
        )
        r_down = self.prom_query(q_down)
        if r_down:
            try:
                val = float(r_down[0]["value"][1])
                if val >= 0.5:
                    return False, {
                        "os": "linux",
                        "metric": "node_systemd_unit_state",
                        "query": q_down,
                        "value": val,
                        "state": "non-active"
                    }
            except Exception:
                pass

    
        # Nothing found
        return (None, {"query_tried": [q1, q2, q3, q_active, q_down], "note": "no_samples"})


    # ----------------------------
    def evaluate_logic(self, logic, metrics):
      op = (logic or {}).get("op") or "AND"
      results = []

      for cond in (logic or {}).get("children", []) or []:
          field = cond.get("field")
          operator = cond.get("op")
          value = cond.get("value")
          actual = metrics.get(field)

          # ---- FIX: normalize service_name comparisons ----
          if field == "service_name":
              actual = (actual or "").strip().lower()
              value = (value or "").strip().lower()
          # -------------------------------------------------

          if operator in ("=", "=="):
              results.append(actual == value)
          elif operator == "!=":
              results.append(actual != value)

      return all(results) if op == "AND" else any(results)


    # ----------------------------
    def execute(self, rule):

        instance = (getattr(rule, "svc_instance", None) or "").strip()
        service_name = (self._get_service_name_from_logic(rule.logic_json) or "").lower()

        # If misconfigured, do nothing but keep a trace in extended_state if state exists later
        if not instance or not service_name:
            return

        # state key: instance|service
        key = f"{instance}|{service_name}"

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
                consecutive=0,
                extended_state={}
            )
            db.session.add(state)
            db.session.flush()

        now = datetime.now(timezone.utc)

        # Evaluate
        running, meta = self._service_running(instance, service_name)

        # Build metrics object to reuse your logic evaluator
        metrics = {"service_name": service_name}
        logic_ok = self.evaluate_logic(rule.logic_json, metrics)

        # Decide "matched" == service is DOWN
        # If no metric available -> treat as NOT matched (no alert), but store debug.
        if running is None:
            matched = False
        else:
            matched = (logic_ok and (running is False))

        # Update debug info always
        st = state.extended_state or {}
        st.update({
            "last_eval_utc": now.isoformat(),
            "svc_instance": instance,
            "service_name": service_name,
            "running": None if running is None else bool(running),
            "prom": meta,
        })
        state.extended_state = st

        # Trigger / recover logic (same style as your PortHandler)
        if matched:
            state.consecutive += 1
            if state.consecutive >= (rule.evaluation_count or 1) and not state.is_active:
                state.is_active = True
                state.last_triggered = now

                send_notification(
                    template="service_down",
                    rule=rule,
                    hostname=instance,
                    service_name=service_name,
                    alert_time_ist=now.astimezone().strftime("%Y-%m-%d %H:%M:%S IST"),
                )
        else:
            if state.is_active:
                send_notification(
                    template="service_recovery",
                    rule=rule,
                    hostname=instance,
                    service_name=service_name,
                    alert_time_ist=now.astimezone().strftime("%Y-%m-%d %H:%M:%S IST"),
                )
            state.is_active = False
            state.consecutive = 0
            state.last_recovered = now

        db.session.commit()

