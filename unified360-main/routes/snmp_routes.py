from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session
from functools import wraps
from ipaddress import ip_address
from sqlalchemy import or_

from extensions import db
from models.snmp import SnmpConfig, SNMP_TEMPLATES
from models.customer import Customer
from services.licensing import can_add_monitor

snmp_bp = Blueprint("snmp", __name__)


# ============================================================
# RBAC HELPERS
# ============================================================
def _current_user():
    return session.get("user")


def login_required_page(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        return fn(*a, **kw)
    return wrapper


def login_required_api(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return fn(*a, **kw)
    return wrapper


def admin_required_api(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = _current_user()
        if not u or not u.get("is_admin"):
            return jsonify({"ok": False, "error": "Forbidden – Admin access required"}), 403
        return fn(*a, **kw)
    return wrapper


# ============================================================
# PAGE
# ============================================================
@snmp_bp.route("/monitoring/snmp")
@login_required_page
def monitoring_snmp():
    user = _current_user()
    if not user.get("is_admin"):
        # tenants can access SNMP view page but cannot modify
        return render_template("monitoring_snmp_switch.html", templates=SNMP_TEMPLATES, readonly=True)

    return render_template("monitoring_snmp_switch.html", templates=SNMP_TEMPLATES, readonly=False)


# ============================================================
# LIST (Admin: all customers | Tenant: only their customer)
# ============================================================
@snmp_bp.get("/api/snmp-configs")
@login_required_api
def api_snmp_list():
    user = _current_user()
    q = (request.args.get("q") or "").strip()
    req_customer = request.args.get("customer_id", type=int)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = SnmpConfig.query

    # --- Tenant isolation ---
    allowed_customer = user.get("customer_id")
    
    # Admin OR FULL_VIEWER → unrestricted
    if user.get("is_admin") or allowed_customer is None:
        if req_customer:
            query = query.filter(SnmpConfig.customer_id == req_customer)
    else:
        # Tenant user
        query = query.filter(SnmpConfig.customer_id == allowed_customer)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                SnmpConfig.name.ilike(like),
                SnmpConfig.device_ip.ilike(like),
                SnmpConfig.template.ilike(like),
            )
        )

    pag = query.order_by(SnmpConfig.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Tenants ALWAYS receive masked output
    masked = not user.get("is_admin")

    return jsonify({
        "items": [x.to_dict(masked=masked) for x in pag.items],
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages or 1,
    })


# ============================================================
# GET SINGLE
# Admin: can view unmasked  
# Tenant: can view only their object, masked  
# ============================================================
@snmp_bp.get("/api/snmp-configs/<int:item_id>")
@login_required_api
def api_snmp_get(item_id):
    user = _current_user()
    obj = SnmpConfig.query.get_or_404(item_id)

    # Tenant access restriction
    if not user.get("is_admin"):
        if obj.customer_id != user.get("customer_id"):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        return jsonify({"ok": True, "item": obj.to_dict(masked=True)})

    return jsonify({"ok": True, "item": obj.to_dict(masked=False)})


# ============================================================
# CREATE (Admin Only)
# ============================================================
@snmp_bp.post("/api/snmp-configs")
@login_required_api
@admin_required_api
def api_snmp_create():
    data = request.get_json(silent=True) or {}
    print(data.get("community"))

    cid = data.get("customer_id")
    if not Customer.query.get(cid):
        return jsonify({"ok": False, "errors": {"customer_id": "Customer is required"}}), 400

    allowed, lic = can_add_monitor(cid, "snmp")
    if not allowed:
        return jsonify({"ok": False, "error": lic.get("message"), "license": lic}), 403

    name = (data.get("name") or "").strip()
    device_ip = (data.get("device_ip") or "").strip()
    snmp_version = (data.get("snmp_version") or "v2c").lower()
    port = int(data.get("port", 161))
    template = data.get("template", "Generic")

    try:
        ip_address(device_ip)
    except Exception:
        return jsonify({"ok": False, "errors": {"device_ip": "Invalid IP"}}), 400

    if SnmpConfig.query.filter_by(device_ip=device_ip).first():
        return jsonify({"ok": False, "errors": {"device_ip": "Device IP already exists"}}), 409

    obj = SnmpConfig(
        customer_id=cid,
        name=name,
        device_ip=device_ip,
        monitoring_server=data.get("monitoring_server"),
        snmp_version=snmp_version,
        port=port,
        template=template,
    )

    if snmp_version == "v2c":
        obj.community = data.get("community")
    else:
        obj.v3_username = data.get("v3_username")
        obj.v3_auth_protocol = data.get("v3_auth_protocol")
        obj.v3_auth_password = data.get("v3_auth_password")
        obj.v3_priv_protocol = data.get("v3_priv_protocol")
        obj.v3_priv_password = data.get("v3_priv_password")

    db.session.add(obj)
    db.session.commit()
    return jsonify({"ok": True, "item": obj.to_dict()}), 201


# ============================================================
# UPDATE (Admin Only)
# ============================================================
@snmp_bp.put("/api/snmp-configs/<int:item_id>")
@login_required_api
@admin_required_api
def api_snmp_update(item_id):
    item = SnmpConfig.query.get_or_404(item_id)
    data = request.get_json(silent=True) or {}

    item.customer_id = data.get("customer_id", item.customer_id)
    item.name = data.get("name", item.name)
    item.device_ip = data.get("device_ip", item.device_ip)
    item.snmp_version = data.get("snmp_version", item.snmp_version)
    item.port = int(data.get("port", item.port))
    item.template = data.get("template", item.template)
    item.monitoring_server = data.get("monitoring_server", item.monitoring_server)

    if item.snmp_version == "v2c":
        item.community = data.get("community")
        item.v3_username = item.v3_auth_protocol = item.v3_auth_password = item.v3_priv_protocol = item.v3_priv_password = None
    else:
        item.v3_username = data.get("v3_username")
        item.v3_auth_protocol = data.get("v3_auth_protocol")
        item.v3_auth_password = data.get("v3_auth_password")
        item.v3_priv_protocol = data.get("v3_priv_protocol")
        item.v3_priv_password = data.get("v3_priv_password")
        item.community = None

    db.session.commit()
    return jsonify({"ok": True, "item": item.to_dict(masked=False)})


# ============================================================
# DELETE (Admin Only)
# ============================================================
@snmp_bp.delete("/api/snmp-configs/<int:item_id>")
@login_required_api
@admin_required_api
def api_snmp_delete(item_id):
    obj = SnmpConfig.query.get_or_404(item_id)
    db.session.delete(obj)
    db.session.commit()
    return jsonify({"ok": True})

