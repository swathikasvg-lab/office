from flask import Blueprint, request, jsonify, render_template
from sqlalchemy import or_
from ipaddress import ip_address

from extensions import db
from models.link_monitor import LinkMonitor
from models.customer import Customer
from services.licensing import can_add_monitor

from security import (
    login_required_page,
    login_required_api,
    get_current_user,
    get_allowed_customer_id,
    enforce_customer_scope,
)

link_bp = Blueprint("links", __name__)


# =====================================================
# Page
# =====================================================
@link_bp.get("/monitoring/link")
@login_required_page
def monitoring_link():
    return render_template("monitoring_link.html")


# =====================================================
# Helpers
# =====================================================
def _s(v):
    return (v or "").strip()


def _i(v, default=None):
    try:
        if v in (None, "", "undefined"):
            return default
        return int(v)
    except Exception:
        return default


def _is_ip(v):
    try:
        ip_address(v)
        return True
    except Exception:
        return False


def _validate_payload(data):
    errors = {}

    cust_id = data.get("customer_id")
    try:
        cust_id = int(cust_id)
        if not Customer.query.get(cust_id):
            errors["customer_id"] = "Invalid customer"
    except Exception:
        errors["customer_id"] = "Customer is required"

    if not _s(data.get("link_name")):
        errors["link_name"] = "Link name is required"

    if not _s(data.get("monitoring_server")):
        errors["monitoring_server"] = "Monitoring server is required"

    ip_addr = _s(data.get("ip_address"))
    if not ip_addr or not _is_ip(ip_addr):
        errors["ip_address"] = "Valid IP address is required"

    if not _s(data.get("if_index")):
        errors["if_index"] = "SNMP ifIndex is required"

    link_type = _s(data.get("link_type") or "ISP")
    if link_type not in ("ISP", "ILL"):
        errors["link_type"] = "Link type must be ISP or ILL"

    snmp_ver = _s(data.get("snmp_version") or "2c")
    if snmp_ver not in ("2c", "3"):
        errors["snmp_version"] = "SNMP version must be 2c or 3"

    if snmp_ver == "2c" and not _s(data.get("snmp_community")):
        errors["snmp_community"] = "Community string required for SNMP v2c"

    return errors


# =====================================================
# LIST (Tenant Scoped)
# =====================================================
@link_bp.get("/api/link-monitors")
@login_required_api
def api_link_list():
    user = get_current_user()
    allowed_customer = get_allowed_customer_id(user)  # None = admin

    q = _s(request.args.get("q"))
    req_customer_id = request.args.get("customer_id", type=int)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = LinkMonitor.query.outerjoin(Customer)

    # === TENANT SCOPING ===
    if allowed_customer is not None:
        query = query.filter(LinkMonitor.customer_id == allowed_customer)
    else:
        if req_customer_id:
            query = query.filter(LinkMonitor.customer_id == req_customer_id)

    # === Search ===
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            LinkMonitor.link_name.ilike(like),
            LinkMonitor.site.ilike(like),
            LinkMonitor.ip_address.ilike(like),
            LinkMonitor.monitoring_server.ilike(like),
            Customer.name.ilike(like),
        ))

    pag = query.order_by(LinkMonitor.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "items": [x.to_dict() for x in pag.items],
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages or 1,
    })


# =====================================================
# CREATE (Tenant Scoped)
# =====================================================
@link_bp.post("/api/link-monitors")
@login_required_api
def api_link_create():
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    errors = _validate_payload(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    customer_id = int(data.get("customer_id"))

    # Tenant cannot create under another customer
    if not enforce_customer_scope(user, customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    allowed, lic = can_add_monitor(customer_id, "link")
    if not allowed:
        return jsonify({"ok": False, "error": lic.get("message"), "license": lic}), 403

    obj = LinkMonitor(
        customer_id=customer_id,
        link_name=_s(data.get("link_name")),
        site=_s(data.get("site")),
        monitoring_server=_s(data.get("monitoring_server")),
        ip_address=_s(data.get("ip_address")),
        if_index=_s(data.get("if_index")),
        link_type=_s(data.get("link_type") or "ISP"),
        provisioned_bandwidth_mbps=_i(data.get("bandwidth_mbps")),

        snmp_version=_s(data.get("snmp_version") or "2c"),
        snmp_community=_s(data.get("snmp_community")),

        snmpv3_sec_level=_s(data.get("snmpv3_sec_level")),
        snmpv3_username=_s(data.get("snmpv3_username")),
        snmpv3_auth_protocol=_s(data.get("snmpv3_auth_protocol")),
        snmpv3_auth_password=_s(data.get("snmpv3_auth_password")),
        snmpv3_priv_protocol=_s(data.get("snmpv3_priv_protocol")),
        snmpv3_priv_password=_s(data.get("snmpv3_priv_password")),
    )

    db.session.add(obj)
    db.session.commit()

    return jsonify({"ok": True, "item": obj.to_dict()}), 201


# =====================================================
# GET SINGLE (Tenant Scoped)
# =====================================================
@link_bp.get("/api/link-monitors/<int:item_id>")
@login_required_api
def api_link_get(item_id):
    user = get_current_user()

    item = LinkMonitor.query.get_or_404(item_id)

    # Tenant cannot view others’ customers
    if not enforce_customer_scope(user, item.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    return jsonify({"ok": True, "item": item.to_dict()})


# =====================================================
# UPDATE (Tenant Scoped)
# =====================================================
@link_bp.put("/api/link-monitors/<int:item_id>")
@login_required_api
def api_link_update(item_id):
    user = get_current_user()
    item = LinkMonitor.query.get_or_404(item_id)
    data = request.get_json(silent=True) or {}

    # Tenant cannot modify other customers' records
    if not enforce_customer_scope(user, item.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    errors = _validate_payload(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    new_customer_id = int(data.get("customer_id"))

    # Tenant cannot reassign customer_id
    if not enforce_customer_scope(user, new_customer_id):
        return jsonify({"ok": False, "error": "Cannot change customer_id"}), 403

    # Update fields
    item.customer_id = new_customer_id
    item.link_name = _s(data.get("link_name"))
    item.site = _s(data.get("site"))
    item.monitoring_server = _s(data.get("monitoring_server"))
    item.ip_address = _s(data.get("ip_address"))
    item.if_index = _s(data.get("if_index"))
    item.link_type = _s(data.get("link_type") or "ISP")
    item.provisioned_bandwidth_mbps = _i(data.get("bandwidth_mbps"))

    item.snmp_version = _s(data.get("snmp_version") or "2c")
    item.snmp_community = _s(data.get("snmp_community"))

    item.snmpv3_sec_level = _s(data.get("snmpv3_sec_level"))
    item.snmpv3_username = _s(data.get("snmpv3_username"))
    item.snmpv3_auth_protocol = _s(data.get("snmpv3_auth_protocol"))
    item.snmpv3_auth_password = _s(data.get("snmpv3_auth_password"))
    item.snmpv3_priv_protocol = _s(data.get("snmpv3_priv_protocol"))
    item.snmpv3_priv_password = _s(data.get("snmpv3_priv_password"))

    db.session.commit()
    return jsonify({"ok": True, "item": item.to_dict()})


# =====================================================
# DELETE (Tenant Scoped)
# =====================================================
@link_bp.delete("/api/link-monitors/<int:item_id>")
@login_required_api
def api_link_delete(item_id):
    user = get_current_user()
    item = LinkMonitor.query.get_or_404(item_id)

    # Tenant cannot delete others’ data
    if not enforce_customer_scope(user, item.customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    db.session.delete(item)
    db.session.commit()
    return jsonify({"ok": True})


# =====================================================
# AGENT SYNC (NO AUTH) – SAFE TO KEEP PUBLIC
# =====================================================
@link_bp.get("/api/monitoring/sync_link")
def api_sync_link():
    server = _s(request.args.get("server"))
    query = LinkMonitor.query
    if server:
        query = query.filter(LinkMonitor.monitoring_server == server)

    items = [x.to_dict(masked=False) for x in query.order_by(LinkMonitor.id.asc()).all()]
    #print(items)
    return jsonify({"ok": True, "items": items})

