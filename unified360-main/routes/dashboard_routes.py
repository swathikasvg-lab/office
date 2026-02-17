# routes/dashboard_routes.py
import time
from math import floor
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, render_template, jsonify, session, redirect,
    url_for, current_app, request
)
from functools import wraps
from sqlalchemy import or_, and_, func
from extensions import db

# Models
from models.device_status_alert import DeviceStatusAlert
from models.alert_rule_state import AlertRuleState
from models.idrac import IdracConfig
from models.proxy import ProxyServer
from models.ping import PingConfig
from models.port_monitor import PortMonitor
from models.url_monitor import UrlMonitor
from models.snmp import SnmpConfig
from models.link_monitor import LinkMonitor
from models.alert_rule import AlertRule

# Desktop helpers (existing)
from routes.desktop_routes import get_db_conn as desktop_get_conn, read_cache_all as desktop_read_cache

from services.http_utils import get_json_with_retry

# Security helpers (your central security module)
import security
from models.customer import Customer

dashboard_bp = Blueprint("dashboard", __name__)
IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------
# Helpers: App config access
# ---------------------------
def get_prometheus_url():
    return current_app.config.get("PROMETHEUS_URL", "http://localhost:9090")


def get_stale_threshold():
    return current_app.config.get("STALE_THRESHOLD", 600)


# ---------------------------
# Auth decorators (wrap to use security)
# ---------------------------
login_required_page = security.login_required_page
login_required_api = security.login_required_api


# ---------------------------
# Tenant helper utilities
# ---------------------------
def _user_allowed_customer():
    user = security.get_current_user()
    return security.get_allowed_customer_id(user)  # returns None for unrestricted admin


def _customer_name_for_allowed(allowed_cid):
    if allowed_cid is None:
        return None
    c = Customer.query.get(allowed_cid)
    return c.name if c else None


def _model_scoped_count(model_cls, allowed_cid):
    """
    Return count of rows in model_cls, filtered by customer_id if the model has that column
    """
    try:
        q = model_cls.query
        if allowed_cid is not None and hasattr(model_cls, "customer_id"):
            q = q.filter(getattr(model_cls, "customer_id") == allowed_cid)
        return q.count()
    except Exception:
        current_app.logger.exception("scoped_count failed for %s", model_cls.__name__)
        return 0


# ---------------------------
# Prometheus helpers (scoped)
# ---------------------------
def _fetch_prometheus_series(metrics, start, end, params_extra=None):
    """
    Helper to fetch /api/v1/series with repeated match[] metrics.
    Returns parsed JSON 'data' list or [].
    """
    prom_url = get_prometheus_url()
    url = f"{prom_url}/api/v1/series"
    params = []
    for m in metrics:
        params.append(("match[]", m))
    params.append(("start", start))
    params.append(("end", end))
    if params_extra:
        for k, v in params_extra.items():
            params.append((k, v))
    j = get_json_with_retry(url, params=params, timeout=15, retries=2)
    if j.get("status") != "success":
        return []
    return j.get("data", [])


def get_server_counts(allowed_customer_name=None):
    """
    Returns (total, active) numbers. If allowed_customer_name is provided,
    only instances with CustomerName-like label matching that name are considered.
    """
    try:
        END = int(time.time())
        START = END - (7 * 24 * 3600)

        metrics = [
            "node_uname_info",
            "node_exporter_build_info",
            "windows_os_info",
            "windows_exporter_build_info",
            "node_time_seconds",
        ]

        # fetch series
        series = _fetch_prometheus_series(metrics, START, END)

        all_instances = set()
        # If a tenant is set, we'll only include instances that report CustomerName matching allowed_customer_name
        for s in series:
            inst = s.get("instance")
            if not inst:
                continue
            # labels: attempt multiple possible CustomerName keys
            cust = s.get("CustomerName") or s.get("customerName") or s.get("customer") or None
            if allowed_customer_name is not None:
                if not cust:
                    # skip metrics without customer label when tenant scoping
                    continue
                # compare case-insensitive
                if str(cust).strip().lower() != str(allowed_customer_name).strip().lower():
                    continue
            all_instances.add(inst)

        total = len(all_instances)

        # Now get latest timestamp per instance (PromQL)
        prom_url = get_prometheus_url()
        ts_parts = [f"timestamp({m})" for m in metrics]
        ts_query = "max by (instance) (" + " or ".join(ts_parts) + ")"
        qjson = get_json_with_retry(
            f"{prom_url}/api/v1/query",
            params={"query": ts_query},
            timeout=15,
            retries=2,
        )

        ts_map = {}
        if qjson.get("status") == "success":
            for item in qjson.get("data", {}).get("result", []):
                inst = item["metric"].get("instance")
                try:
                    last_ts = float(item["value"][1])
                except Exception:
                    last_ts = 0.0
                if inst:
                    # If tenant scoping, ensure this instance is in our selected set
                    if allowed_customer_name is not None and inst not in all_instances:
                        continue
                    ts_map[inst] = last_ts

        stale_threshold = get_stale_threshold()
        active = 0
        nowf = time.time()
        for inst in all_instances:
            last_ts = ts_map.get(inst, 0.0)
            if (nowf - last_ts) <= stale_threshold:
                active += 1

        return total, active
    except Exception:
        current_app.logger.exception("Prometheus get_server_counts failed")
        return 0, 0


def get_servers_by_customer(allowed_customer_name=None):
    """
    Returns (servers_by_customer, server_device_map).
    If allowed_customer_name is set, returns only that customer's entry (name==allowed_customer_name).
    """
    import math
    from datetime import timezone

    metrics = [
        "node_uname_info",
        "node_exporter_build_info",
        "windows_os_info",
        "windows_exporter_build_info",
        "node_time_seconds",
    ]
    servers_by_customer = {}
    server_device_map = {}

    try:
        now_ts = int(time.time())
        series = _fetch_prometheus_series(metrics, now_ts - 7 * 24 * 3600, now_ts)

        for metric in series:
            inst = metric.get("instance")
            if not inst:
                continue

            cust = metric.get("CustomerName") or metric.get("customerName") or metric.get("customer") or "Backend"
            if allowed_customer_name is not None:
                if str(cust).strip().lower() != str(allowed_customer_name).strip().lower():
                    continue

            server_device_map[inst] = cust
            if ":" in inst:
                host_only = inst.split(":")[0]
                server_device_map.setdefault(host_only, cust)

            servers_by_customer.setdefault(cust, {"instances": set(), "active": 0, "total": 0, "down_instances": []})
            servers_by_customer[cust]["instances"].add(inst)

        # fetch timestamp map
        prom_url = get_prometheus_url()
        ts_parts = [f"timestamp({m})" for m in metrics]
        ts_query = "max by (instance) (" + " or ".join(ts_parts) + ")"


        qjson = get_json_with_retry(
            f"{prom_url}/api/v1/query",
            params={"query": ts_query},
            timeout=15,
            retries=2,
        )

        ts_map = {}
        if qjson.get("status") == "success":
            for item in qjson.get("data", {}).get("result", []):
                inst = item.get("metric", {}).get("instance")
                if not inst:
                    continue
                if allowed_customer_name is not None and inst not in server_device_map:
                    continue

                raw = None
                try:
                    raw = item["value"][1]
                except Exception:
                    raw = None

                try:
                    last_ts = float(raw)
                except Exception:
                    last_ts = None

                # Keep only valid finite timestamps
                if last_ts is not None and math.isfinite(last_ts) and last_ts > 0:
                    ts_map[inst] = last_ts

        stale_threshold = float(get_stale_threshold() or 0)
        now_f = time.time()

        for cust, info in servers_by_customer.items():
            total = len(info["instances"])
            active = 0
            down_instances = []

            for inst in info["instances"]:
                last_ts = ts_map.get(inst)  # None if missing

                if last_ts is None:
                    # UNKNOWN: Prometheus gave no timestamp; do NOT lie with "now" or epoch.
                    down_instances.append({
                        "instance": inst,
                        "last_seen": None,
                        "down_since": None,
                        "note": "no_prometheus_timestamp"
                    })
                    continue

                # last_ts is real epoch seconds
                if (now_f - last_ts) <= stale_threshold:
                    active += 1
                else:
                    last_seen_dt_utc = datetime.fromtimestamp(last_ts, tz=timezone.utc)
                    down_detected_dt_utc = datetime.fromtimestamp(last_ts + stale_threshold, tz=timezone.utc)
                    down_instances.append({
                        "instance": inst,
                        "last_seen": last_seen_dt_utc.isoformat().replace("+00:00", "Z"),
                        "down_since": down_detected_dt_utc.isoformat().replace("+00:00", "Z"),
                    })

            info["total"] = total
            info["active"] = active
            info["down_instances"] = down_instances

        if allowed_customer_name is not None:
            single = {}
            if allowed_customer_name in servers_by_customer:
                single[allowed_customer_name] = servers_by_customer[allowed_customer_name]
            return single, server_device_map

        return servers_by_customer, server_device_map

    except Exception:
        current_app.logger.exception("get_servers_by_customer failed")
        return {}, {}


# ---------------------------
# Desktops helpers (scoped)
# ---------------------------
def get_desktops_by_customer(allowed_customer_name=None):
    desktops_by_customer = {}
    desktop_device_map = {}
    try:
        conn = desktop_get_conn()
        desktops = desktop_read_cache(conn)  # dict: hostname -> info
        conn.close()
        for host, info in desktops.items():
            cust = info.get("customer_name") or info.get("customer") or info.get("CustomerName") or "Backend"
            if allowed_customer_name is not None:
                if str(cust).strip().lower() != str(allowed_customer_name).strip().lower():
                    continue
            desktop_device_map[host] = cust
            desktops_by_customer.setdefault(cust, {"hosts": [], "total": 0, "active": 0})
            desktops_by_customer[cust]["hosts"].append(host)
            desktops_by_customer[cust]["total"] += 1
            if info.get("status") and info.get("status").upper() == "UP":
                desktops_by_customer[cust]["active"] += 1
        if allowed_customer_name is not None:
            single = {}
            if allowed_customer_name in desktops_by_customer:
                single[allowed_customer_name] = desktops_by_customer[allowed_customer_name]
            return single, desktop_device_map
        return desktops_by_customer, desktop_device_map
    except Exception:
        current_app.logger.exception("get_desktops_by_customer failed")
        return {}, {}


def build_device_customer_maps(allowed_customer_name=None):
    servers_map, server_dev_map = get_servers_by_customer(allowed_customer_name)
    desktops_map, desktop_dev_map = get_desktops_by_customer(allowed_customer_name)
    device_map = {}
    device_map.update(server_dev_map or {})
    device_map.update(desktop_dev_map or {})
    return device_map, servers_map, desktops_map, server_dev_map, desktop_dev_map


# ---------------------------
# UI routes
# ---------------------------
@dashboard_bp.route("/dashboard2")
@login_required_page
def dashboard_enterprise():
    return render_template("dashboard2.html")


@dashboard_bp.route("/api/dashboard2/settings")
@login_required_api
def api_dashboard2_settings():
    try:
        interval = int(current_app.config.get("DASHBOARD_REFRESH_INTERVAL", 30))
        default_limit = int(request.args.get("default_limit", current_app.config.get("DASHBOARD_ALERT_LIMIT", 50)))
        return jsonify({"refresh_interval": interval, "default_limit": default_limit, "ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------
# KPI Summary (tenant-scoped)
# ---------------------------
# ---------------------------
# KPI Summary (tenant-scoped)
# ---------------------------
@dashboard_bp.route("/api/dashboard2/kpi-summary")
@login_required_api
def api_kpi_summary():
    try:
        from datetime import datetime, timedelta
        from sqlalchemy import func

        allowed_cid = _user_allowed_customer()
        allowed_cust_name = _customer_name_for_allowed(allowed_cid)

        # -----------------------
        # Servers (Prometheus)
        # -----------------------
        total_servers, active_servers = get_server_counts(allowed_customer_name=allowed_cust_name)
        down_servers = max(0, int(total_servers) - int(active_servers))

        # If we have per-instance downs available, prefer that for "critical count"
        server_down_instances = set()
        try:
            servers_map, _ = get_servers_by_customer(allowed_customer_name=allowed_cust_name)
            for cust, info in (servers_map or {}).items():
                if allowed_cust_name is not None and str(cust).strip().lower() != str(allowed_cust_name).strip().lower():
                    continue
                for item in (info.get("down_instances") or []):
                    if isinstance(item, dict):
                        inst = item.get("instance")
                        if inst:
                            server_down_instances.add(str(inst))
                    elif isinstance(item, str):
                        server_down_instances.add(item)
        except Exception:
            current_app.logger.exception("kpi-summary: server down_instances build failed")

        # -----------------------
        # Desktops (cache)
        # -----------------------
        total_desktops = 0
        active_desktops = 0
        try:
            desktops_map, _ = get_desktops_by_customer(allowed_customer_name=allowed_cust_name)
            if allowed_cust_name is not None:
                vals = list(desktops_map.values())
                total_desktops = int(vals[0].get("total", 0)) if vals else 0
                active_desktops = int(vals[0].get("active", 0)) if vals else 0
            else:
                total_desktops = sum(int(v.get("total", 0)) for v in (desktops_map or {}).values())
                active_desktops = sum(int(v.get("active", 0)) for v in (desktops_map or {}).values())
        except Exception:
            current_app.logger.exception("kpi-summary: desktop counts failed")
            total_desktops, active_desktops = 0, 0

        # -----------------------
        # Proxy (DB)
        # -----------------------
        total_proxy = 0
        active_proxy = 0
        try:
            q = ProxyServer.query
            if allowed_cid is not None and hasattr(ProxyServer, "customer_id"):
                q = q.filter(ProxyServer.customer_id == allowed_cid)
            total_proxy = q.count()
            active_proxy = q.filter(ProxyServer.last_heartbeat != None).count()
        except Exception:
            current_app.logger.exception("kpi-summary: proxy counts failed")
            total_proxy, active_proxy = 0, 0

        # -----------------------
        # Monitor totals (DB)
        # -----------------------
        total_ping = _model_scoped_count(PingConfig, allowed_cid)
        total_port = _model_scoped_count(PortMonitor, allowed_cid)
        total_url  = _model_scoped_count(UrlMonitor, allowed_cid)
        total_snmp = _model_scoped_count(SnmpConfig, allowed_cid)
        total_idrac = _model_scoped_count(IdracConfig, allowed_cid)
        total_link = _model_scoped_count(LinkMonitor, allowed_cid)

        # ----------------------------------------------------
        # ACTIVE critical targets per type:
        # union(DeviceStatusAlert DOWN) + (AlertRuleState active)
        # This fixes your "Port 17/17 but 2 rule-triggered downs".
        # ----------------------------------------------------
        def active_targets_for_type(mtype: str, device_source_like: str):
            targets = set()

            # A) DeviceStatusAlert (DOWN + active)
            try:
                dq = DeviceStatusAlert.query.filter(
                    DeviceStatusAlert.is_active.is_(True),
                    DeviceStatusAlert.last_status == "DOWN"
                )
                if allowed_cid is not None and hasattr(DeviceStatusAlert, "customer_id"):
                    dq = dq.filter(DeviceStatusAlert.customer_id == allowed_cid)
                if device_source_like:
                    dq = dq.filter(DeviceStatusAlert.source.ilike(f"%{device_source_like}%"))

                rows = dq.with_entities(DeviceStatusAlert.device).distinct().all()
                for (dev,) in rows:
                    if dev:
                        targets.add(str(dev))
            except Exception:
                current_app.logger.exception("kpi-summary: DeviceStatusAlert union failed for %s", mtype)

            # B) AlertRuleState (active rules) for this monitoring_type
            try:
                rq = (
                    db.session.query(AlertRuleState)
                    .join(AlertRule, AlertRule.id == AlertRuleState.rule_id)
                    .filter(AlertRuleState.is_active.is_(True))
                    .filter(AlertRule.monitoring_type == mtype)
                )
                if allowed_cid is not None:
                    rq = rq.filter(AlertRule.customer_id == allowed_cid)

                for r in rq.all():
                    key = getattr(r, "target_value", None)
                    if not key:
                        ext = getattr(r, "extended_state", None) or {}
                        if isinstance(ext, dict):
                            key = ext.get("instance") or ext.get("device") or ext.get("host")
                    if not key:
                        key = f"rule_state_{r.id}"
                    targets.add(str(key))
            except Exception:
                current_app.logger.exception("kpi-summary: AlertRuleState union failed for %s", mtype)

            return targets

        down_ping_set = active_targets_for_type("ping", "ping")
        down_port_set = active_targets_for_type("port", "port")
        down_url_set  = active_targets_for_type("url",  "url")
        down_snmp_set = active_targets_for_type("snmp", "snmp")
        down_idrac_set = active_targets_for_type("idrac", "idrac")
        down_link_set = active_targets_for_type("link", "link")

        active_ping = max(0, int(total_ping) - len(down_ping_set))
        active_port = max(0, int(total_port) - len(down_port_set))   # ✅ will become 15/17
        active_url  = max(0, int(total_url)  - len(down_url_set))
        active_snmp = max(0, int(total_snmp) - len(down_snmp_set))
        active_idrac = max(0, int(total_idrac) - len(down_idrac_set))
        active_link = max(0, int(total_link) - len(down_link_set))

        # ----------------------------------------------------
        # Active rules count (use unique targets, not raw rows)
        # ----------------------------------------------------
        active_rule_targets = set()
        try:
            rq_all = (
                db.session.query(AlertRuleState)
                .join(AlertRule, AlertRule.id == AlertRuleState.rule_id)
                .filter(AlertRuleState.is_active.is_(True))
            )
            if allowed_cid is not None:
                rq_all = rq_all.filter(AlertRule.customer_id == allowed_cid)

            for r in rq_all.all():
                key = getattr(r, "target_value", None)
                if not key:
                    ext = getattr(r, "extended_state", None) or {}
                    if isinstance(ext, dict):
                        key = ext.get("instance") or ext.get("device") or ext.get("host")
                if not key:
                    key = f"rule_state_{r.id}"
                active_rule_targets.add(str(key))
        except Exception:
            current_app.logger.exception("kpi-summary: active_rule_targets calc failed")

        # ----------------------------------------------------
        # Alerts (24h) — count ONLY "new ACTIVE" alerts in last 24h
        # This matches what you’re expecting (0 if nothing started recently).
        # ----------------------------------------------------
        cutoff = datetime.utcnow() - timedelta(hours=24)

        alerts_24h = 0
        try:
            # Active rules that triggered within last 24h
            new_rule_targets = set()
            rq_24 = (
                db.session.query(AlertRuleState)
                .join(AlertRule, AlertRule.id == AlertRuleState.rule_id)
                .filter(AlertRuleState.is_active.is_(True))
                .filter(AlertRuleState.last_triggered != None)
                .filter(AlertRuleState.last_triggered >= cutoff)
            )
            if allowed_cid is not None:
                rq_24 = rq_24.filter(AlertRule.customer_id == allowed_cid)

            for r in rq_24.all():
                key = getattr(r, "target_value", None)
                if not key:
                    ext = getattr(r, "extended_state", None) or {}
                    if isinstance(ext, dict):
                        key = ext.get("instance") or ext.get("device") or ext.get("host")
                if not key:
                    key = f"rule_state_{r.id}"
                new_rule_targets.add(str(key))

            # Active device downs that changed within last 24h
            new_dev_targets = set()
            dq_24 = DeviceStatusAlert.query.filter(
                DeviceStatusAlert.is_active.is_(True),
                DeviceStatusAlert.last_status == "DOWN",
                DeviceStatusAlert.last_change != None,
                DeviceStatusAlert.last_change >= cutoff,
            )
            if allowed_cid is not None and hasattr(DeviceStatusAlert, "customer_id"):
                dq_24 = dq_24.filter(DeviceStatusAlert.customer_id == allowed_cid)

            for (dev,) in dq_24.with_entities(DeviceStatusAlert.device).distinct().all():
                if dev:
                    new_dev_targets.add(str(dev))

            alerts_24h = len(new_rule_targets) + len(new_dev_targets)
        except Exception:
            current_app.logger.exception("kpi-summary: alerts_24h calc failed")
            alerts_24h = 0

        # ----------------------------------------------------
        # Totals + Health %
        # ----------------------------------------------------
        types = {
            "servers":  {"total": int(total_servers),  "active": int(active_servers)},
            "desktops": {"total": int(total_desktops), "active": int(active_desktops)},
            "proxy":    {"total": int(total_proxy),    "active": int(active_proxy)},
            "ping":     {"total": int(total_ping),     "active": int(active_ping)},
            "port":     {"total": int(total_port),     "active": int(active_port)},
            "url":      {"total": int(total_url),      "active": int(active_url)},
            "snmp":     {"total": int(total_snmp),     "active": int(active_snmp)},
            "idrac":    {"total": int(total_idrac),    "active": int(active_idrac)},
            "link":     {"total": int(total_link),     "active": int(active_link)},
        }

        total_monitors = sum(v["total"] for v in types.values())
        active_monitors = sum(v["active"] for v in types.values())
        health_percent = int((active_monitors / total_monitors) * 100) if total_monitors > 0 else 0

        # Active criticals = server downs + active rule targets (unique)
        # Prefer instance-level server down count if present.
        server_crit = len(server_down_instances) if server_down_instances else int(down_servers)
        critical_active = int(server_crit) + int(len(active_rule_targets))

        summary = {
            "total_monitors": int(total_monitors),
            "active_monitors": int(active_monitors),
            "health_percent": int(health_percent),
            "alerts_24h": int(alerts_24h),
            "critical_active": int(critical_active),
        }

        return jsonify({
            "ok": True,
            "summary": summary,
            "types": types,
            # flat keys (your JS often reads these directly)
            **summary
        })

    except Exception:
        current_app.logger.exception("kpi-summary fatal error")
        return jsonify({"ok": False, "error": "Internal error"}), 500


# ---------------------------
# category-status (tenant-scoped)
# ---------------------------
@dashboard_bp.route("/api/dashboard2/category-status")
@login_required_api
def api_category_status():
    try:
        allowed_cid = _user_allowed_customer()
        allowed_cust_name = _customer_name_for_allowed(allowed_cid)

        device_map, servers_map, desktops_map, server_dev_map, desktop_dev_map = build_device_customer_maps(allowed_cust_name)

        # server_customers: if tenant-scoped, will include only that tenant; else include all
        server_customers = {}
        for c, info in (servers_map or {}).items():
            total = info.get("total") if isinstance(info.get("total", 0), int) else len(info.get("instances", []))
            active = info.get("active", 0)
            down = max(0, total - active)
            server_customers[c] = {"Servers": {"active": active, "total": total, "down": down}, "health": int((active / total) * 100) if total > 0 else 100}

        desktop_customers = {}
        for c, info in (desktops_map or {}).items():
            total = info.get("total", 0)
            active = info.get("active", 0)
            down = max(0, total - active)
            desktop_customers[c] = {"Desktops": {"active": active, "total": total, "down": down}, "health": int((active / total) * 100) if total > 0 else 100}

        # Ensure 'Backend' exists for convenience (if not present)
        if "Backend" not in server_customers:
            server_customers["Backend"] = {"Servers": {"active": 0, "total": 0, "down": 0}, "health": 100}
        if "Backend" not in desktop_customers:
            desktop_customers["Backend"] = {"Desktops": {"active": 0, "total": 0, "down": 0}, "health": 100}

        merged_device_map = {}
        merged_device_map.update(server_dev_map or {})
        merged_device_map.update(desktop_dev_map or {})

        return jsonify({
            "ok": True,
            "server_customers": server_customers,
            "desktop_customers": desktop_customers,
            "device_map": merged_device_map
        })
    except Exception:
        current_app.logger.exception("category-status error")
        return jsonify({"ok": False, "error": "Internal error"}), 500


# ---------------------------
# recent-alerts (tenant-scoped)
# ---------------------------
@dashboard_bp.route("/api/dashboard2/recent-alerts")
@login_required_api
def api_recent_alerts():
    from datetime import timezone  # local import to be safe

    def iso_utc_or_none(val):
        """
        Convert datetime/iso/epoch to ISO Z string.
        If val is None/empty/invalid -> return None (do NOT return current time).
        """
        if val is None:
            return None

        # datetime
        if isinstance(val, datetime):
            dt = val
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")

        # numeric epoch
        if isinstance(val, (int, float)):
            if float(val) <= 0:
                return None
            dt = datetime.fromtimestamp(float(val), tz=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")

        # string: iso or epoch string
        s = str(val).strip()
        if not s:
            return None

        # epoch string
        try:
            num = float(s)
            if num <= 0:
                return None
            dt = datetime.fromtimestamp(num, tz=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except Exception:
            pass

        # iso string normalize
        if s.endswith("Z"):
            return s
        return s.replace("+00:00", "Z")

    def parse_iso_to_utc_dt(val):
        """For sorting only: return tz-aware UTC datetime from iso/epoch/None."""
        if val is None:
            return datetime.min.replace(tzinfo=timezone.utc)

        if isinstance(val, datetime):
            dt = val
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        if isinstance(val, (int, float)):
            if float(val) <= 0:
                return datetime.min.replace(tzinfo=timezone.utc)
            return datetime.fromtimestamp(float(val), tz=timezone.utc)

        s = str(val).strip()
        if not s:
            return datetime.min.replace(tzinfo=timezone.utc)

        # epoch string
        try:
            num = float(s)
            if num <= 0:
                return datetime.min.replace(tzinfo=timezone.utc)
            return datetime.fromtimestamp(num, tz=timezone.utc)
        except Exception:
            pass

        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    try:
        limit = int(request.args.get("limit", 50))
        allowed_cid = _user_allowed_customer()
        allowed_cust_name = _customer_name_for_allowed(allowed_cid)

        device_map, servers_map, desktops_map, server_dev_map, desktop_dev_map = build_device_customer_maps(allowed_cust_name)

        alerts = []

        # 0) SYNTHETIC: Server down alerts based on servers_map (Prometheus staleness-derived)
        try:
            for cust, info in (servers_map or {}).items():
                if allowed_cust_name is not None and str(cust).strip() != str(allowed_cust_name).strip():
                    continue

                total = info.get("total")
                if not isinstance(total, int):
                    total = len(info.get("instances", []) or [])
                active = info.get("active", 0) or 0
                down = max(0, int(total) - int(active))
                if down <= 0:
                    continue

                down_list = (
                    info.get("down_instances")
                    or info.get("down_devices")
                    or info.get("down_hosts")
                    or []
                )

                # Unknown exact instances or timestamps
                if not down_list:
                    alerts.append({
                        "severity": "CRITICAL",
                        "type": "Server Down",
                        "device": f"{down} servers down",
                        "source": "servers",
                        "customer": cust,
                        "ts": None,  # unknown (do not show current time)
                        "details": {"down_count": down, "reason": "stale_prometheus"}
                    })
                    continue

                for item in down_list:
                    if isinstance(item, dict):
                        inst = item.get("instance")
                        ts = item.get("down_since") or item.get("last_seen")  # may be None
                        alerts.append({
                            "severity": "CRITICAL",
                            "type": "Server Down",
                            "device": inst,
                            "source": "servers",
                            "customer": cust,
                            "ts": iso_utc_or_none(ts),  # <-- FIX: if None, stays None
                            "details": {
                                "reason": "stale_prometheus",
                                "instance": inst,
                                "last_seen": item.get("last_seen"),
                                "down_since": item.get("down_since"),
                                "note": item.get("note"),
                            }
                        })
                    else:
                        inst = str(item)
                        alerts.append({
                            "severity": "CRITICAL",
                            "type": "Server Down",
                            "device": inst,
                            "source": "servers",
                            "customer": cust,
                            "ts": None,
                            "details": {"reason": "stale_prometheus", "instance": inst}
                        })

        except Exception:
            current_app.logger.exception("recent-alerts: synthetic server-down build failed")

        # 1) DeviceStatusAlert: active DOWN alerts (use DB timestamps)
        try:
            q = DeviceStatusAlert.query.filter(DeviceStatusAlert.is_active == True).order_by(DeviceStatusAlert.updated_at.desc())
            if allowed_cid is not None and hasattr(DeviceStatusAlert, "customer_id"):
                q = q.filter(DeviceStatusAlert.customer_id == allowed_cid)
            q = q.limit(limit).all()

            for a in q:
                dev = a.device or ""
                src = (a.source or "").lower()

                if "server" in src or "windows" in src or "linux" in src:
                    cust = (
                        server_dev_map.get(dev)
                        or server_dev_map.get(dev.split(":")[0])
                        or desktop_dev_map.get(dev)
                        or "Backend"
                    )
                elif "desktop" in src or "workstation" in src:
                    cust = (
                        desktop_dev_map.get(dev)
                        or desktop_dev_map.get(dev.split(":")[0])
                        or server_dev_map.get(dev)
                        or "Backend"
                    )
                else:
                    cust = (
                        server_dev_map.get(dev)
                        or desktop_dev_map.get(dev)
                        or device_map.get(dev)
                        or device_map.get(dev.split(":")[0])
                        or "Backend"
                    )

                if allowed_cust_name is not None and str(cust).strip().lower() != str(allowed_cust_name).strip().lower():
                    continue

                ts_dt = a.last_change or a.updated_at or a.created_at
                alerts.append({
                    "severity": "CRITICAL",
                    "type": "Device Down",
                    "device": dev,
                    "source": a.source,
                    "customer": cust,
                    "ts": iso_utc_or_none(ts_dt),  # real time, or None if totally missing
                    "details": a.to_dict()
                })
        except Exception:
            current_app.logger.exception("recent-alerts: device alerts fetch failed")

        # 2) AlertRuleState: active rules (use DB customer, not maps)
        try:
            from sqlalchemy.orm import joinedload
        
            ars_q = (
                AlertRuleState.query
                .options(
                    joinedload(AlertRuleState.customer),   # avoid N+1
                    joinedload(AlertRuleState.rule)
                )
                .filter(AlertRuleState.is_active == True)
                .order_by(AlertRuleState.last_triggered.desc())
            )
        
            # ✅ tenant scope should be on AlertRuleState.customer_id (source of truth)
            if allowed_cid is not None:
                ars_q = ars_q.filter(AlertRuleState.customer_id == allowed_cid)
        
            rows = ars_q.limit(limit).all()
        
            for r in rows:
                ext = r.extended_state or {}
                inst = None
                if isinstance(ext, dict):
                    inst = ext.get("instance") or ext.get("device") or ext.get("host")
        
                dev = inst or r.target_value or f"rule_{r.rule_id}"
        
                # ✅ always use DB customer
                cobj = r.customer
                cust = (
                    getattr(cobj, "name", None)
                    or getattr(cobj, "customer_name", None)
                    or getattr(cobj, "cname", None)
                    or str(r.customer_id)
                )
        
                if allowed_cust_name is not None and str(cust).strip().lower() != str(allowed_cust_name).strip().lower():
                    continue
        
                ts_dt = r.last_triggered or r.updated_at or r.created_at
                alerts.append({
                    "severity": "CRITICAL",
                    "type": "Rule Triggered",
                    "device": dev,
                    "source": (r.rule.monitoring_type if r.rule else "rule"),
                    "customer": cust,
                    "customer_id": r.customer_id,   # helpful for debugging/UI
                    "ts": iso_utc_or_none(ts_dt),
                    "details": {
                        "rule_id": r.rule_id,
                        "monitoring_type": (r.rule.monitoring_type if r.rule else None),
                        "extended_state": r.extended_state
                    }
                })
        
        except Exception:
            current_app.logger.exception("recent-alerts: rules fetch failed")


        # sort by ts and trim
        alerts = sorted(alerts, key=lambda x: parse_iso_to_utc_dt(x.get("ts")), reverse=True)[:limit]
        return jsonify(alerts)

    except Exception:
        current_app.logger.exception("recent-alerts error")
        return jsonify({"ok": False, "error": "Internal error"}), 500


# ---------------------------
# heatmap (tenant-scoped)
# ---------------------------
@dashboard_bp.route("/api/dashboard2/heatmap")
@login_required_api
def api_heatmap():
    try:
        allowed_cid = _user_allowed_customer()
        allowed_cust_name = _customer_name_for_allowed(allowed_cid)

        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        now_ist = now_utc.astimezone(IST)

        slots = []
        for i in range(48):
            t = now_ist - timedelta(minutes=30 * (47 - i))
            slots.append(t)

        categories = ["Servers", "Desktops", "Ping", "Port", "URL", "SNMP", "iDRAC", "Link", "Proxy"]
        matrix = [[0 for _ in range(len(slots))] for _ in categories]

        since_utc = now_utc - timedelta(hours=24)
        q = DeviceStatusAlert.query.filter(DeviceStatusAlert.updated_at >= since_utc)
        if allowed_cid is not None and hasattr(DeviceStatusAlert, "customer_id"):
            q = q.filter(DeviceStatusAlert.customer_id == allowed_cid)
        device_alerts = q.all()

        # Build mapping for quick classification
        device_map, servers_map, desktops_map, server_dev_map, desktop_dev_map = build_device_customer_maps(allowed_cust_name)

        for a in device_alerts:
            tstamp = a.last_change or a.updated_at or a.created_at
            if not tstamp:
                continue
            if tstamp.tzinfo is None:
                tstamp = tstamp.replace(tzinfo=timezone.utc)
            tstamp = tstamp.astimezone(IST)

            for si, sstart in enumerate(slots):
                s_end = sstart + timedelta(minutes=30)
                if sstart <= tstamp < s_end:
                    src = (a.source or "").lower()
                    if "server" in src:
                        ridx = categories.index("Servers")
                    elif "desktop" in src:
                        ridx = categories.index("Desktops")
                    elif "ping" in src:
                        ridx = categories.index("Ping")
                    elif "port" in src:
                        ridx = categories.index("Port")
                    elif "url" in src or (isinstance(a.device, str) and a.device.startswith(("http://", "https://"))):
                        ridx = categories.index("URL")
                    elif "snmp" in src:
                        ridx = categories.index("SNMP")
                    elif "idrac" in src:
                        ridx = categories.index("iDRAC")
                    elif "link" in src:
                        ridx = categories.index("Link")
                    elif "proxy" in src:
                        ridx = categories.index("Proxy")
                    else:
                        ridx = categories.index("Servers")

                    if a.is_active and a.last_status == "DOWN":
                        matrix[ridx][si] = 2
                    break

        labels = [s.strftime("%H:%M") for s in slots]

        return jsonify({
            "ok": True,
            "categories": categories,
            "timestamps": labels,
            "matrix": matrix,
            "timezone": "IST",
            "slot_minutes": 30
        })
    except Exception:
        current_app.logger.exception("heatmap error")
        return jsonify({"ok": False, "error": "Internal error"}), 500


# ---------------------------
# legacy dashboard and summary endpoints (kept but tenant-scoped)
# ---------------------------
@dashboard_bp.route("/dashboard")
@login_required_page
def dashboard_home():
    allowed_cid = _user_allowed_customer()
    allowed_cust_name = _customer_name_for_allowed(allowed_cid)

    active_proxy_q = ProxyServer.query
    total_proxy_q = ProxyServer.query
    if allowed_cid is not None and hasattr(ProxyServer, "customer_id"):
        active_proxy_q = active_proxy_q.filter(ProxyServer.customer_id == allowed_cid)
        total_proxy_q = total_proxy_q.filter(ProxyServer.customer_id == allowed_cid)

    active_proxy = active_proxy_q.filter(ProxyServer.last_heartbeat != None).count()
    total_proxy = total_proxy_q.count()

    ping_q = PingConfig.query
    port_q = PortMonitor.query
    url_q = UrlMonitor.query
    snmp_q = SnmpConfig.query

    if allowed_cid is not None:
        for q in (lambda model: model.query):
            pass  # no-op placeholder (we filter per-model below)

    if allowed_cid is not None:
        if hasattr(PingConfig, "customer_id"):
            ping_q = ping_q.filter(PingConfig.customer_id == allowed_cid)
        if hasattr(PortMonitor, "customer_id"):
            port_q = port_q.filter(PortMonitor.customer_id == allowed_cid)
        if hasattr(UrlMonitor, "customer_id"):
            url_q = url_q.filter(UrlMonitor.customer_id == allowed_cid)
        if hasattr(SnmpConfig, "customer_id"):
            snmp_q = snmp_q.filter(SnmpConfig.customer_id == allowed_cid)

    ping_count = ping_q.count()
    port_count = port_q.count()
    url_count = url_q.count()
    snmp_count = snmp_q.count()

    total_servers, active_servers = get_server_counts(allowed_customer_name=allowed_cust_name)

    conn = desktop_get_conn()
    desktops = desktop_read_cache(conn)
    conn.close()

    # if tenant-scoped, filter desktop cache by allowed_cust_name
    if allowed_cust_name is not None:
        filtered = {h: info for h, info in desktops.items() if (info.get("customer_name") or info.get("customer") or info.get("CustomerName") or "Backend") and (str(info.get("customer_name") or info.get("customer") or info.get("CustomerName") or "Backend").strip().lower() == allowed_cust_name.strip().lower())}
    else:
        filtered = desktops

    total_desktops = len(filtered)
    active_desktops = len([d for d in filtered.values() if d.get("status") == "UP"])

    summary = {
        "active_proxies": active_proxy,
        "proxy": total_proxy,
        "active_servers": active_servers,
        "servers": total_servers,
        "active_desktops": active_desktops,
        "desktops": total_desktops,
        "ping": ping_count,
        "port": port_count,
        "url": url_count,
        "snmp": snmp_count,
    }

    last_updated = datetime.now().strftime("%I:%M:%S %p")
    return render_template("dashboard.html", summary=summary, last_updated=last_updated)


@dashboard_bp.route("/api/dashboard-summary")
@login_required_api
def dashboard_summary_api():
    try:
        allowed_cid = _user_allowed_customer()
        allowed_cust_name = _customer_name_for_allowed(allowed_cid)

        active_proxy_q = ProxyServer.query
        total_proxy_q = ProxyServer.query
        if allowed_cid is not None and hasattr(ProxyServer, "customer_id"):
            active_proxy_q = active_proxy_q.filter(ProxyServer.customer_id == allowed_cid)
            total_proxy_q = total_proxy_q.filter(ProxyServer.customer_id == allowed_cid)

        active_proxy = active_proxy_q.filter(ProxyServer.last_heartbeat != None).count()
        total_proxy = total_proxy_q.count()

        ping_q = PingConfig.query
        port_q = PortMonitor.query
        url_q = UrlMonitor.query
        snmp_q = SnmpConfig.query

        if allowed_cid is not None:
            if hasattr(PingConfig, "customer_id"):
                ping_q = ping_q.filter(PingConfig.customer_id == allowed_cid)
            if hasattr(PortMonitor, "customer_id"):
                port_q = port_q.filter(PortMonitor.customer_id == allowed_cid)
            if hasattr(UrlMonitor, "customer_id"):
                url_q = url_q.filter(UrlMonitor.customer_id == allowed_cid)
            if hasattr(SnmpConfig, "customer_id"):
                snmp_q = snmp_q.filter(SnmpConfig.customer_id == allowed_cid)

        ping_count = ping_q.count()
        port_count = port_q.count()
        url_count = url_q.count()
        snmp_count = snmp_q.count()

        total_servers, active_servers = get_server_counts(allowed_customer_name=allowed_cust_name)

        return jsonify({
            "ok": True,
            "active_proxies": active_proxy,
            "proxy": total_proxy,
            "active_servers": active_servers,
            "servers": total_servers,
            "ping": ping_count,
            "port": port_count,
            "url": url_count,
            "snmp": snmp_count
        })
    except Exception:
        current_app.logger.exception("dashboard-summary error")
        return jsonify({"ok": False, "error": "Internal error"}), 500
