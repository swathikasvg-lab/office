from flask import Blueprint, jsonify, render_template, session, redirect, url_for, request
from functools import wraps
import requests
import time
from datetime import datetime
import sqlite3
import os
import math

from models.customer import Customer

server_bp = Blueprint("server", __name__)

# ============================================================
# AUTH HELPERS
# ============================================================
def _current_user():
    return session.get("user")


def require_login_page(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        return fn(*a, **kw)
    return wrapper


def require_login_api(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return fn(*a, **kw)
    return wrapper


def require_admin_api(fn):
    """
    Backwards-compatible decorator kept for endpoints that must absolutely be admin-only.
    Use require_login_api + in-endpoint checks for endpoints that can allow non-admin access
    with constraints (preferred for monitored-servers).
    """
    @wraps(fn)
    def wrapper(*a, **kw):
        user = _current_user()
        if not user or not user.get("is_admin"):
            return jsonify({"ok": False, "error": "Forbidden – Admin access required"}), 403
        return fn(*a, **kw)
    return wrapper


# ============================================================
# CONFIG
# ============================================================
PROMETHEUS_URL = "http://localhost:9090"
STALE_THRESHOLD = 600  # 10 mins
INACTIVE_7DAYS = 7 * 24 * 3600
CACHE_DB_PATH = os.environ.get(
    "AUTOINTER_CACHE_DB",
    "/usr/local/autointelli/opsduty-server/.servers_cache.db"
)


# ============================================================
# SQLITE CACHE
# ============================================================
def init_cache_db(path: str = CACHE_DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS servers_cache (
            instance TEXT PRIMARY KEY,
            location TEXT,
            customer_name TEXT,
            os TEXT,
            cpu REAL,
            mem REAL,
            disk REAL,
            download TEXT,
            upload TEXT,
            last_update_ts REAL,
            last_update_iso TEXT,
            delay INTEGER,
            status TEXT,
            updated_at INTEGER
        )
    """)

    conn.commit()
    conn.close()
    return path


def get_db_conn():
    if not os.path.exists(CACHE_DB_PATH):
        init_cache_db(CACHE_DB_PATH)
    return sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)


def read_cache_all(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT instance, location, customer_name, os, cpu, mem, disk,
               download, upload, last_update_ts, last_update_iso,
               delay, status, updated_at
        FROM servers_cache
    """)

    rows = cur.fetchall()
    items = {}
    for r in rows:
        items[r[0]] = {
            "instance": r[0],
            "location": r[1],
            "customer_name": r[2],
            "os": r[3],
            "cpu": safe_metric_value(r[4], 0.0),
            "mem": safe_metric_value(r[5], 0.0),
            "disk": safe_metric_value(r[6], 0.0),
            "download": r[7] or "—",
            "upload": r[8] or "—",
            "last_update_ts": float(r[9] or 0),
            "last_update": r[10] or "N/A",
            "delay": int(r[11] or 0),
            "status": r[12] or "DOWN",
            "updated_at": int(r[13] or 0),
        }
    return items


def upsert_cache(conn, s):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO servers_cache (
            instance, location, customer_name, os, cpu, mem, disk,
            download, upload, last_update_ts, last_update_iso,
            delay, status, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance) DO UPDATE SET
            location=excluded.location,
            customer_name=excluded.customer_name,
            os=excluded.os,
            cpu=excluded.cpu,
            mem=excluded.mem,
            disk=excluded.disk,
            download=excluded.download,
            upload=excluded.upload,
            last_update_ts=excluded.last_update_ts,
            last_update_iso=excluded.last_update_iso,
            delay=excluded.delay,
            status=excluded.status,
            updated_at=excluded.updated_at
    """, (
        s["instance"],
        s.get("location", "—"),
        s.get("customer_name", "Backend"),
        s.get("os", "unknown"),
        safe_metric_value(s.get("cpu", 0.0), 0.0),
        safe_metric_value(s.get("mem", 0.0), 0.0),
        safe_metric_value(s.get("disk", 0.0), 0.0),
        s.get("download", "—"),
        s.get("upload", "—"),
        float(s.get("last_update_ts") or 0),
        s.get("last_update") or "N/A",
        int(s.get("delay") or 0),
        s.get("status") or "DOWN",
        int(time.time())
    ))
    conn.commit()


init_cache_db(CACHE_DB_PATH)


# ============================================================
# PROMETHEUS HELPERS
# ============================================================
def normalize_instance(name):
    return str(name).split(":")[0] if name else name


def safe_metric_value(value, default=0.0):
    """
    Convert metric values to finite floats for JSON-safe responses.
    Handles NaN/inf values returned by Prometheus and cache.
    """
    try:
        parsed = float(value)
        if not math.isfinite(parsed):
            return float(default)
        return parsed
    except Exception:
        return float(default)


def prom_query(query):
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=10
        )
        if r.ok:
            return r.json().get("data", {}).get("result", [])
    except Exception:
        return []
    return []


def format_bits_per_sec(value):
    try:
        value = float(value)
        if value > 1e9:
            return f"{value/1e9:.2f} Gbps"
        if value > 1e6:
            return f"{value/1e6:.2f} Mbps"
        if value > 1e3:
            return f"{value/1e3:.2f} Kbps"
        return f"{value:.2f} bps"
    except:
        return "—"


# ============================================================
# ROUTES
# ============================================================
@server_bp.get("/monitoring/servers")
@require_login_page
def monitoring_servers_page():
    return render_template("monitoring_servers.html")


@server_bp.get("/api/monitored-servers")
@require_login_api
def api_monitored_servers():
    """
    Server monitoring:
      - Admin and NOC role: full view, can optionally filter by 'customer' (string name).
      - Customer-scoped users: see only their customer (server-side enforced).
    Returns:
      { ok: True, items: [...], total: <int>, pages: 1, customers: [...] }
    """
    conn = None
    try:
        now_ts = time.time()
        conn = get_db_conn()
        cache = read_cache_all(conn)

        # Load customers (map cid -> name) so we can translate session customer_id to name
        try:
            custs = Customer.query.all()
            customer_name_map = {c.cid: c.name for c in custs}
        except Exception:
            # If DB not reachable, continue with empty map; cache/customer_name values will still work
            customer_name_map = {}

        user = _current_user() or {}
        user_customer_id = user.get("customer_id")
        user_roles = user.get("roles") or []
        is_admin = bool(user.get("is_admin"))
        normalized_roles = {
            str(r).strip().upper()
            for r in user_roles
            if r is not None and str(r).strip()
        }
        is_noc = "NOC" in normalized_roles
        is_full_viewer = "FULL_VIEWER" in normalized_roles

        # Treat blank/null-like customer_id as unscoped for legacy payloads.
        has_customer_scope = user_customer_id not in (None, "", "null", "None")
        unrestricted = is_admin or is_noc or is_full_viewer or (not has_customer_scope)
        requested_customer = (request.args.get("customer") or "").strip()

        # If non-unrestricted (customer-bound) ignore requested_customer and enforce their customer
        if not unrestricted:
            # translate ID -> name
            user_customer_name = customer_name_map.get(user_customer_id)
            if not user_customer_name:
                # If we don't have a mapping, the user likely has a missing/invalid customer.
                return jsonify({"ok": False, "error": "User is customer-scoped but customer mapping not found."}), 403

        # ----------------- PROMETHEUS QUERIES -----------------
        os_results = prom_query("node_os_info or windows_os_info")
        os_map = {}
        for r in os_results:
            inst = normalize_instance(r["metric"].get("instance"))
            os_map[inst] = {
                "os": (
                    r["metric"].get("pretty_name")
                    or r["metric"].get("product")
                    or f"{r['metric'].get('sysname','')} {r['metric'].get('release','')}"
                ),
                "location": r["metric"].get("location", "—"),
                "customer_name": r["metric"].get("CustomerName", "Backend")
            }

        cpu_query = (
            "100 - (avg by (instance)(irate(node_cpu_seconds_total{mode='idle'}[5m])) * 100)"
            " or "
            "100 - (avg by (instance)(irate(windows_cpu_time_total{mode='idle'}[5m])) * 100)"
        )
        cpu_map = {
            normalize_instance(r["metric"].get("instance")):
                round(safe_metric_value(r["value"][1], 0.0), 1)
            for r in prom_query(cpu_query)
        }

        mem_query = (
            "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100"
            " or "
            "(100 - 100 * windows_os_physical_memory_free_bytes / windows_cs_physical_memory_bytes)"
        )
        mem_map = {
            normalize_instance(r["metric"].get("instance")):
                round(safe_metric_value(r["value"][1], 0.0), 1)
            for r in prom_query(mem_query)
        }

        disk_query = (
            "(100 - (node_filesystem_avail_bytes{fstype!~'tmpfs|overlay'} / node_filesystem_size_bytes{fstype!~'tmpfs|overlay'} * 100))"
            " or "
            "(100 - 100 * windows_logical_disk_free_bytes / windows_logical_disk_size_bytes)"
        )
        disk_map = {
            normalize_instance(r["metric"].get("instance")):
                round(safe_metric_value(r["value"][1], 0.0), 1)
            for r in prom_query(disk_query)
        }

        # Network
        download_map = {
            normalize_instance(r["metric"].get("instance")):
                format_bits_per_sec(r["value"][1])
            for r in prom_query(
                "max(rate(node_network_receive_bytes_total[2m])*8) by (instance)"
                " or "
                "sum by (instance)(irate(windows_net_bytes_received_total[5m]) * 8)"
            )
        }

        upload_map = {
            normalize_instance(r["metric"].get("instance")):
                format_bits_per_sec(r["value"][1])
            for r in prom_query(
                "max(rate(node_network_transmit_bytes_total[2m])*8) by (instance)"
                " or "
                "sum by (instance)(irate(windows_net_bytes_sent_total[5m]) * 8)"
            )
        }

        ts_map = {
            normalize_instance(r["metric"].get("instance")):
                safe_metric_value(r["value"][1], 0.0)
            for r in prom_query(
                "max by (instance) (timestamp(node_cpu_seconds_total) or timestamp(windows_cpu_time_total))"
            )
        }

        # ----------------- MERGE ALL DATA -----------------
        instances = set(cache.keys()) | set(os_map.keys()) | set(cpu_map.keys())
        servers = {}

        for inst in instances:
            cached = cache.get(inst, {})
            last_ts = ts_map.get(inst, cached.get("last_update_ts", 0))
            delay = now_ts - last_ts if last_ts else now_ts
            status = "UP" if delay <= STALE_THRESHOLD else "DOWN"
            last_update_iso = (
                datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%dT%H:%M:%SZ")
                if last_ts else "N/A"
            )

            entry = {
                "instance": inst,
                "location": cached.get("location") or os_map.get(inst, {}).get("location"),
                "customer_name": cached.get("customer_name") or os_map.get(inst, {}).get("customer_name") or "Backend",
                "os": cached.get("os") or os_map.get(inst, {}).get("os") or "unknown",
                "cpu": safe_metric_value(cpu_map.get(inst, cached.get("cpu", 0.0)), 0.0),
                "mem": safe_metric_value(mem_map.get(inst, cached.get("mem", 0.0)), 0.0),
                "disk": safe_metric_value(disk_map.get(inst, cached.get("disk", 0.0)), 0.0),
                "download": download_map.get(inst, cached.get("download", "—")),
                "upload": upload_map.get(inst, cached.get("upload", "—")),
                "last_update_ts": last_ts,
                "last_update": last_update_iso,
                "delay": int(delay),
                "status": status,
            }

            servers[inst] = entry
            upsert_cache(conn, entry)

        # ----------------- ACCESS CONTROL & FILTERING -----------------
        if unrestricted:
            # Admin/NOC: allow filter by requested_customer (string). If none, return all.
            if requested_customer and requested_customer.lower() != "all":
                filtered = [
                    s for s in servers.values()
                    if (s.get("customer_name") or "").lower() == requested_customer.lower()
                ]
            else:
                filtered = list(servers.values())
        else:
            # Customer-scoped: show only user's customer
            user_customer_name = customer_name_map.get(user_customer_id)
            # If mapping fails, deny access earlier above.
            filtered = [
                s for s in servers.values()
                if (s.get("customer_name") or "").lower() == (user_customer_name or "").lower()
            ]

        # Prepare response metadata (no pagination implemented server-side yet)
        items = sorted(filtered, key=lambda x: (x.get("customer_name") or "", x.get("instance") or ""))
        visible_customer_names = sorted({(s.get("customer_name") or "Backend") for s in items})
        total = len(items)
        pages = 1

        return jsonify({
            "ok": True,
            "items": items,
            "total": total,
            "pages": pages,
            "customers": visible_customer_names
        })

    except Exception as e:
        try:
            if conn:
                conn.close()
        except:
            pass
        print("Error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

