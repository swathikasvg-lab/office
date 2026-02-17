#routes/sqlserver_routes.py
from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for
from functools import wraps
from sqlalchemy import or_

from extensions import db
from models.customer import Customer
from models.proxy import ProxyServer
from models.sqlserver_monitor import SqlServerMonitor
from services.licensing import can_add_monitor

sqlserver_bp = Blueprint("sqlserver", __name__)

# ============================================================
# AUTH HELPERS + TENANT SCOPE (same pattern as Port Monitoring)
# ============================================================

def _current_user():
    return session.get("user")


def require_login(fn):
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


def enforce_scope(customer_id: int):
    """
    Admin → full access.
    Tenant → only their assigned customer_id.
    """
    user = _current_user()
    if not user:
        return False

    if user.get("is_admin"):
        return True

    return user.get("customer_id") == customer_id


# ============================================================
# PAGE
# ============================================================
@sqlserver_bp.get("/sqlserver/monitoring")
@require_login
def sqlserver_monitor_page():
    proxies = ProxyServer.query.all()
    return render_template("monitoring_sqlserver.html", proxies=proxies)


# ============================================================
# LIST (Tenant Scoped)
# ============================================================
@sqlserver_bp.get("/api/sqlserver-monitors")
@require_login_api
def api_sqlserver_list():
    user = _current_user()
    q = (request.args.get("q") or "").strip()
    requested_customer = request.args.get("customer_id", type=int)

    query = SqlServerMonitor.query

    # unified scoping logic
    allowed_cid = user.get("customer_id")

    # unrestricted users: admin OR FULL_VIEWER (customer_id None)
    if user.get("is_admin") or allowed_cid is None:
        if requested_customer:
            query = query.filter(SqlServerMonitor.customer_id == requested_customer)
    else:
        query = query.filter(SqlServerMonitor.customer_id == allowed_cid)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                SqlServerMonitor.friendly_name.ilike(like),
                SqlServerMonitor.ip_address.ilike(like),
                SqlServerMonitor.monitoring_server.ilike(like),
                SqlServerMonitor.db_type.ilike(like),
                SqlServerMonitor.username.ilike(like),
            )
        )

    items = query.order_by(SqlServerMonitor.id.desc()).all()

    return jsonify({
        "ok": True,
        "items": [x.to_dict() for x in items]
    })


# ============================================================
# GET ONE (for edit) - Tenant Scoped
# ============================================================
@sqlserver_bp.get("/api/sqlserver-monitor/<int:fid>")
@require_login_api
def api_sqlserver_get_one(fid):
    obj = SqlServerMonitor.query.get_or_404(fid)

    if not enforce_scope(obj.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    # include_secret=True to prefill password in edit (optional)
    # If you don't want to return password to UI, set include_secret=False.
    return jsonify({"ok": True, "item": obj.to_dict(include_secret=True)})


# ============================================================
# CREATE / UPDATE (Tenant Scoped)
# ============================================================
@sqlserver_bp.post("/api/sqlserver-monitors")
@require_login_api
def api_sqlserver_add_update():
    user = _current_user()
    data = request.get_json(silent=True) or {}

    cid = data.get("customer_id")
    if not Customer.query.get(cid):
        return jsonify({"ok": False, "error": "Invalid customer"}), 400

    if not enforce_scope(cid):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    fid = data.get("id")

    if fid:
        obj = SqlServerMonitor.query.get_or_404(fid)

        if not enforce_scope(obj.customer_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403

        if not user.get("is_admin") and cid != obj.customer_id:
            return jsonify({"ok": False, "error": "Cannot change customer_id"}), 403
    else:
        allowed, lic = can_add_monitor(cid, "sqlserver")
        if not allowed:
            return jsonify({"ok": False, "error": lic.get("message"), "license": lic}), 403

        obj = SqlServerMonitor(customer_id=cid)
        db.session.add(obj)

    # Populate fields
    obj.customer_id = cid
    obj.friendly_name = (data.get("friendly_name") or "").strip()
    obj.monitoring_server = (data.get("monitoring_server") or "").strip()
    obj.ip_address = (data.get("ip_address") or "").strip()
    obj.port = int(data.get("port") or 1433)
    obj.username = (data.get("username") or "").strip() or None
    obj.db_type = (data.get("db_type") or "SQLServer").strip()
    obj.active = bool(data.get("active", True))

    # password: allow update only if provided (so edit doesn't wipe it)
    password = data.get("password")
    if password is not None and str(password).strip() != "":
        obj.set_password(str(password))

    # minimal validation
    if not obj.friendly_name or not obj.monitoring_server or not obj.ip_address:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    db.session.commit()
    return jsonify({"ok": True, "item": obj.to_dict()})


# ============================================================
# DELETE (Tenant Scoped)
# ============================================================
@sqlserver_bp.delete("/api/sqlserver-monitor/<int:fid>")
@require_login_api
def api_sqlserver_delete(fid):
    obj = SqlServerMonitor.query.get_or_404(fid)

    if not enforce_scope(obj.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    db.session.delete(obj)
    db.session.commit()
    return jsonify({"ok": True})

