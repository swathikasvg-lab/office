from flask import Blueprint, jsonify, current_app as app
from datetime import datetime, timezone
import requests, time

from security import (
    login_required_api,
    get_current_user,
    get_allowed_customer_id,
)

monitor_status = Blueprint("monitor_status", __name__)

# Config
PROMETHEUS_URL = "http://localhost:9090"
INFLUX_URL = "http://localhost:8086"
INFLUX_DB = "autointelli"
PROM_TIMEOUT = 8
INFLUX_TIMEOUT = 8
STALE_THRESHOLD = 600  # 10 minutes


# -----------------------------------------------------------
#  Helper utilities
# -----------------------------------------------------------
def influx_query(q):
    resp = requests.get(
        f"{INFLUX_URL}/query",
        params={"db": INFLUX_DB, "q": q},
        timeout=INFLUX_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def columns_to_lastrow_map(series):
    cols = series.get("columns", [])
    vals = series.get("values") or []
    if not vals:
        return {}
    last = vals[-1]
    return {cols[i]: last[i] for i in range(len(cols))}


def parse_influx_time(series):
    if not series.get("values"):
        return None
    try:
        t_raw = series["values"][-1][0]
        if isinstance(t_raw, (int, float)):
            t_raw = datetime.fromtimestamp(t_raw / 1e9, tz=timezone.utc)
        else:
            t_raw = datetime.fromisoformat(str(t_raw).replace("Z", "+00:00"))
        return t_raw.astimezone()
    except Exception:
        return None


# -----------------------------------------------------------
#  Generic customer resolution for InfluxDB / Prometheus tags
# -----------------------------------------------------------
def resolve_customer_from_tags(tags):
    if not tags:
        return None

    keys = [
        "CustomerName",
        "customer_name",
        "customer",
        "location",       # sometimes reused as customer
        "region",         # optional
    ]

    for k in keys:
        v = tags.get(k)
        if v:
            return str(v).strip()

    return None


def filter_for_tenant(user, items):
    """
    Admin → return all items.
    Tenant → only items that match their customer_id OR
             items where customer cannot be determined will be hidden.
    """
    allowed = get_allowed_customer_id(user)
    if allowed is None:
        return items  # Admin unrestricted

    tenant_customer = user.customer.name if user.customer else None

    out = []
    for it in items:
        cust = it.get("customer")
        if cust and cust == tenant_customer:
            out.append(it)

    return out


# -----------------------------------------------------------
# API
# -----------------------------------------------------------
@monitor_status.route("/api/monitor-status")
@login_required_api
def monitor_status_api():
    user = get_current_user()
    items = []
    now_ts = time.time()

    # ---------------------------------------------------------
    # 1) Server Monitor (Prometheus / Alloy)
    # ---------------------------------------------------------
    try:
        q = 'timestamp(node_os_info)'
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": q},
            timeout=PROM_TIMEOUT,
        ).json()

        if r.get("status") == "success":
            for res in r.get("data", {}).get("result", []):
                metric = res.get("metric", {}) or {}

                node_name = metric.get("nodename") or metric.get("instance") or "Unknown"
                instance = metric.get("instance", "")
                ip = instance.split(":")[0] if ":" in instance else instance

                ts = float(res.get("value", [0, 0])[0]) or None
                if not ts:
                    continue

                age = now_ts - ts
                status = "UP" if age <= STALE_THRESHOLD else "DOWN"
                since = (
                    datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
                    if status == "DOWN"
                    else None
                )

                customer = resolve_customer_from_tags(metric)

                items.append({
                    "type": "Server Monitor",
                    "target": node_name,
                    "ip_or_url": ip if ip != node_name else "—",
                    "status": status,
                    "since": since.strftime("%d %b %Y, %I:%M %p") if since else "—",
                    "source": "Grafana Alloy",
                    "customer": customer,
                })
    except Exception as e:
        app.logger.warning("Prometheus query failed: %s", e)

    # ---------------------------------------------------------
    # 2) Ping Monitors (InfluxDB)
    # ---------------------------------------------------------
    try:
        q = '''
        SELECT LAST("percent_packet_loss") AS loss
        FROM "ping"
        GROUP BY "friendly_name","url","location","monitoring_server"
        '''
        res = influx_query(q)
        series_list = res.get("results", [{}])[0].get("series") or []

        for s in series_list:
            tags = s.get("tags", {}) or {}
            colmap = columns_to_lastrow_map(s)
            last_time = parse_influx_time(s)

            target = tags.get("friendly_name") or tags.get("url") or "Unknown"
            ip_or_url = tags.get("url") or "—"
            customer = resolve_customer_from_tags(tags)

            loss = colmap.get("loss")
            try:
                loss = float(loss)
            except Exception:
                loss = None

            status = "DOWN" if (loss is None or loss >= 100) else "UP"

            items.append({
                "type": "Ping Monitor",
                "target": target,
                "ip_or_url": ip_or_url,
                "status": status,
                "since": last_time.strftime("%d %b %Y, %I:%M %p")
                         if status == "DOWN" and last_time else "—",
                "source": tags.get("location") or tags.get("monitoring_server") or "",
                "customer": customer,
            })
    except Exception as e:
        app.logger.warning("Influx ping failed: %s", e)

    # ---------------------------------------------------------
    # 3) Port Monitors
    # ---------------------------------------------------------
    try:
        q = '''
        SELECT LAST("result") AS result
        FROM "net_response"
        GROUP BY "friendly_name","server","location","port"
        '''
        res = influx_query(q)
        for s in res.get("results", [{}])[0].get("series", []):
            tags = s.get("tags", {}) or {}
            colmap = columns_to_lastrow_map(s)
            last_time = parse_influx_time(s)

            host = tags.get("server")
            port = tags.get("port")
            ip_or_url = f"{host}:{port}" if host and port else host

            result = colmap.get("result")
            status = "UP" if str(result) == "success" else "DOWN"
            customer = resolve_customer_from_tags(tags)

            items.append({
                "type": "Port Monitor",
                "target": tags.get("friendly_name") or host,
                "ip_or_url": ip_or_url or "—",
                "status": status,
                "since": last_time.strftime("%d %b %Y, %I:%M %p")
                         if status == "DOWN" and last_time else "—",
                "source": tags.get("location") or "",
                "customer": customer,
            })
    except Exception as e:
        app.logger.warning("Influx port failed: %s", e)

    # ---------------------------------------------------------
    # 4) URL Monitors
    # ---------------------------------------------------------
    try:
        q = '''
        SELECT LAST("status_code") AS code, LAST("result") AS result
        FROM "http_response"
        GROUP BY "friendly_name","server","location"
        '''
        res = influx_query(q)

        for s in res.get("results", [{}])[0].get("series", []):
            tags = s.get("tags", {}) or {}
            colmap = columns_to_lastrow_map(s)
            last_time = parse_influx_time(s)

            code = colmap.get("code")
            result = colmap.get("result")
            ip_or_url = tags.get("server")
            target = tags.get("friendly_name") or ip_or_url
            customer = resolve_customer_from_tags(tags)

            status = (
                "UP"
                if str(result) == "success" and code and 200 <= int(code) < 400
                else "DOWN"
            )

            items.append({
                "type": "URL Monitor",
                "target": target,
                "ip_or_url": ip_or_url or "—",
                "status": status,
                "since": last_time.strftime("%d %b %Y, %I:%M %p")
                         if status == "DOWN" and last_time else "—",
                "source": tags.get("location") or "",
                "customer": customer,
            })
    except Exception as e:
        app.logger.warning("Influx http_response failed: %s", e)

    # ---------------------------------------------------------
    # Tenant Filtering
    # ---------------------------------------------------------
    items = filter_for_tenant(user, items)

    # Sort DOWN first
    items.sort(key=lambda x: (x["status"] != "DOWN", x["type"], x["target"]))

    return jsonify({"ok": True, "items": items})

