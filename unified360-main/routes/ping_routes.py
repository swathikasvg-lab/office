from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session
from functools import wraps
from sqlalchemy import or_
from extensions import db
from models.ping import PingConfig
from models.customer import Customer
from services.licensing import can_add_monitor

ping_bp = Blueprint("ping", __name__)


# ============================================================
#  AUTH HELPERS + RBAC + TENANT SCOPING
# ============================================================
def is_full_viewer():
    user = _current_user()
    return user and "FULL_VIEWER" in (user.get("roles") or [])


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
    Admin + FULL_VIEWER → full access
    Tenant users → only their own customer_id
    """
    user = _current_user()
    if not user:
        return False

    if user.get("is_admin") or "FULL_VIEWER" in (user.get("roles") or []):
        return True

    return user.get("customer_id") == customer_id


# ============================================================
#  PAGE
# ============================================================
@ping_bp.route("/monitoring/ping")
@require_login
def monitoring_ping():
    return render_template("monitoring_ping.html")


# ============================================================
#  LIST
# ============================================================
@ping_bp.get("/api/ping-configs")
@require_login_api
def api_ping_list():
    user = _current_user()

    q = (request.args.get("q") or "").strip()
    requested_customer = request.args.get("customer_id", type=int)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = PingConfig.query

    allowed_cid = user.get("customer_id")
    roles = user.get("roles") or []

    # Global admin OR FULL_VIEWER → unrestricted list
    if user.get("is_admin") or "FULL_VIEWER" in roles:
        if requested_customer:
            query = query.filter(PingConfig.customer_id == requested_customer)

    # Tenant-scoped users
    else:
        query = query.filter(PingConfig.customer_id == allowed_cid)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                PingConfig.name.ilike(like),
                PingConfig.host.ilike(like),
                PingConfig.monitoring_server.ilike(like),
            )
        )

    pag = query.order_by(PingConfig.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "items": [x.to_dict() for x in pag.items],
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages or 1,
    })


# ============================================================
#  BULK CREATE
# ============================================================
@ping_bp.post("/api/ping-configs")
@require_login_api
def api_ping_create():

    if is_full_viewer():
        return jsonify({"ok": False, "error": "Read-only access"}), 403

    user = _current_user()
    data = request.get_json() or {}

    customer_id = data.get("customer_id")
    if not Customer.query.get(customer_id):
        return jsonify({"ok": False, "errors": {"customer_id": "Invalid customer"}}), 400

    # Tenant cannot create for other customers
    if not enforce_scope(customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    items = data.get("items") or []
    monitoring_server = data.get("monitoring_server")
    timeout = int(data.get("timeout", 5))
    packet_count = int(data.get("packet_count", 3))

    inserted, ignored = [], []
    candidates = []

    for it in items:
        host = (it.get("host") or "").strip()
        name = (it.get("name") or "").strip()
        if not host or not name:
            continue

        if PingConfig.query.filter_by(host=host).first():
            ignored.append(host)
            continue

        candidates.append({"host": host, "name": name})

    if candidates:
        allowed, lic = can_add_monitor(customer_id, "ping", delta=len(candidates))
        if not allowed:
            return jsonify({"ok": False, "error": lic.get("message"), "license": lic}), 403

    for it in candidates:
        obj = PingConfig(
            customer_id=customer_id,
            name=it["name"],
            host=it["host"],
            monitoring_server=monitoring_server,
            timeout=timeout,
            packet_count=packet_count,
        )
        db.session.add(obj)
        inserted.append(obj)

    db.session.commit()
    return jsonify({
        "ok": True,
        "inserted": [o.to_dict() for o in inserted],
        "inserted_count": len(inserted),
        "ignored_hosts": ignored,
    }), 201


# ============================================================
#  GET SINGLE (Tenant Scoped)
# ============================================================
@ping_bp.get("/api/ping-configs/<int:item_id>")
@require_login_api
def api_ping_get(item_id):
    obj = PingConfig.query.get_or_404(item_id)

    if not enforce_scope(obj.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    return jsonify({"ok": True, "item": obj.to_dict()})


# ============================================================
#  UPDATE (Tenant Scoped)
# ============================================================
@ping_bp.put("/api/ping-configs/<int:item_id>")
@require_login_api
def api_ping_update(item_id):
    if is_full_viewer():
        return jsonify({"ok": False, "error": "Read-only access"}), 403

    user = _current_user()
    obj = PingConfig.query.get_or_404(item_id)
    data = request.get_json() or {}

    # Tenant cannot modify other customer's configs
    if not enforce_scope(obj.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    # Tenant cannot reassign to another customer
    new_customer_id = data.get("customer_id", obj.customer_id)
    if not user.get("is_admin") and new_customer_id != obj.customer_id:
        return jsonify({"ok": False, "error": "Cannot change customer_id"}), 403

    if not Customer.query.get(new_customer_id):
        return jsonify({"ok": False, "errors": {"customer_id": "Invalid customer"}}), 400

    obj.customer_id = new_customer_id
    obj.name = data.get("name", obj.name)
    obj.host = data.get("host", obj.host)
    obj.timeout = int(data.get("timeout", obj.timeout))
    obj.packet_count = int(data.get("packet_count", obj.packet_count))
    obj.monitoring_server = data.get("monitoring_server", obj.monitoring_server)

    db.session.commit()
    return jsonify({"ok": True, "item": obj.to_dict()})


# ============================================================
#  DELETE (Tenant Scoped)
# ============================================================
@ping_bp.delete("/api/ping-configs/<int:item_id>")
@require_login_api
def api_ping_delete(item_id):
    if is_full_viewer():
        return jsonify({"ok": False, "error": "Read-only access"}), 403

    obj = PingConfig.query.get_or_404(item_id)

    # Tenant cannot delete others' configs
    if not enforce_scope(obj.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    db.session.delete(obj)
    db.session.commit()
    return jsonify({"ok": True})

