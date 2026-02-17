# routes/oracle_routes.py
from flask import Blueprint, jsonify, render_template, session, redirect, url_for, request, current_app
from functools import wraps

from extensions import db
from models.oracle_db_monitor import OracleDbMonitor
from models.customer import Customer
from services.licensing import can_add_monitor

oracle_bp = Blueprint("oracle", __name__)

# ============================================================
# AUTH HELPERS
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

def _can_edit_oracle():
    user = _current_user() or {}
    perms = set(user.get("permissions", []) or [])
    if user['is_admin']:
        return True
    return ("edit_oracle" in perms)

# ============================================================
# PAGE
# ============================================================
@oracle_bp.route("/monitoring/oracle")
@require_login_page
def oracle_monitor_page():
    return render_template("monitoring_oracle.html")

# ============================================================
# LIST (Frontend expects: { items: [...] })
# ============================================================
@oracle_bp.route("/api/oracle-db-monitors", methods=["GET"])
@require_login_api
def api_oracle_db_monitors_list():
    customer_id = (request.args.get("customer_id") or "").strip()
    q = (request.args.get("q") or "").strip().lower()

    query = OracleDbMonitor.query.join(Customer, Customer.cid == OracleDbMonitor.customer_id)

    if customer_id:
        try:
            query = query.filter(OracleDbMonitor.customer_id == int(customer_id))
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid customer_id"}), 400

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                db.func.lower(db.func.coalesce(OracleDbMonitor.friendly_name, "")).like(like),
                db.func.lower(OracleDbMonitor.host).like(like),
                db.func.lower(OracleDbMonitor.service_name).like(like),
                db.func.lower(OracleDbMonitor.username).like(like),
                db.func.lower(OracleDbMonitor.monitoring_server).like(like),
                db.func.lower(Customer.name).like(like),
                db.func.lower(Customer.acct_id).like(like),
            )
        )

    rows = query.order_by(Customer.name.asc(), OracleDbMonitor.updated_at.desc()).all()
    return jsonify({"items": [r.to_dict() for r in rows]})

# ============================================================
# CREATE / UPDATE (Frontend uses same endpoint with POST)
# ============================================================
@oracle_bp.route("/api/oracle-db-monitors", methods=["POST"])
@require_login_api
def api_oracle_db_monitors_upsert():
    if not _can_edit_oracle():
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    payload = request.get_json(force=True) or {}

    def _req(k):
        v = (payload.get(k) or "").strip() if isinstance(payload.get(k), str) else payload.get(k)
        if v is None or v == "":
            raise ValueError(f"Missing field: {k}")
        return v

    try:
        _id = payload.get("id")  # can be null for create
        customer_id = int(_req("customer_id"))
        friendly_name = (payload.get("friendly_name") or "").strip()
        host = _req("host").strip()
        port = int(payload.get("port") or 1521)
        service_name = _req("service_name").strip()
        username = _req("username").strip()
        monitoring_server = _req("monitoring_server").strip()
        active = bool(payload.get("active", True))

        # password optional on edit; required on create
        password = payload.get("password")
        if _id:
            # if present, update; if absent, keep existing
            password = password.strip() if isinstance(password, str) and password.strip() else None
        else:
            password = _req("password").strip()

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    # validate customer exists
    c = Customer.query.get(customer_id) or Customer.query.filter_by(cid=customer_id).first()
    if not c:
        return jsonify({"ok": False, "error": "Customer not found"}), 404

    if _id:
        row = OracleDbMonitor.query.get(_id)
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404
    else:
        allowed, lic = can_add_monitor(customer_id, "oracle")
        if not allowed:
            return jsonify({"ok": False, "error": lic.get("message"), "license": lic}), 403

        row = OracleDbMonitor(customer_id=customer_id)

    row.customer_id = customer_id
    row.friendly_name = friendly_name
    row.host = host
    row.port = port
    row.service_name = service_name
    row.username = username
    row.monitoring_server = monitoring_server
    row.active = active

    if password is not None:
        row.password = password

    db.session.add(row)
    db.session.commit()

    return jsonify({"ok": True, "id": row.id})

# ============================================================
# DELETE (Frontend uses /api/oracle-db-monitor/<id>)
# ============================================================
@oracle_bp.route("/api/oracle-db-monitor/<int:monitor_id>", methods=["DELETE"])
@require_login_api
def api_oracle_db_monitor_delete(monitor_id: int):
    if not _can_edit_oracle():
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    row = OracleDbMonitor.query.get(monitor_id)
    if not row:
        return jsonify({"ok": False, "error": "Not found"}), 404

    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})

# ============================================================
# AGENT EXPORT API (kept similar, but now uses OracleDbMonitor)
# ============================================================
@oracle_bp.route("/api/agent/oracle/configs", methods=["GET"])
def api_agent_oracle_configs():
    token = request.headers.get("X-AGENT-TOKEN", "")
    expected = current_app.config.get("AGENT_TOKEN", "")
    if not expected or token != expected:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    rows = OracleDbMonitor.query.filter_by(active=True).order_by(OracleDbMonitor.customer_id.asc()).all()

    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "customer_id": r.customer_id,
            "customer_name": r.customer.name if r.customer else "",
            "customer_acct_id": r.customer.acct_id if r.customer else "",
            "db_name": r.service_name,
            "host": r.host,
            "port": r.port,
            "service_name": r.service_name,
            "username": r.username,
            "password": r.password,
            "monitoring_server": r.monitoring_server,
        })

    return jsonify({"ok": True, "configs": out})

@oracle_bp.get("/api/oracle/monitors")
@require_login_api
def api_oracle_monitors_by_customer():
    cid = request.args.get("customer_id", type=int)
    if not cid:
        return jsonify({"ok": True, "items": []})

    rows = OracleDbMonitor.query.filter_by(customer_id=cid, active=True).order_by(OracleDbMonitor.friendly_name.asc()).all()
    return jsonify({"ok": True, "items": [{
        "id": r.id,
        "friendly_name": r.friendly_name or "",
        "host": r.host,
        "port": r.port,
        "service_name": r.service_name
    } for r in rows]})

