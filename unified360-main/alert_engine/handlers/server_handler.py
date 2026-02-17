# alert_engine/handlers/server_handler.py
"""
ServerHandler (Prometheus)
-------------------------
Evaluates server alerts for BOTH Linux (node_exporter) and Windows (windows_exporter / wmi fallback)
using your existing AlertRule.logic_json structure and AlertRuleState state machine.

✅ Filters metrics by tenant label: CustomerName="<customers.name>" (configurable)
✅ Does NOT use "up" query
✅ Supports nested logic_json groups (AND/OR, nested children)
✅ Supports host-level metrics (CPU/MEM) and per-disk / per-interface metrics (Disk/Network)
✅ Uses AlertRule.evaluation_count as consecutive threshold
✅ Engine commits; handler does NOT commit.

Supported logic fields:
  - cpu_usage   : % CPU used
  - mem_usage   : % Memory used
  - disk_usage  : % Disk used (per mountpoint/volume)
  - disk_free   : % Disk free (per mountpoint/volume)
  - net_mbps    : Mbps (RX+TX) per interface
  - net_util    : % utilization per interface (requires link-speed metric)

State target_value formats:
  - host-level:            "<host>"
  - disk-level per mount:  "<host>|disk|<mount_or_volume>"
  - net-level per iface:   "<host>|net|<iface>"
"""

import requests
from datetime import datetime
from flask import current_app

from extensions import db
from models.alert_rule_state import AlertRuleState
from alert_engine.trigger.notifier import send_notification


class ServerHandler:
    # =========================================================
    # Prometheus helpers
    # =========================================================
    def _prom_url(self) -> str:
        return current_app.config.get("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")

    def _instance_label(self) -> str:
        # Your samples show instance="GKB-VM2" and instance="evok-web"
        return current_app.config.get("PROM_INSTANCE_LABEL", "instance")

    def _tenant_label(self) -> str:
        # Your metrics use "CustomerName" (case-sensitive)
        return current_app.config.get("PROM_TENANT_LABEL", "CustomerName")

    def _prom_escape(self, s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    def _tenant_value(self, rule) -> str:
        # rule.customer is joined in your model (lazy="joined")
        if rule.customer and rule.customer.name:
            return rule.customer.name
        return ""

    def _base_matchers(self, rule):
        """
        Returns list of raw matchers like:
          ['CustomerName="GKBVision"']
        """
        lbl = self._tenant_label()
        val = self._tenant_value(rule)
        if not val:
            return []
        return [f'{lbl}="{self._prom_escape(val)}"']

    def _m(self, rule, *extra_matchers: str, **kwargs) -> str:
        """
        Builds matcher string inside {...}

        Supports BOTH:
          - self._m(rule, 'mode="idle"', 'device!~"lo"')
          - self._m(rule, mode="idle")

        kwargs are converted to equality matchers: key="value"
        """
        parts = self._base_matchers(rule)

        # kwargs -> key="value"
        for k, v in (kwargs or {}).items():
            if v is None:
                continue
            parts.append(f'{k}="{self._prom_escape(v)}"')

        # extra raw matchers (like device!~"...", fstype!~"...")
        parts.extend([m for m in extra_matchers if m])

        return ",".join(parts)

    def prom_query(self, query: str):
        """
        Returns Prometheus instant query 'result' list (vector).
        Each item: {"metric": {...}, "value": [ts, "123.4"]}
        """
        try:
            r = requests.get(
                f"{self._prom_url()}/api/v1/query",
                params={"query": query},
                timeout=10,
            )
            r.raise_for_status()
            js = r.json()
            if js.get("status") != "success":
                return []
            data = js.get("data", {})
            if data.get("resultType") != "vector":
                return []
            return data.get("result", []) or []
        except Exception:
            return []

    # =========================================================
    # Label normalization helpers
    # =========================================================
    def _guess_host(self, metric_labels: dict) -> str:
        """
        Prefer stable hostname labels when present, else use instance.
        Your samples: hostname="GKB-VM2", instance="evok-web"
        """
        for k in ("hostname", "host", "nodename", "computer", "fqdn", "instance"):
            v = metric_labels.get(k)
            if v:
                if k == "instance":
                    return v.split(":")[0]
                return v
        return "unknown"

    def _get_iface_label(self, metric_labels: dict) -> str:
        for k in ("device", "nic", "interface", "adapter"):
            v = metric_labels.get(k)
            if v:
                return v
        return "unknown"

    def _get_disk_label(self, metric_labels: dict) -> str:
        # Linux uses mountpoint, Windows uses volume typically
        for k in ("mountpoint", "volume", "device", "path"):
            v = metric_labels.get(k)
            if v:
                return v
        return "unknown"

    # =========================================================
    # Logic evaluation (supports nesting)
    # =========================================================
    def evaluate_logic(self, logic: dict, metrics: dict) -> bool:
        """
        Supports:
          - leaf condition: {"field":"cpu_usage","op":">","value":80}
          - nested group:   {"op":"AND","children":[...]} (children can contain nested groups)
        """
        if not logic or not isinstance(logic, dict):
            return False

        # Leaf node
        if "field" in logic:
            return self._eval_condition(logic, metrics)

        op = (logic.get("op") or "AND").upper()
        children = logic.get("children") or []
        if not children:
            # IMPORTANT: avoid "AND of [] = True" which would alert forever
            return False

        results = []
        for child in children:
            if not isinstance(child, dict):
                results.append(False)
                continue
            if "field" in child:
                results.append(self._eval_condition(child, metrics))
            else:
                results.append(self.evaluate_logic(child, metrics))

        return all(results) if op == "AND" else any(results)

    def _eval_condition(self, cond: dict, metrics: dict) -> bool:
        field = cond.get("field")
        operator = cond.get("op")
        expected = cond.get("value")
        actual = metrics.get(field)

        # string compare
        if isinstance(expected, str):
            if operator in ("=", "=="):
                return str(actual) == expected
            if operator == "!=":
                return str(actual) != expected
            return False

        # numeric compare
        try:
            actual_f = float(actual)
            expected_f = float(expected)
        except Exception:
            return False

        if operator == ">":
            return actual_f > expected_f
        if operator == "<":
            return actual_f < expected_f
        if operator == ">=":
            return actual_f >= expected_f
        if operator == "<=":
            return actual_f <= expected_f
        if operator in ("=", "=="):
            return actual_f == expected_f
        if operator == "!=":
            return actual_f != expected_f
        return False

    # =========================================================
    # PromQL builders (Linux OR Windows)
    # =========================================================
    def _q_cpu_usage(self, rule) -> str:
        inst = self._instance_label()

        # Linux: node_cpu_seconds_total idle
        linux = (
            f'(100 - (avg by ({inst}) (rate(node_cpu_seconds_total'
            f'{{{self._m(rule, mode="idle")}}}[5m])) * 100))'
        )

        # Windows: windows_cpu_time_total idle (and wmi fallback)
        win = (
            f'(100 - (avg by ({inst}) (rate(windows_cpu_time_total'
            f'{{{self._m(rule, mode="idle")}}}[5m])) * 100))'
        )

        wmi = (
            f'(100 - (avg by ({inst}) (rate(wmi_cpu_time_total'
            f'{{{self._m(rule, mode="idle")}}}[5m])) * 100))'
        )

        return f"{linux} or {win} or {wmi}"

    def _q_mem_usage(self, rule) -> str:
        m = self._m(rule)

        # Linux: MemAvailable / MemTotal
        linux = (
            f'((1 - (node_memory_MemAvailable_bytes{{{m}}} / '
            f'node_memory_MemTotal_bytes{{{m}}})) * 100)'
        )

        # Windows: free / total (new + legacy)
        win = (
            f'((1 - (windows_os_physical_memory_free_bytes{{{m}}} / '
            f'windows_cs_physical_memory_bytes{{{m}}})) * 100)'
        )

        wmi = (
            f'((1 - (wmi_os_physical_memory_free_bytes{{{m}}} / '
            f'wmi_cs_physical_memory_bytes{{{m}}})) * 100)'
        )

        return f"{linux} or {win} or {wmi}"

    def _q_disk_usage(self, rule) -> str:
        inst = self._instance_label()

        # Linux: used% per mountpoint (exclude noisy fstype)
        f = 'fstype!~"tmpfs|overlay|squashfs|aufs|ramfs|nsfs|tracefs|cgroup2?"'
        linux = (
            f'(100 - (100 * (node_filesystem_avail_bytes{{{self._m(rule, f)}}} / '
            f'node_filesystem_size_bytes{{{self._m(rule, f)}}})))'
        )
        linux = f"(max by ({inst}, mountpoint) ({linux}))"

        # Windows: used% per volume
        win = (
            f'(100 - (100 * (windows_logical_disk_free_bytes{{{self._m(rule)}}} / '
            f'windows_logical_disk_size_bytes{{{self._m(rule)}}})))'
        )
        win = f"(max by ({inst}, volume) ({win}))"

        wmi = (
            f'(100 - (100 * (wmi_logical_disk_free_bytes{{{self._m(rule)}}} / '
            f'wmi_logical_disk_size_bytes{{{self._m(rule)}}})))'
        )
        wmi = f"(max by ({inst}, volume) ({wmi}))"

        return f"{linux} or {win} or {wmi}"

    def _q_disk_free(self, rule) -> str:
        inst = self._instance_label()

        # Linux: free% per mountpoint
        f = 'fstype!~"tmpfs|overlay|squashfs|aufs|ramfs|nsfs|tracefs|cgroup2?"'
        linux = (
            f'(100 * (node_filesystem_avail_bytes{{{self._m(rule, f)}}} / '
            f'node_filesystem_size_bytes{{{self._m(rule, f)}}}))'
        )
        linux = f"(max by ({inst}, mountpoint) ({linux}))"

        # Windows: free% per volume
        win = (
            f'(100 * (windows_logical_disk_free_bytes{{{self._m(rule)}}} / '
            f'windows_logical_disk_size_bytes{{{self._m(rule)}}}))'
        )
        win = f"(max by ({inst}, volume) ({win}))"

        wmi = (
            f'(100 * (wmi_logical_disk_free_bytes{{{self._m(rule)}}} / '
            f'wmi_logical_disk_size_bytes{{{self._m(rule)}}}))'
        )
        wmi = f"(max by ({inst}, volume) ({wmi}))"

        return f"{linux} or {win} or {wmi}"


    def _q_net_rx_mbps(self, rule) -> str:
        inst = self._instance_label()

        # Linux RX Mbps per device
        devf = 'device!~"lo|docker.*|veth.*|br-.*|cni.*|flannel.*"'
        linux = f'(rate(node_network_receive_bytes_total{{{self._m(rule, devf)}}}[5m]) * 8 / 1e6)'
        linux = f"(sum by ({inst}, device) ({linux}))"

        # Windows RX Mbps per nic
        win = f'(rate(windows_net_bytes_received_total{{{self._m(rule)}}}[5m]) * 8 / 1e6)'
        win = f"(sum by ({inst}, nic) ({win}))"

        # WMI fallback
        wmi = f'(rate(wmi_net_bytes_received_total{{{self._m(rule)}}}[5m]) * 8 / 1e6)'
        wmi = f"(sum by ({inst}, nic) ({wmi}))"

        return f"{linux} or {win} or {wmi}"

    def _q_net_tx_mbps(self, rule) -> str:
        inst = self._instance_label()

        # Linux TX Mbps per device
        devf = 'device!~"lo|docker.*|veth.*|br-.*|cni.*|flannel.*"'
        linux = f'(rate(node_network_transmit_bytes_total{{{self._m(rule, devf)}}}[5m]) * 8 / 1e6)'
        linux = f"(sum by ({inst}, device) ({linux}))"

        # Windows TX Mbps per nic
        win = f'(rate(windows_net_bytes_sent_total{{{self._m(rule)}}}[5m]) * 8 / 1e6)'
        win = f"(sum by ({inst}, nic) ({win}))"

        # WMI fallback
        wmi = f'(rate(wmi_net_bytes_sent_total{{{self._m(rule)}}}[5m]) * 8 / 1e6)'
        wmi = f"(sum by ({inst}, nic) ({wmi}))"

        return f"{linux} or {win} or {wmi}"

    def _q_net_mbps(self, rule) -> str:
        inst = self._instance_label()

        # Linux: (rx + tx) Mbps per device, exclude virtual/noisy
        devf = 'device!~"lo|docker.*|veth.*|br-.*|cni.*|flannel.*"'
        linux = (
            f'((rate(node_network_receive_bytes_total{{{self._m(rule, devf)}}}[5m]) + '
            f'rate(node_network_transmit_bytes_total{{{self._m(rule, devf)}}}[5m])) * 8 / 1e6)'
        )
        linux = f"(sum by ({inst}, device) ({linux}))"

        # Windows: (recv + sent) Mbps per nic (new + wmi fallback)
        win = (
            f'((rate(windows_net_bytes_received_total{{{self._m(rule)}}}[5m]) + '
            f'rate(windows_net_bytes_sent_total{{{self._m(rule)}}}[5m])) * 8 / 1e6)'
        )
        win = f"(sum by ({inst}, nic) ({win}))"

        wmi = (
            f'((rate(wmi_net_bytes_received_total{{{self._m(rule)}}}[5m]) + '
            f'rate(wmi_net_bytes_sent_total{{{self._m(rule)}}}[5m])) * 8 / 1e6)'
        )
        wmi = f"(sum by ({inst}, nic) ({wmi}))"

        return f"{linux} or {win} or {wmi}"

    def _q_link_mbps(self, rule) -> str:
        inst = self._instance_label()

        # Linux: speed bytes/sec -> Mbps
        devf = 'device!~"lo|docker.*|veth.*|br-.*|cni.*|flannel.*"'
        linux = f'(node_network_speed_bytes{{{self._m(rule, devf)}}} * 8 / 1e6)'
        linux = f"(max by ({inst}, device) ({linux}))"

        # Windows: bandwidth bytes/sec -> Mbps (new + legacy fallbacks)
        win = f'(windows_net_current_bandwidth_bytes{{{self._m(rule)}}} * 8 / 1e6)'
        win = f"(max by ({inst}, nic) ({win}))"

        old = f'(windows_net_current_bandwidth{{{self._m(rule)}}} / 1e6)'
        old = f"(max by ({inst}, nic) ({old}))"

        wmi = f'(wmi_net_current_bandwidth_bytes{{{self._m(rule)}}} * 8 / 1e6)'
        wmi = f"(max by ({inst}, nic) ({wmi}))"

        return f"{linux} or {win} or {wmi} or {old}"

    # =========================================================
    # AlertRuleState helpers
    # =========================================================
    def _get_or_create_state(self, rule, key: str) -> AlertRuleState:
        state = AlertRuleState.query.filter_by(
            rule_id=rule.id,
            customer_id=rule.customer_id,
            target_value=key,
        ).first()

        if not state:
            state = AlertRuleState(
                rule_id=rule.id,
                customer_id=rule.customer_id,
                target_value=key,
                is_active=False,
                consecutive=0,
                extended_state={},
            )
            db.session.add(state)
            db.session.flush()
        return state

    def _choose_templates(self, rule, scope: str):
        """
        Map to your notifier templates.
        Adjust if your notifier uses different template keys.
        """
        name = (rule.name or "").lower()
        if "cpu" in name:
            return "server_cpu_high", "server_cpu_recovery"
        if "mem" in name or "memory" in name:
            return "server_mem_high", "server_mem_recovery"
        if "disk" in name:
            return "server_disk_high", "server_disk_recovery"
        if "net" in name or "network" in name or "bandwidth" in name:
            return "server_net_high", "server_net_recovery"
        return f"server_{scope}_alert", f"server_{scope}_recovery"

    def _process_target(self, rule, key: str, host: str, scope: str, metrics: dict, meta: dict):
        state = self._get_or_create_state(rule, key)

        matched = self.evaluate_logic(rule.logic_json, metrics)

        now = datetime.utcnow().astimezone()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # save last values for UI/debugging
        state.extended_state = state.extended_state or {}
        state.extended_state.update(
            {
                "last_metrics": metrics,
                "meta": meta,
                "last_seen": now_str,
            }
        )

        alert_tpl, recovery_tpl = self._choose_templates(rule, scope)
        threshold = rule.evaluation_count or 1

        if matched:
            state.consecutive += 1

            if state.consecutive >= threshold and not state.is_active:
                state.is_active = True
                state.last_triggered = now

                send_notification(
                    template=alert_tpl,
                    rule=rule,
                    hostname=host,
                    scope=scope,
                    target=key,
                    metrics=metrics,
                    meta=meta,
                    alert_time=now_str,
                )
        else:
            if state.is_active:
                send_notification(
                    template=recovery_tpl,
                    rule=rule,
                    hostname=host,
                    scope=scope,
                    target=key,
                    metrics=metrics,
                    meta=meta,
                    recovery_time=now_str,
                )

            state.is_active = False
            state.consecutive = 0
            state.last_recovered = now

    # =========================================================
    # Main entrypoint
    # =========================================================
    def execute(self, rule):
        # Guard: empty logic should NOT alert forever
        if not rule.logic_json or not (rule.logic_json.get("children") or []):
            print(f"[ServerHandler] rule={rule.id} has empty logic_json.children → skipping")
            return

        # Determine needed fields from logic_json tree
        needed_fields = set()

        def walk(node):
            if not isinstance(node, dict):
                return
            if "field" in node:
                needed_fields.add(node.get("field"))
                return
            for ch in node.get("children") or []:
                walk(ch)

        walk(rule.logic_json)

        need_cpu = "cpu_usage" in needed_fields
        need_mem = "mem_usage" in needed_fields
        need_disk = ("disk_usage" in needed_fields) or ("disk_free" in needed_fields)
        need_net = any(f in needed_fields for f in (
            "net_mbps",
            "net_util",
            "network_receive_mbps",
            "network_transmit_mbps",
        ))


        # Host-level base metrics
        base_by_host = {}  # host -> metrics dict

        # ---------------- CPU ----------------
        if need_cpu:
            for item in self.prom_query(self._q_cpu_usage(rule)):
                labels = item.get("metric", {})
                host = self._guess_host(labels)
                try:
                    val = float(item.get("value", [0, "nan"])[1])
                except Exception:
                    continue
                base_by_host.setdefault(host, {})["cpu_usage"] = val

        # ---------------- MEM ----------------
        if need_mem:
            for item in self.prom_query(self._q_mem_usage(rule)):
                labels = item.get("metric", {})
                host = self._guess_host(labels)
                try:
                    val = float(item.get("value", [0, "nan"])[1])
                except Exception:
                    continue
                base_by_host.setdefault(host, {})["mem_usage"] = val

        # ---------------- DISK (per mount/volume) ----------------
        disks = []
        if need_disk:
            usage_map = {}  # (host, disk) -> (val, labels)
            free_map = {}   # (host, disk) -> (val, labels)

            if "disk_usage" in needed_fields:
                for item in self.prom_query(self._q_disk_usage(rule)):
                    labels = item.get("metric", {})
                    host = self._guess_host(labels)
                    disk = self._get_disk_label(labels)
                    try:
                        val = float(item.get("value", [0, "nan"])[1])
                    except Exception:
                        continue
                    usage_map[(host, disk)] = (val, labels)

            if "disk_free" in needed_fields:
                for item in self.prom_query(self._q_disk_free(rule)):
                    labels = item.get("metric", {})
                    host = self._guess_host(labels)
                    disk = self._get_disk_label(labels)
                    try:
                        val = float(item.get("value", [0, "nan"])[1])
                    except Exception:
                        continue
                    free_map[(host, disk)] = (val, labels)

            all_keys = set(usage_map.keys()) | set(free_map.keys())
            for (host, disk) in all_keys:
                entry = {"host": host, "disk": disk, "meta": {}}
                if (host, disk) in usage_map:
                    entry["disk_usage"] = usage_map[(host, disk)][0]
                    entry["meta"].update(usage_map[(host, disk)][1])
                if (host, disk) in free_map:
                    entry["disk_free"] = free_map[(host, disk)][0]
                    entry["meta"].update(free_map[(host, disk)][1])
                disks.append(entry)

        # ---------------- NETWORK (per iface) ----------------
        ifaces = []
        if need_net:
            rx_map = {}    # (host, iface) -> (val, labels)
            tx_map = {}    # (host, iface) -> (val, labels)
            mbps_map = {}  # (host, iface) -> (val, labels)  (total)
            link_map = {}  # (host, iface) -> (val, labels)

            want_rx = "network_receive_mbps" in needed_fields
            want_tx = "network_transmit_mbps" in needed_fields
            want_total = ("net_mbps" in needed_fields) or ("net_util" in needed_fields)

            # RX
            if want_rx or want_total:
                for item in self.prom_query(self._q_net_rx_mbps(rule)):
                    labels = item.get("metric", {})
                    host = self._guess_host(labels)
                    iface = self._get_iface_label(labels)
                    try:
                        val = float(item.get("value", [0, "nan"])[1])
                    except Exception:
                        continue
                    rx_map[(host, iface)] = (val, labels)

            # TX
            if want_tx or want_total:
                for item in self.prom_query(self._q_net_tx_mbps(rule)):
                    labels = item.get("metric", {})
                    host = self._guess_host(labels)
                    iface = self._get_iface_label(labels)
                    try:
                        val = float(item.get("value", [0, "nan"])[1])
                    except Exception:
                        continue
                    tx_map[(host, iface)] = (val, labels)

            # Build total map if needed (RX+TX)
            if want_total:
                all_keys = set(rx_map.keys()) | set(tx_map.keys())
                for k in all_keys:
                    rx = rx_map.get(k, (0.0, {}))[0]
                    tx = tx_map.get(k, (0.0, {}))[0]
                    labels = {}
                    labels.update(rx_map.get(k, (0.0, {}))[1])
                    labels.update(tx_map.get(k, (0.0, {}))[1])
                    mbps_map[k] = (rx + tx, labels)

            # Link speed only if net_util needed
            if "net_util" in needed_fields:
                for item in self.prom_query(self._q_link_mbps(rule)):
                    labels = item.get("metric", {})
                    host = self._guess_host(labels)
                    iface = self._get_iface_label(labels)
                    try:
                        val = float(item.get("value", [0, "nan"])[1])
                    except Exception:
                        continue
                    link_map[(host, iface)] = (val, labels)

            # Combine into iface entries
            all_keys = set(rx_map.keys()) | set(tx_map.keys()) | set(mbps_map.keys()) | set(link_map.keys())
            for (host, iface) in all_keys:
                entry = {"host": host, "iface": iface, "meta": {}}

                rx = None
                tx = None
                total = None
                link = None

                if (host, iface) in rx_map:
                    rx = rx_map[(host, iface)][0]
                    entry["network_receive_mbps"] = rx
                    entry["meta"].update(rx_map[(host, iface)][1])

                if (host, iface) in tx_map:
                    tx = tx_map[(host, iface)][0]
                    entry["network_transmit_mbps"] = tx
                    entry["meta"].update(tx_map[(host, iface)][1])

                if (host, iface) in mbps_map:
                    total = mbps_map[(host, iface)][0]
                    entry["net_mbps"] = total
                    entry["meta"].update(mbps_map[(host, iface)][1])

                if (host, iface) in link_map:
                    link = link_map[(host, iface)][0]
                    entry["meta"].update(link_map[(host, iface)][1])

                if "net_util" in needed_fields and total is not None and link is not None and link > 0:
                    entry["net_util"] = (total / link) * 100.0

                ifaces.append(entry)

        # =====================================================
        # Evaluate targets by granularity:
        #   - disk fields present -> per disk target
        #   - else net fields present -> per iface target
        #   - else -> per host target
        # =====================================================
        if need_disk:
            for d in disks:
                host = d["host"]
                disk = d["disk"]
                key = f"{host}|disk|{disk}"

                metrics = {}
                metrics.update(base_by_host.get(host, {}))
                if "disk_usage" in d:
                    metrics["disk_usage"] = d["disk_usage"]
                if "disk_free" in d:
                    metrics["disk_free"] = d["disk_free"]

                meta = {"disk": disk}
                self._process_target(rule, key, host, "disk", metrics, meta)

        elif need_net:
            for n in ifaces:
                host = n["host"]
                iface = n["iface"]
                key = f"{host}|net|{iface}"

                metrics = {}
                metrics.update(base_by_host.get(host, {}))

                # ✅ include total traffic fields (existing)
                if "net_mbps" in n:
                    metrics["net_mbps"] = n["net_mbps"]
                if "net_util" in n:
                    metrics["net_util"] = n["net_util"]

                # ✅ include RX/TX fields (NEW rules)
                if "network_receive_mbps" in n:
                    metrics["network_receive_mbps"] = n["network_receive_mbps"]
                if "network_transmit_mbps" in n:
                    metrics["network_transmit_mbps"] = n["network_transmit_mbps"]

                meta = {"iface": iface}
                self._process_target(rule, key, host, "net", metrics, meta)

        else:
            # Host-level (CPU/MEM only)
            for host, metrics in base_by_host.items():
                key = host
                self._process_target(rule, key, host, "host", metrics, {})

