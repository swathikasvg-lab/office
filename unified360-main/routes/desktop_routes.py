from flask import Blueprint, render_template, jsonify, session, redirect, url_for, request, current_app
from functools import wraps
import sqlite3, os, requests, time
from datetime import datetime

desktop_bp = Blueprint("desktop", __name__)

STALE_THRESHOLD = 600            # 10 minutes
INACTIVE_7DAYS = 7 * 24 * 3600   # 7 days
CACHE_DB_PATH = "/usr/local/autointelli/opsduty-server/.desktops_cache.db"


# ============================================================
#  DECORATORS
# ============================================================
def login_required_page(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        return fn(*a, **kw)
    return wrap


def login_required_api(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if "user" not in session:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return fn(*a, **kw)
    return wrap


# ============================================================
#  SQLITE CACHE INITIALIZATION
# ============================================================
def init_cache_db(path=CACHE_DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS desktops_cache (
            host TEXT PRIMARY KEY,
            customer_name TEXT,
            os TEXT,
            cpu REAL,
            mem REAL,
            disk REAL,
            download TEXT,
            upload TEXT,
            loss REAL,
            latency REAL,
            is_up_to_date INTEGER,
            pending_updates INTEGER,
            last_update_ts REAL,
            last_update_iso TEXT,
            status TEXT,
            updated_at INTEGER
        )
    """)
    conn.commit()
    conn.close()


def get_db_conn():
    if not os.path.exists(CACHE_DB_PATH):
        init_cache_db()
    return sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)


def read_cache_all(conn):
    cur = conn.cursor()
    cur.execute("SELECT * FROM desktops_cache")
    rows = cur.fetchall()

    out = {}
    for r in rows:
        out[r[0]] = {
            "host": r[0],
            "customer_name": r[1],
            "os": r[2],
            "cpu": r[3],
            "mem": r[4],
            "disk": r[5],
            "download": r[6],
            "upload": r[7],
            "loss": r[8],
            "latency": r[9],
            "is_up_to_date": bool(r[10]),
            "pending_updates": r[11],
            "last_update_ts": r[12],
            "last_update": r[13],
            "status": r[14],
            "updated_at": r[15]
        }
    return out


def upsert_cache(conn, d):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO desktops_cache (
            host, customer_name, os, cpu, mem, disk, download, upload,
            loss, latency, is_up_to_date, pending_updates,
            last_update_ts, last_update_iso, status, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(host) DO UPDATE SET
            customer_name=excluded.customer_name,
            os=excluded.os,
            cpu=excluded.cpu,
            mem=excluded.mem,
            disk=excluded.disk,
            download=excluded.download,
            upload=excluded.upload,
            loss=excluded.loss,
            latency=excluded.latency,
            is_up_to_date=excluded.is_up_to_date,
            pending_updates=excluded.pending_updates,
            last_update_ts=excluded.last_update_ts,
            last_update_iso=excluded.last_update_iso,
            status=excluded.status,
            updated_at=excluded.updated_at
    """, (
        d["host"], d["customer_name"], d["os"], d["cpu"], d["mem"], d["disk"],
        d["download"], d["upload"], d["loss"], d["latency"], int(d["is_up_to_date"]),
        d["pending_updates"], d["last_update_ts"], d["last_update"],
        d["status"], int(time.time())
    ))
    conn.commit()


# ============================================================
#  HELPERS
# ============================================================
def influx_query(q):
    try:
        url = current_app.config["INFLUXDB_URL"]
        #dbname = current_app.config["INFLUXDB_DB"]
        dbname = "end_user_monitoring"

        r = requests.get(url, params={"db": dbname, "q": q}, timeout=10)
        data = r.json().get("results", [])
        #print(data)
        series = []
        for result in data:
            for s in result.get("series", []):
                series.append(s)
        return series
    except Exception as e:
        #print(str(e))
        return []


def extract_host(tags, row):
    keys = ["host", "hostname", "instance", "host_name"]
    value = None

    for k in keys:
        if k in tags and tags[k]:
            value = tags[k]
            break

    if not value:
        for k in keys:
            if k in row and row[k]:
                value = row[k]
                break

    if not value:
        return None

    h = str(value).strip()
    h = h.split(":")[0]
    h = h.split(".")[0]
    return h.upper()


def extract_numeric(v, default=0.0):
    try:
        if v is None:
            return float(default)
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace(",", " ").split()[0]
        return float(s)
    except:
        return float(default)


def parse_time_value(t):
    if t is None:
        return 0.0
    try:
        if isinstance(t, (int, float)) or (isinstance(t, str) and t.isdigit()):
            num = int(t)
            if num > 1e15: return num / 1e9
            if num > 1e12: return num / 1e6
            if num > 1e10: return num / 1e3
            return float(num)
    except:
        pass

    try:
        return datetime.strptime(str(t), "%Y-%m-%dT%H:%M:%SZ").timestamp()
    except:
        return 0.0


def beautify_os(val):
    if not val:
        return None
    return str(val).replace('"', "").replace("_", " ").strip()


def map_series(series):
    out = {}
    for s in series:
        tags = s.get("tags", {}) or {}
        cols = s.get("columns", [])
        for v in s.get("values", []):
            row = dict(zip(cols, v))
            host = extract_host(tags, row)
            if not host:
                continue
            if host not in out:
                out[host] = {}
            out[host].update(tags)
            out[host].update(row)
            out[host]["host"] = host
    return out


# ------------------------------------------------------------
# Helper: sorting key factory
# ------------------------------------------------------------
def make_sort_key(field):
    # support a few known fields; fallback to host
    def key_fn(item):
        if field in ("host", "hostname"):
            return (item.get("host") or item.get("hostname") or "").lower()
        if field == "os":
            return (item.get("os") or "").lower()
        if field == "cpu":
            return float(item.get("cpu") or 0.0)
        if field == "mem":
            return float(item.get("mem") or 0.0)
        if field == "disk":
            return float(item.get("disk") or 0.0)
        if field == "last_update" or field == "last_update_ts":
            return float(item.get("last_update_ts") or 0.0)
        if field == "status":
            # make UP sort before DOWN when ascending
            return 0 if item.get("status") == "UP" else 1
        if field == "pending_updates":
            return int(item.get("pending_updates") or 0)
        if field == "is_up_to_date":
            return 0 if item.get("is_up_to_date") else 1
        # fallback
        return (item.get("host") or "").lower()
    return key_fn


# ============================================================
#  ROUTES
# ============================================================
@desktop_bp.get("/monitoring/desktops")
@login_required_page
def monitoring_desktops_page():
    return render_template("monitoring_desktops.html")


@desktop_bp.get("/api/monitored-desktops")
@login_required_api
def api_monitored_desktops():
    try:
        now_ts = time.time()
        conn = get_db_conn()
        cache = read_cache_all(conn)

        # parse sorting params (global)
        sort_by = request.args.get("sort_by", "host")
        order = request.args.get("order", "asc").lower()
        if order not in ("asc", "desc"):
            order = "asc"

        # ---- MAIN MEASUREMENT QUERIES ----
        q_system  = "SELECT host,uptime,customer_name FROM system GROUP BY host ORDER BY time DESC LIMIT 1"
        q_os      = "SELECT * FROM os_info GROUP BY host ORDER BY time DESC LIMIT 1"
        q_cpu     = "SELECT host, 100 - usage_idle AS cpu_used FROM cpu WHERE cpu='cpu-total' GROUP BY host ORDER BY time DESC LIMIT 1"
        q_mem     = "SELECT host, used_percent FROM mem GROUP BY host ORDER BY time DESC LIMIT 1"
        q_disk    = "SELECT host, used_percent FROM disk WHERE (path='\\\\C:' OR path='/') GROUP BY host ORDER BY time DESC LIMIT 1"
        q_pending = "SELECT host, last(pending_updates) FROM system_update_status GROUP BY host"
        q_update  = "SELECT host, last(is_up_to_date) FROM system_update_status GROUP BY host"
        q_speed   = "SELECT hostname, download_mbps, upload_mbps FROM speed_test GROUP BY hostname ORDER BY time DESC LIMIT 1"
        q_isp     = "SELECT host, packet_loss_percent, response_time_ms FROM isp_uptime GROUP BY host ORDER BY time DESC LIMIT 1"

        system_map  = map_series(influx_query(q_system))
        os_map      = map_series(influx_query(q_os))
        cpu_map     = map_series(influx_query(q_cpu))
        mem_map     = map_series(influx_query(q_mem))
        disk_map    = map_series(influx_query(q_disk))
        pending_map = map_series(influx_query(q_pending))
        update_map  = map_series(influx_query(q_update))
        speed_map   = map_series(influx_query(q_speed))
        isp_map     = map_series(influx_query(q_isp))

        # ---- CUSTOMER NAMES (distinct) ----
        customer_series = influx_query('SELECT DISTINCT("customer_name") FROM (select * from system WHERE time >= now() - 30d)')
        customer_list = []
        for s in customer_series:
            for v in s.get("values", []):
                if v[1]:
                    customer_list.append(v[1].strip())
        customer_list = sorted(list(set(customer_list)))

        hosts = set().union(
            system_map.keys(), os_map.keys(), cpu_map.keys(),
            mem_map.keys(), disk_map.keys(), pending_map.keys(),
            update_map.keys(), speed_map.keys(), isp_map.keys(), cache.keys()
        )

        result = []

        for h in hosts:
            cached = cache.get(h, {})

            # CUSTOMER NAME
            customer = (
                system_map.get(h, {}).get("customer_name") or
                os_map.get(h, {}).get("customer_name") or
                cached.get("customer_name") or "UNKNOWN"
            )

            # LAST UPDATE (pick latest metric time)
            last_time = (
                system_map.get(h, {}).get("time") or
                cpu_map.get(h, {}).get("time") or
                mem_map.get(h, {}).get("time") or
                disk_map.get(h, {}).get("time") or
                speed_map.get(h, {}).get("time") or
                isp_map.get(h, {}).get("time") or
                cached.get("last_update_ts")
            )
            last_ts = parse_time_value(last_time)
            last_iso = datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%dT%H:%M:%SZ") if last_ts else "N/A"
            print(last_iso)
            status = "UP" if last_ts and (now_ts - last_ts) <= STALE_THRESHOLD else "DOWN"

            # OS (prefer os_name_1 or os_name)
            raw_os = (
                os_map.get(h, {}).get("os_name") or
                os_map.get(h, {}).get("os_name_1") or None
            )
            os_name = beautify_os(raw_os) or cached.get("os", "unknown")

            # CPU / MEM / DISK
            cpu  = extract_numeric(cpu_map.get(h, {}).get("cpu_used"), cached.get("cpu", 0))
            mem  = extract_numeric(mem_map.get(h, {}).get("used_percent"), cached.get("mem", 0))
            disk = extract_numeric(disk_map.get(h, {}).get("used_percent"), cached.get("disk", 0))

            # UPDATES
            pending = extract_numeric(pending_map.get(h, {}).get("last"), cached.get("pending_updates", 0))
            up2date = bool(extract_numeric(update_map.get(h, {}).get("last"), cached.get("is_up_to_date", 0)))

            # SPEED
            download = speed_map.get(h, {}).get("download_mbps")
            upload   = speed_map.get(h, {}).get("upload_mbps")
            download = f"{extract_numeric(download):.2f} Mbps" if download else "—"
            upload   = f"{extract_numeric(upload):.2f} Mbps" if upload else "—"

            # ISP
            isp_data = isp_map.get(h, {}) or {}
            loss_raw = isp_data.get("packet_loss_percent")
            latency_raw = isp_data.get("response_time_ms")
            loss = extract_numeric(loss_raw, 0) if loss_raw is not None else 0
            latency = extract_numeric(latency_raw, 0) if latency_raw is not None else 0

            entry = {
                "host": h,
                "hostname": h,
                "customer_name": customer,
                "os": os_name,
                "cpu": cpu,
                "mem": mem,
                "disk": disk,
                "download": download,
                "upload": upload,
                "gateway_packet_loss": loss,
                "gateway_response_ms": latency,
                # required by cache
                "loss": loss,
                "latency": latency,
                "pending_updates": int(pending),
                "is_up_to_date": up2date,
                "last_update_ts": last_ts,
                "last_update": last_iso,
                "status": status,
            }

            result.append(entry)
            upsert_cache(conn, entry)

        conn.close()

        # --- Build customers_meta (active/total counts) from full result set ---
        customers_meta = {}
        for r in result:
            cname = r.get("customer_name") or "UNKNOWN"
            if cname not in customers_meta:
                customers_meta[cname] = {"name": cname, "active": 0, "total": 0}
            customers_meta[cname]["total"] += 1
            if r.get("status") == "UP":
                customers_meta[cname]["active"] += 1

        customers_meta_list = list(customers_meta.values())
        # sort customers: active desc, then name asc
        customers_meta_list.sort(key=lambda x: (-x["active"], x["name"]))

        # produce flat names list (sorted)
        customers_sorted_names = [c["name"] for c in customers_meta_list]

        # --------------------------
        # Global sorting (before pagination)
        # --------------------------
        sort_key = make_sort_key(sort_by)
        reverse = (order == "desc")
        try:
            result.sort(key=sort_key, reverse=reverse)
        except Exception:
            # fallback to host sort
            result.sort(key=make_sort_key("host"), reverse=False)

        # ----- FILTERING (apply before pagination) -----
        q = request.args.get("q", "").lower().strip()
        filter_customer = request.args.get("customer", "All")
        show_inactive = request.args.get("show_inactive", "true") in ("true", "1", "yes")

        items = result

        if filter_customer and filter_customer.lower() != "all":
            items = [i for i in items if i["customer_name"].lower() == filter_customer.lower()]

        if q:
            items = [i for i in items if q in (i.get("host","") or "").lower() or q in (i.get("os","") or "").lower()]

        if not show_inactive:
            cutoff = now_ts - INACTIVE_7DAYS
            items = [i for i in items if (i.get("last_update_ts") or 0) >= cutoff]

        # ----- PAGINATION -----
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 25))
        total = len(items)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        start = (page - 1) * per_page
        paged = items[start:start + per_page]

        return jsonify({
            "ok": True,
            "items": paged,
            "total": total,
            "pages": pages,
            "customers": customers_sorted_names,
            "customers_meta": customers_meta_list,
            "sort_by": sort_by,
            "order": order
        })

    except Exception as e:
        print("Error in monitored-desktops:", e)
        try:
            conn.close()
        except:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500

