from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for
from functools import wraps
from sqlalchemy import or_
from extensions import db
from models.port_monitor import PortMonitor
from models.proxy import ProxyServer
from models.customer import Customer
from services.licensing import can_add_monitor

port_bp = Blueprint("port", __name__)


# ============================================================
#  AUTH HELPERS + TENANT SCOPE
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
#  PAGE
# ============================================================
@port_bp.get("/port/monitoring")
@require_login
def port_monitor_page():
    proxies = ProxyServer.query.all()
    return render_template("monitoring_port.html", proxies=proxies)


# ============================================================
#  LIST (Tenant Scoped)
# ============================================================
@port_bp.get("/api/port-monitors")
@require_login_api
def api_port_list():
    user = _current_user()
    q = (request.args.get("q") or "").strip()
    requested_customer = request.args.get("customer_id", type=int)

    query = PortMonitor.query

    # ✅ unified scoping logic
    allowed_cid = user.get("customer_id")

    # unrestricted users: admin OR FULL_VIEWER
    if user.get("is_admin") or allowed_cid is None:
        if requested_customer:
            query = query.filter(PortMonitor.customer_id == requested_customer)
    else:
        # tenant user
        query = query.filter(PortMonitor.customer_id == allowed_cid)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                PortMonitor.friendly_name.ilike(like),
                PortMonitor.host_ip.ilike(like),
                PortMonitor.monitoring_server.ilike(like),
            )
        )

    items = (
        query
        .order_by(PortMonitor.id.desc())
        .all()
    )

    return jsonify({
        "ok": True,
        "items": [x.to_dict() for x in items]
    })


# ============================================================
#  CREATE / UPDATE (Tenant Scoped)
# ============================================================
@port_bp.post("/api/port-monitors")
@require_login_api
def api_port_add():
    user = _current_user()
    data = request.get_json(silent=True) or {}

    cid = data.get("customer_id")

    if not Customer.query.get(cid):
        return jsonify({"ok": False, "error": "Invalid customer"}), 400

    # Tenant security
    if not enforce_scope(cid):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    fid = data.get("id")

    if fid:
        # Updating existing
        obj = PortMonitor.query.get_or_404(fid)

        # Tenant cannot edit monitors outside their scope
        if not enforce_scope(obj.customer_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403

        # Tenant cannot reassign customer_id
        if not user.get("is_admin") and cid != obj.customer_id:
            return jsonify({"ok": False, "error": "Cannot change customer_id"}), 403

        obj.customer_id = cid

    else:
        # Creating new
        allowed, lic = can_add_monitor(cid, "port")
        if not allowed:
            return jsonify({"ok": False, "error": lic.get("message"), "license": lic}), 403

        obj = PortMonitor(customer_id=cid)
        db.session.add(obj)

    # Populate fields
    obj.friendly_name = data.get("friendly_name")
    obj.host_ip = data.get("host_ip")
    obj.protocol = data.get("protocol", "tcp")
    obj.timeout = int(data.get("timeout", 5))
    obj.monitoring_server = data.get("monitoring_server")
    obj.active = bool(data.get("active", True))

    ports = data.get("ports", [])
    if isinstance(ports, list):
        obj.ports = ",".join(str(p).strip() for p in ports)
    else:
        obj.ports = str(ports).strip()

    db.session.commit()
    return jsonify({"ok": True, "item": obj.to_dict()})


# ============================================================
#  DELETE (Tenant Scoped)
# ============================================================
@port_bp.delete("/api/port-monitor/<int:fid>")
@require_login_api
def api_port_delete(fid):
    obj = PortMonitor.query.get_or_404(fid)

    # Tenant cannot delete monitors belonging to other customers
    if not enforce_scope(obj.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    db.session.delete(obj)
    db.session.commit()
    return jsonify({"ok": True})

