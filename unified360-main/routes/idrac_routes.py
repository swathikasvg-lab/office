from flask import Blueprint, request, jsonify, render_template
from sqlalchemy import or_
from extensions import db
from models.idrac import IdracConfig
from models.customer import Customer
from services.licensing import can_add_monitor

from security import (
    login_required_page,
    login_required_api,
    get_current_user,
    get_allowed_customer_id,
    enforce_customer_scope,
)

idrac_bp = Blueprint("idrac", __name__)


# ---------------------------------------------------------
# PAGE
# ---------------------------------------------------------
@idrac_bp.get("/monitoring/idrac")
@login_required_page
def monitoring_idrac():
    return render_template("monitoring_idrac.html")


# ---------------------------------------------------------
# LIST (Tenant-Scoped)
# ---------------------------------------------------------
@idrac_bp.get("/api/idrac-configs")
@login_required_api
def api_idrac_list():
    user = get_current_user()
    allowed_customer = get_allowed_customer_id(user)
    is_unrestricted = allowed_customer is None  # admin + FULL_VIEWER

    q = (request.args.get("q") or "").strip()
    req_customer = request.args.get("customer_id", type=int)

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = IdracConfig.query

    if not is_unrestricted:
        query = query.filter(IdracConfig.customer_id == allowed_customer)
    else:
        if req_customer:
            query = query.filter(IdracConfig.customer_id == req_customer)

    if q:
        like = f"%{q}%"
        query = query.filter(IdracConfig.device_ip.ilike(like))

    pag = query.order_by(IdracConfig.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "items": [x.to_dict(masked=True) for x in pag.items],
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages or 1,
    })


# ---------------------------------------------------------
# GET SINGLE (Tenant-Scoped)
# ---------------------------------------------------------
@idrac_bp.get("/api/idrac-configs/<int:item_id>")
@login_required_api
def api_idrac_get(item_id):
    user = get_current_user()

    item = IdracConfig.query.get_or_404(item_id)

    # Tenant cannot access others’ customers
    if not enforce_customer_scope(user, item.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    return jsonify({"ok": True, "item": item.to_dict(masked=False)})


# ---------------------------------------------------------
# CREATE (Tenant-Scoped)
# ---------------------------------------------------------
@idrac_bp.post("/api/idrac-configs")
@login_required_api
def api_idrac_create():
    user = get_current_user()

    data = request.get_json(silent=True) or {}
    cid = data.get("customer_id")

    # Validate Customer
    if not cid or not Customer.query.get(cid):
        return jsonify({"ok": False, "errors": {"customer_id": "Invalid customer"}}), 400

    # Tenant cannot create under another customer
    if not enforce_customer_scope(user, cid):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    allowed, lic = can_add_monitor(cid, "idrac")
    if not allowed:
        return jsonify({"ok": False, "error": lic.get("message"), "license": lic}), 403

    device_ip = (data.get("device_ip") or "").strip()

    if IdracConfig.query.filter_by(device_ip=device_ip).first():
        return jsonify({"ok": False, "errors": {"device_ip": "Device IP already exists"}}), 409

    item = IdracConfig(
        customer_id=cid,
        device_ip=device_ip,
        monitoring_server=data.get("monitoring_server"),
        snmp_version=data.get("snmp_version", "v2c"),
        community=data.get("community"),
        port=int(data.get("port", 161)),
    )

    db.session.add(item)
    db.session.commit()
    return jsonify({"ok": True, "item": item.to_dict()}), 201


# ---------------------------------------------------------
# UPDATE (Tenant-Scoped)
# ---------------------------------------------------------
@idrac_bp.put("/api/idrac-configs/<int:item_id>")
@login_required_api
def api_idrac_update(item_id):
    user = get_current_user()

    item = IdracConfig.query.get_or_404(item_id)
    data = request.get_json(silent=True) or {}

    # Tenant cannot modify others’ customers
    if not enforce_customer_scope(user, item.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    new_cid = data.get("customer_id", item.customer_id)

    # Tenant cannot reassign customer_id
    if not enforce_customer_scope(user, new_cid):
        return jsonify({"ok": False, "error": "Cannot change customer_id"}), 403

    item.customer_id = new_cid
    item.device_ip = data.get("device_ip", item.device_ip)
    item.monitoring_server = data.get("monitoring_server", item.monitoring_server)
    item.snmp_version = data.get("snmp_version", item.snmp_version)

    if data.get("community"):
        item.community = data["community"]

    item.port = int(data.get("port", item.port))

    db.session.commit()
    return jsonify({"ok": True, "item": item.to_dict()})


# ---------------------------------------------------------
# DELETE (Tenant-Scoped)
# ---------------------------------------------------------
@idrac_bp.delete("/api/idrac-configs/<int:item_id>")
@login_required_api
def api_idrac_delete(item_id):
    user = get_current_user()

    obj = IdracConfig.query.get_or_404(item_id)

    # Tenant cannot delete others’ customers
    if not enforce_customer_scope(user, obj.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    db.session.delete(obj)
    db.session.commit()

    return jsonify({"ok": True})

