# routes/device_updown_routes.py

from flask import Blueprint, request, jsonify, render_template

from extensions import db
from models.device_updown_rule import DeviceUpDownRule
from models.contact import ContactGroup
from models.customer import Customer

import security

device_updown_bp = Blueprint("device_updown_bp", __name__)


# --------------------------------------------------------------------
# PAGE
# --------------------------------------------------------------------
@device_updown_bp.get("/alerting/device-updown")
@security.login_required_page
def device_updown_page():
    return render_template("device_updown_rules.html")


# --------------------------------------------------------------------
# API – LIST RULES (TENANT SAFE)
# --------------------------------------------------------------------
@device_updown_bp.get("/api/device-updown/rules")
@security.login_required_api
def api_device_updown_rules():
    user = security.get_current_user()
    requested_customer_id = request.args.get("customer_id", type=int)

    query = DeviceUpDownRule.query

    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is None:
        if requested_customer_id:
            query = query.filter(DeviceUpDownRule.customer_id == requested_customer_id)
    else:
        query = query.filter(DeviceUpDownRule.customer_id == allowed_cid)

    rules = query.order_by(DeviceUpDownRule.updated_at.desc()).all()
    return jsonify({"items": [r.to_dict() for r in rules]})


# --------------------------------------------------------------------
# API – LIST DEVICES (CUSTOMER AWARE – METRIC DRIVEN)
# --------------------------------------------------------------------
@device_updown_bp.get("/api/device-updown/devices")
@security.login_required_api
def api_device_updown_devices():
    """
    Device list is derived from metrics:
      - Prometheus label: CustomerName
      - InfluxDB tag: customer_name
    """

    from alert_engine.handlers.device_updown import (
        _get_snmp_last_seen_for_customer,
        _get_server_last_seen_for_customer,
        _get_idrac_last_seen_for_customer,
        _get_ilo_last_seen_for_customer,
    )


    customer_id = request.args.get("customer_id", type=int)
    customer_name = None

    if customer_id:
        cust = Customer.query.get(customer_id)
        if not cust:
            return jsonify({"items": []})
        customer_name = cust.name.replace("'", "\\'")
        print(customer_name)

    devices = []

    # SNMP devices (InfluxDB)
    snmp_map = _get_snmp_last_seen_for_customer(customer_name)
    #print(snmp_map)
    for host in snmp_map.keys():
        devices.append({
            "source": "snmp",
            "device": host,
            "label": f"SNMP :: {host}",
        })

    # Server devices (Prometheus)
    server_map = _get_server_last_seen_for_customer(customer_name)
    for host in server_map.keys():
        devices.append({
            "source": "server",
            "device": host,
            "label": f"Server :: {host}",
        })

    # iDRAC devices
    idrac_map = _get_idrac_last_seen_for_customer(customer_name)
    for host in idrac_map.keys():
        devices.append({
            "source": "idrac",
            "device": host,
            "label": f"iDRAC :: {host}",
        })
    
    # iLO devices
    ilo_map = _get_ilo_last_seen_for_customer(customer_name)
    print(ilo_map)
    for host in ilo_map.keys():
        devices.append({
            "source": "ilo",
            "device": host,
            "label": f"iLO :: {host}",
        })


    return jsonify({"items": devices})


# --------------------------------------------------------------------
# API – CREATE / OVERWRITE RULES
# --------------------------------------------------------------------
@device_updown_bp.post("/api/device-updown/rules")
@security.login_required_api
@security.require_permission("alert.manage")
def api_device_updown_create():
    payload = request.get_json() or {}
    user = security.get_current_user()

    customer_id = int(payload.get("customer_id"))
    devices = payload.get("devices", [])
    contact_group_id = payload.get("contact_group_id")

    errors = {}

    # Customer validation
    if not customer_id:
        errors["customer_id"] = "Customer is required."
    else:
        cust = Customer.query.get(customer_id)
        if not cust:
            errors["customer_id"] = "Invalid customer."

    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None and customer_id != allowed_cid:
        errors["customer_id"] = "Unauthorized customer."

    # Devices
    if not devices or not isinstance(devices, list):
        errors["devices"] = "Select at least one device."

    # Contact group
    if not contact_group_id:
        errors["contact_group_id"] = "Contact group is required."
    else:
        cg = ContactGroup.query.get(contact_group_id)
        if not cg:
            errors["contact_group_id"] = "Invalid contact group."
        elif cg.customer_id != customer_id:
            errors["contact_group_id"] = "Contact group does not belong to customer."

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    created, updated = 0, 0

    for d in devices:
        source = d.get("source")
        device = d.get("device")
        if not source or not device:
            continue

        rule = DeviceUpDownRule.query.filter_by(
            customer_id=customer_id,
            source=source,
            device=device,
        ).first()

        if rule:
            rule.contact_group_id = contact_group_id
            rule.is_enabled = True
            updated += 1
        else:
            rule = DeviceUpDownRule(
                customer_id=customer_id,
                source=source,
                device=device,
                contact_group_id=contact_group_id,
                is_enabled=True,
            )
            db.session.add(rule)
            created += 1

    db.session.commit()

    return jsonify({
        "ok": True,
        "created": created,
        "updated": updated,
    })


# --------------------------------------------------------------------
# API – DELETE RULE
# --------------------------------------------------------------------
@device_updown_bp.delete("/api/device-updown/rules/<int:rid>")
@security.login_required_api
@security.require_permission("alert.manage")
def api_device_updown_delete(rid):
    user = security.get_current_user()
    rule = DeviceUpDownRule.query.get_or_404(rid)

    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None and rule.customer_id != allowed_cid:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    db.session.delete(rule)
    db.session.commit()
    return jsonify({"ok": True})

