# routes/alert_routes.py

import sqlite3
import os

from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, session
)
from functools import wraps
import datetime

from extensions import db
from models.alert_rule import AlertRule
from models.contact import ContactGroup
from models.customer import Customer

import security

alerts_bp = Blueprint("alerts_bp", __name__)

# --------------------------------------------------------------------
# Constants / Mapping
# --------------------------------------------------------------------
ALLOWED_FIELDS = {
    "server": {
        "cpu_usage": "number",
        "mem_usage": "number",
        "disk_usage": "number",
        "status": "string",
        "network_transmit_mbps": "number",
        "network_receive_mbps": "number",
    },
    "port": {"port_status": "string", "response_time_ms": "number"},
    "idrac": {
        "system_health": "string",
        "power_status": "string",
        "temperature_c": "number",
    },
    "ping": {"latency_ms": "number", "packet_loss": "number"},
    "url": {"status_code": "number", "response_time_ms": "number"},
    "bandwidth": {"in_mbps": "number", "out_mbps": "number"},
    "service_down": {
        "service_name": "string",
    },
    "oracle": {
        "db_status": "string",
        "tablespace_usage_pct": "number",
        "active_sessions": "number",
    },
}

OPS = {">", ">=", "<", "<=", "==", "!=", "="}

MONITORING_MAP = {
    "Server": "server",
    "Port": "port",
    "iDRAC": "idrac",
    "Ping": "ping",
    "URL": "url",
    "Bandwidth": "bandwidth",
    "SNMP Interface": "SNMP_Interface",
    "Service Down": "service_down",
    "Oracle": "oracle",
}

# helper to convert monitoring type back to label for UI
TYPE_TO_LABEL = {v: k for k, v in MONITORING_MAP.items()}

CACHE_DB_PATH = os.environ.get(
    "AUTOINTER_CACHE_DB",
    "/usr/local/autointelli/opsduty-server/.servers_cache.db"
)

# --------------------------------------------------------------------
# VALIDATION
# --------------------------------------------------------------------
def _validate_payload(payload, current_user=None):
    """
    Validate payload and enforce tenant rules (does not persist).
    Returns: (errors_dict, mtype, eval_count, is_enabled)
    """
    errors = {}

    name = (payload.get("name") or "").strip()
    mlabel = payload.get("monitoring_label")
    customer_id = payload.get("customer_id")
    logic = payload.get("logic")
    contact_group_id = payload.get("contact_group_id")
    is_enabled = bool(payload.get("is_enabled", True))
    eval_count = payload.get("evaluation_count", 1)
    svc_instance = (payload.get("svc_instance") or "").strip()
    oracle_monitor_id = (payload.get("oracle_monitor_id") or "").strip()
    oracle_tablespace = (payload.get("oracle_tablespace") or "__ALL__").strip()



    # Monitoring conversion
    mtype = MONITORING_MAP.get(mlabel)

    if mtype == "service_down":
        if not svc_instance:
            errors["svc_instance"] = "Server (instance) is required for Service Down."

    # ✅ Oracle validation
    if mtype == "oracle":
        if not oracle_monitor_id:
            errors["oracle_monitor_id"] = "Oracle Instance is required for Oracle monitoring."



    # Customer Validation
    if not customer_id:
        errors["customer_id"] = "Customer is required."
    else:
        cust = Customer.query.get(customer_id)
        if not cust:
            errors["customer_id"] = "Invalid customer."

    # Enforce tenant scoping (tenant cannot create rules for other customer)
    if current_user and not current_user.get("is_admin"):
        if current_user.get("customer_id") != customer_id:
            errors["customer_id"] = "Unauthorized customer assignment."

    if not name:
        errors["name"] = "Rule name is required."

    if not mtype:
        errors["monitoring_label"] = "Unsupported monitoring type."

    # Contact group must belong to same customer
    if not contact_group_id:
        errors["contact_group_id"] = "Contact group is required."
    else:
        cg = ContactGroup.query.get(contact_group_id)
        if not cg:
            errors["contact_group_id"] = "Invalid contact group."
        elif customer_id and cg.customer_id != customer_id:
            errors["contact_group_id"] = "Contact group does not belong to the customer."

    try:
        eval_count = int(eval_count)
        if eval_count < 1 or eval_count > 10:
            raise ValueError
    except Exception:
        errors["evaluation_count"] = "Evaluation count must be 1–10."

    if logic is None:
        errors["logic"] = "Logic is required."

    return errors, mtype, eval_count, is_enabled, svc_instance,  oracle_monitor_id, oracle_tablespace


def _servers_from_cache_by_customer_name(customer_name: str):
    if not os.path.exists(CACHE_DB_PATH):
        return []

    conn = sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT instance
        FROM servers_cache
        WHERE lower(customer_name) = lower(?)
        ORDER BY instance ASC
    """, (customer_name,))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows if r and r[0]]


@alerts_bp.get("/api/servers/by-customer")
@security.login_required_api
def api_servers_by_customer():
    """
    Returns server instances (from sqlite cache) for the selected customer_id.
    Response: { ok: True, items: [ {id: "<instance>", name: "<instance>"} ] }
    """
    user = security.get_current_user()
    customer_id = request.args.get("customer_id", type=int)

    if not customer_id:
        return jsonify({"ok": True, "items": []})

    # Tenant enforcement
    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None and allowed_cid != customer_id:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    cust = Customer.query.get(customer_id)
    if not cust:
        return jsonify({"ok": True, "items": []})

    instances = _servers_from_cache_by_customer_name(cust.name)

    return jsonify({
        "ok": True,
        "items": [{"id": inst, "name": inst} for inst in instances]
    })


# --------------------------------------------------------------------
# PAGE (UI)
# --------------------------------------------------------------------
@alerts_bp.get("/alert/config")
@security.login_required_page
def alert_config():
    return render_template("alert_config.html")


# --------------------------------------------------------------------
# API – LIST RULES (TENANT SCOPED)
# --------------------------------------------------------------------
@alerts_bp.get("/api/alert-rules")
@security.login_required_api
def api_alert_list():
    """
    Query params:
      - page
      - per_page
      - q
      - customer_id (admin / FULL_VIEWER only)
    """
    user = security.get_current_user()

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)
    q = (request.args.get("q") or "").strip()
    requested_customer_id = request.args.get("customer_id", type=int)

    query = AlertRule.query

    # ------------------------------------------------
    # RBAC / Tenant Scoping
    # ------------------------------------------------
    allowed_cid = security.get_allowed_customer_id(user)

    if allowed_cid is None:
        # ADMIN or FULL_VIEWER
        if requested_customer_id:
            query = query.filter(AlertRule.customer_id == requested_customer_id)
    else:
        # Tenant user
        query = query.filter(AlertRule.customer_id == allowed_cid)

    # ------------------------------------------------
    # Search
    # ------------------------------------------------
    if q:
        like = f"%{q}%"
        query = query.filter(AlertRule.name.ilike(like))

    # ------------------------------------------------
    # Pagination
    # ------------------------------------------------
    pag = query.order_by(AlertRule.created_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    items = []
    for r in pag.items:
        d = r.to_dict()
        d["monitoring_label"] = TYPE_TO_LABEL.get(
            r.monitoring_type, r.monitoring_type
        )
        items.append(d)

    return jsonify({
        "items": items,
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages or 1
    })


# --------------------------------------------------------------------
# API – CREATE (TENANT-SAFE)
# --------------------------------------------------------------------
@alerts_bp.post("/api/alert-rules")
@security.login_required_api
@security.require_permission("alert.manage")
def api_alert_create():
    payload = request.get_json() or {}
    user = security.get_current_user()
    errors, mtype, eval_count, is_enabled, svc_instance, oracle_monitor_id, oracle_tablespace = _validate_payload(
        payload, current_user=user.to_dict() if user else None
    )


    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Determine final customer_id to use
    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is None:
        # admin: accept provided or None
        cid = payload.get("customer_id")
    else:
        # tenant: force their cid
        cid = allowed_cid

    rule = AlertRule(
        customer_id=cid,
        name=payload["name"].strip(),
        monitoring_type=mtype,
        logic_json=payload["logic"],
        contact_group_id=payload["contact_group_id"],
        is_enabled=is_enabled,
        evaluation_count=eval_count,
        created_at=datetime.datetime.utcnow(),
        svc_instance=svc_instance if mtype == "service_down" else None,
    )
    # ✅ Save Oracle extra fields if present in model
    if mtype == "oracle":
        try:
            setattr(rule, "oracle_monitor_id", oracle_monitor_id or None)
            setattr(rule, "oracle_tablespace", oracle_tablespace or "__ALL__")
        except Exception:
            pass

    db.session.add(rule)
    db.session.commit()

    return jsonify({"ok": True, "item": rule.to_dict()}), 201


# --------------------------------------------------------------------
# API – UPDATE (TENANT-SAFE)
# --------------------------------------------------------------------
@alerts_bp.put("/api/alert-rules/<int:rid>")
@security.login_required_api
@security.require_permission("alert.manage")
def api_alert_update(rid):
    user = security.get_current_user()

    rule = AlertRule.query.get_or_404(rid)

    # Tenant cannot modify rules of another customer
    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None and rule.customer_id != allowed_cid:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    payload = request.get_json() or {}
    #print(payload)

    # Ensure payload customer_id is not used to reassign cross-tenant (unless admin)
    if "customer_id" in payload:
        print(allowed_cid)
        if allowed_cid is not None and payload["customer_id"] != rule.customer_id:
            return jsonify({"ok": False, "error": "Cannot reassign customer_id"}), 403

    errors, mtype, eval_count, is_enabled, svc_instance, oracle_monitor_id, oracle_tablespace = _validate_payload(
        payload, current_user=user.to_dict() if user else None
    )

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Update fields
    rule.name = payload["name"].strip()
    rule.monitoring_type = mtype
    rule.logic_json = payload["logic"]
    rule.contact_group_id = payload["contact_group_id"]
    rule.is_enabled = is_enabled
    rule.evaluation_count = eval_count
    rule.svc_instance = svc_instance if mtype == "service_down" else None

    # ✅ Save Oracle extra fields if present in model
    if mtype == "oracle":
        try:
            setattr(rule, "oracle_monitor_id", oracle_monitor_id or None)
            setattr(rule, "oracle_tablespace", oracle_tablespace or "__ALL__")
        except Exception:
            pass
    else:
        # clear if switching from oracle to other types
        try:
            setattr(rule, "oracle_monitor_id", None)
            setattr(rule, "oracle_tablespace", None)
        except Exception:
            pass


    if user.is_admin:
        rule.customer_id = payload["customer_id"]
    db.session.add(rule) 
    db.session.commit()
    return jsonify({"ok": True, "item": rule.to_dict()})


# --------------------------------------------------------------------
# API – DELETE (TENANT-SAFE)
# --------------------------------------------------------------------
@alerts_bp.delete("/api/alert-rules/<int:rid>")
@security.login_required_api
@security.require_permission("alert.manage")
def api_alert_delete(rid):
    user = security.get_current_user()

    rule = AlertRule.query.get_or_404(rid)

    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None and rule.customer_id != allowed_cid:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    db.session.delete(rule)
    db.session.commit()
    return jsonify({"ok": True})

