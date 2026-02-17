# routes/iis_routes.py
from flask import Blueprint, jsonify, render_template, session, redirect, url_for, request
from functools import wraps
import requests
import time
from datetime import datetime

from models.customer import Customer

iis_bp = Blueprint("iis", __name__)

# ============================================================
# AUTH HELPERS (same pattern as server_routes.py)
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


# ============================================================
# CONFIG
# ============================================================
PROMETHEUS_URL = "http://localhost:9090"
STALE_THRESHOLD = 600  # 10 mins


# ============================================================
# PROMETHEUS HELPERS
# ============================================================
def normalize_instance(name):
    return str(name).split(":")[0] if name else name


def prom_query(query: str):
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


def _customer_name_from_metric(m: dict) -> str:
    return (
        m.get("CustomerName")
        or m.get("customer")
        or m.get("customer_name")
        or "Backend"
    )


def _location_from_metric(m: dict) -> str:
    return m.get("location") or m.get("dcname") or "—"


def _iso_from_ts(ts: float) -> str:
    if not ts:
        return "N/A"
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_inactive_over_7d(iso: str) -> bool:
    if not iso or iso == "N/A":
        return True
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        age_sec = (datetime.now(dt.tzinfo) - dt).total_seconds()
        return age_sec > (7 * 24 * 3600)
    except Exception:
        return True


def _apps_display(apps):
    """
    apps: list of tuples [(app_name, worker_count), ...]
    """
    if not apps:
        return "—"
    # keep stable ordering
    apps = sorted(apps, key=lambda x: (x[0] or "").lower())
    names = [a[0] for a in apps if a and a[0]]
    if not names:
        return "—"
    if len(names) <= 3:
        return ", ".join(names)
    return ", ".join(names[:3]) + f" (+{len(names)-3} more)"


# ============================================================
# ROUTES
# ============================================================
@iis_bp.get("/monitoring/iis")
@require_login_page
def monitoring_iis_page():
    return render_template("monitoring_iis.html")


@iis_bp.get("/api/monitored-iis")
@require_login_api
def api_monitored_iis():
    """
    IIS monitoring rows are primarily per (instance, site).
    App column shows app pools present on the instance (summary).
    """
    now_ts = time.time()

    # query params (same UX as server monitoring)
    page = int(request.args.get("page", 1) or 1)
    per_page = int(request.args.get("per_page", 25) or 25)
    q = (request.args.get("q") or "").strip().lower()
    requested_customer = (request.args.get("customer") or "All").strip()
    show_inactive = (request.args.get("show_inactive") or "false").lower() == "true"

    # customers mapping for enforcing customer-scoped access
    try:
        custs = Customer.query.all()
        customer_name_map = {c.cid: c.name for c in custs}
    except Exception:
        customer_name_map = {}

    user = _current_user() or {}
    user_customer_id = user.get("customer_id")
    user_roles = user.get("roles") or []
    is_admin = bool(user.get("is_admin"))
    is_noc = "noc" in [r.lower() for r in user_roles]
    unrestricted = is_admin or is_noc or (user_customer_id is None)

    if not unrestricted:
        user_customer_name = customer_name_map.get(user_customer_id)
        if not user_customer_name:
            return jsonify({"ok": False, "error": "User is customer-scoped but customer mapping not found."}), 403

    # ----------------- PROMETHEUS QUERIES -----------------
    uptime_res = prom_query("windows_iis_service_uptime")
    ts_res = prom_query("max by (instance, site) (timestamp(windows_iis_service_uptime))")
    app_res = prom_query("windows_iis_current_worker_processes")

    # timestamp map per (instance, site)
    ts_map = {}
    for r in ts_res:
        m = r.get("metric", {})
        inst = normalize_instance(m.get("instance"))
        site = m.get("site") or "—"
        try:
            ts_map[(inst, site)] = float(r["value"][1])
        except Exception:
            ts_map[(inst, site)] = 0.0

    # app pools per instance
    apps_by_instance = {}
    for r in app_res:
        m = r.get("metric", {})
        inst = normalize_instance(m.get("instance"))
        app = m.get("app") or "—"
        try:
            workers = int(float(r["value"][1]))
        except Exception:
            workers = 0
        apps_by_instance.setdefault(inst, []).append((app, workers))

    # base rows per (instance, site)
    base = {}
    for r in uptime_res:
        m = r.get("metric", {})
        inst = normalize_instance(m.get("instance"))
        site = m.get("site") or "—"
        customer_name = _customer_name_from_metric(m)
        location = _location_from_metric(m)
        try:
            uptime_seconds = float(r["value"][1])
        except Exception:
            uptime_seconds = 0.0

        key = (inst, site)
        # keep max uptime if duplicate series
        if key not in base or uptime_seconds > base[key].get("uptime_seconds", 0):
            base[key] = {
                "customer_name": customer_name,
                "instance": inst,
                "site": site,
                "location": location,
                "uptime_seconds": uptime_seconds,
            }

    # if exporter gives only app metrics (rare), create at least one row per instance
    if not base and apps_by_instance:
        for inst in apps_by_instance.keys():
            base[(inst, "—")] = {
                "customer_name": "Backend",
                "instance": inst,
                "site": "—",
                "location": "—",
                "uptime_seconds": 0.0,
            }

    # build final items
    items = []
    for (inst, site), b in base.items():
        last_ts = ts_map.get((inst, site), 0.0)
        delay = now_ts - last_ts if last_ts else now_ts
        status = "UP" if delay <= STALE_THRESHOLD else "DOWN"
        last_update_iso = _iso_from_ts(last_ts)
        app_display = _apps_display(apps_by_instance.get(inst, []))

        items.append({
            "customer_name": b.get("customer_name") or "Backend",
            "instance": inst,
            "site": site,
            "app": app_display,
            "last_update_ts": last_ts,
            "last_update": last_update_iso,
            "delay": int(delay),
            "status": status,
            "location": b.get("location") or "—",
        })

    # ----------------- ACCESS CONTROL -----------------
    if unrestricted:
        filtered = items
        if requested_customer and requested_customer.lower() != "all":
            filtered = [x for x in filtered if (x.get("customer_name") or "").lower() == requested_customer.lower()]
    else:
        user_customer_name = customer_name_map.get(user_customer_id) or ""
        filtered = [x for x in items if (x.get("customer_name") or "").lower() == user_customer_name.lower()]

    # show_inactive filter (>7d)
    if not show_inactive:
        filtered = [x for x in filtered if not _is_inactive_over_7d(x.get("last_update"))]

    # search filter
    if q:
        def hit(x):
            return (
                q in (x.get("customer_name") or "").lower()
                or q in (x.get("instance") or "").lower()
                or q in (x.get("site") or "").lower()
                or q in (x.get("app") or "").lower()
            )
        filtered = [x for x in filtered if hit(x)]

    # customers list for dropdown (post-access-control, pre-pagination)
    customers = sorted({(x.get("customer_name") or "Backend") for x in filtered})

    # sort + paginate
    filtered.sort(key=lambda x: (x.get("customer_name") or "", x.get("instance") or "", x.get("site") or ""))
    total = len(filtered)

    if per_page <= 0:
        per_page = 25
    pages = max(1, (total + per_page - 1) // per_page)
    if page < 1:
        page = 1
    if page > pages:
        page = pages

    start = (page - 1) * per_page
    end = start + per_page
    page_items = filtered[start:end]

    return jsonify({
        "ok": True,
        "items": page_items,
        "total": total,
        "pages": pages,
        "customers": customers,
    })

