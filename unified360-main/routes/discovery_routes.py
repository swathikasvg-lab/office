# routes/discovery_routes.py
from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    session,
)
from functools import wraps
from datetime import datetime

from extensions import db
from models.discovery import DiscoveredAsset, DiscoveryJob
from models.proxy import ProxyServer

discovery_bp = Blueprint("discovery", __name__)

# -------------------------------------------------------------------
# Auth helpers (same pattern as other route files)
# -------------------------------------------------------------------
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


# -------------------------------------------------------------------
# PAGE: Discovery UI
# -------------------------------------------------------------------
@discovery_bp.route("/tools/discovery")
@login_required_page
def discovery_page():
    """
    Render the Discovery UI.
    Monitoring server dropdown will be populated on the client
    by calling /api/proxy-servers (same pattern as SNMP page).
    """
    return render_template("discovery.html")

#Delete
@discovery_bp.delete("/api/discovery-jobs/<int:job_id>")
@login_required_api
def api_discovery_delete(job_id):
    job = DiscoveryJob.query.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Not found"}), 404

    db.session.delete(job)
    db.session.commit()
    return jsonify({"ok": True})

#Trigger
@discovery_bp.post("/api/discovery-jobs/<int:job_id>/trigger")
@login_required_api
def api_discovery_trigger(job_id):
    job = DiscoveryJob.query.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Not found"}), 404

    # Update status so proxy picks it up next sync
    job.status = "pending"
    job.last_error = None
    db.session.commit()

    return jsonify({"ok": True, "message": "Discovery triggered"})


# -------------------------------------------------------------------
# API: List discovered assets (for Discovery UI table)
# -------------------------------------------------------------------
@discovery_bp.get("/api/discovery-assets")
@login_required_api
def api_discovery_assets():
    """
    List discovered assets with search + pagination.
    """
    q_text = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = DiscoveredAsset.query

    if q_text:
        like = f"%{q_text}%"
        query = query.filter(
            db.or_(
                DiscoveredAsset.ip_address.ilike(like),
                DiscoveredAsset.hostname.ilike(like),
                DiscoveredAsset.vendor.ilike(like),
                DiscoveredAsset.model.ilike(like),
                DiscoveredAsset.device_type.ilike(like),
            )
        )

    query = query.order_by(DiscoveredAsset.first_seen.desc())
    pag = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify(
        {
            "ok": True,
            "items": [x.to_dict() for x in pag.items],
            "page": pag.page,
            "per_page": pag.per_page,
            "total": pag.total,
            "pages": pag.pages,
        }
    )


# -------------------------------------------------------------------
# API: Create a Discovery job (called from Discovery UI)
# -------------------------------------------------------------------
@discovery_bp.post("/api/discovery-jobs")
@login_required_api
def api_discovery_create_job():
    """
    Create a new discovery job that will be picked up by a proxy server.

    Expected JSON:
    {
      "friendly_name": "Core Switches - DC1",
      "monitoring_server": "10.10.10.11",   # proxy IP
      "ip_range": "10.10.10.1-10.10.10.254",  # IP Range Format = D (as per your choice)
      "snmp_version": "v2c" | "v3",
      "community": "...",                    # for v2c
      "v3_username": "...",                  # for v3
      "v3_auth_protocol": "MD5" | "SHA",
      "v3_auth_password": "...",
      "v3_priv_protocol": "AES" | "DES",
      "v3_priv_password": "..."
    }
    """
    data = request.get_json(silent=True) or {}
    errors = {}

    name = (data.get("name") or "").strip()
    monitoring_server = (data.get("monitoring_server") or "").strip()
    ip_range = (data.get("ip_range") or "").strip()
    snmp_version = (data.get("snmp_version") or "v2c").lower()

    community = (data.get("community") or "").strip()

    v3_username = (data.get("v3_username") or "").strip()
    v3_auth_protocol = (data.get("v3_auth_protocol") or "").upper()
    v3_auth_password = (data.get("v3_auth_password") or "").strip()
    v3_priv_protocol = (data.get("v3_priv_protocol") or "").upper()
    v3_priv_password = (data.get("v3_priv_password") or "").strip()

    # --- basic validation ---
    if not name:
        errors["name"] = "Job name is required"

    if not monitoring_server:
        errors["monitoring_server"] = "Monitoring server is required"
    else:
        proxy = ProxyServer.query.filter_by(ip_address=monitoring_server).first()
        if not proxy:
            errors["monitoring_server"] = "Unknown monitoring server"

    if not ip_range:
        errors["ip_range"] = "IP range is required"

    if snmp_version not in ("v2c", "v3"):
        errors["snmp_version"] = "SNMP version must be v2c or v3"

    if snmp_version == "v2c":
        if not community:
            errors["community"] = "Community is required for v2c"
    else:
        if not v3_username:
            errors["v3_username"] = "V3 username is required"
        if v3_auth_protocol not in ("MD5", "SHA"):
            errors["v3_auth_protocol"] = "Auth protocol must be MD5 or SHA"
        if not v3_auth_password:
            errors["v3_auth_password"] = "V3 auth password is required"
        if v3_priv_protocol not in ("AES", "DES"):
            errors["v3_priv_protocol"] = "Privacy protocol must be AES or DES"
        if not v3_priv_password:
            errors["v3_priv_password"] = "V3 privacy password is required"

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    job = DiscoveryJob(
        name=name,
        monitoring_server=monitoring_server,
        ip_range=ip_range,
        snmp_version=snmp_version,
        community=community if snmp_version == "v2c" else None,
        v3_username=v3_username if snmp_version == "v3" else None,
        v3_auth_protocol=v3_auth_protocol if snmp_version == "v3" else None,
        v3_auth_password=v3_auth_password if snmp_version == "v3" else None,
        v3_priv_protocol=v3_priv_protocol if snmp_version == "v3" else None,
        v3_priv_password=v3_priv_password if snmp_version == "v3" else None,
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.session.add(job)
    db.session.commit()

    return jsonify({"ok": True, "job": job.to_dict()}), 201


# -------------------------------------------------------------------
# API: List Discovery jobs (optional for UI later)
# -------------------------------------------------------------------
@discovery_bp.get("/api/discovery-jobs")
@login_required_api
def api_discovery_jobs_list():
    """
    (Optional) List discovery jobs for a quick view in UI.
    For now simple: no pagination needed.
    """
    jobs = DiscoveryJob.query.order_by(DiscoveryJob.created_at.desc()).all()
    return jsonify(
        {
            "ok": True,
            "items": [j.to_dict() for j in jobs],
        }
    )


# -------------------------------------------------------------------
# API (AGENT): Sync discovery jobs for a proxy
# Endpoint to be called from opsduty_agent on proxy
# -------------------------------------------------------------------
@discovery_bp.get("/api/monitoring/sync_discovery")
def api_monitoring_sync_discovery():
    """
    Called by OpsDuty Agent (proxy) to fetch discovery jobs it should run.

    Example (from proxy):
      GET /api/monitoring/sync_discovery?server=10.10.10.11

    Returns only jobs with status 'pending' or 'running' assigned
    to that monitoring_server.
    """
    server = (request.args.get("server") or "").strip()
    if not server:
        return jsonify({"ok": False, "error": "server param required"}), 400

    proxy = ProxyServer.query.filter_by(ip_address=server).first()
    if not proxy:
        return jsonify({"ok": False, "error": "unknown proxy server"}), 404

    jobs = (
        DiscoveryJob.query.filter(
            DiscoveryJob.monitoring_server == server,
            DiscoveryJob.status.in_(["pending", "running"]),
        )
        .order_by(DiscoveryJob.created_at.asc())
        .all()
    )

    payload = []
    for j in jobs:
        payload.append(
            {
                "id": j.id,
                "name": j.name,
                "ip_range": j.ip_range,
                "snmp_version": j.snmp_version,
                "community": j.community,
                "v3_username": j.v3_username,
                "v3_auth_protocol": j.v3_auth_protocol,
                "v3_auth_password": j.v3_auth_password,
                "v3_priv_protocol": j.v3_priv_protocol,
                "v3_priv_password": j.v3_priv_password,
            }
        )
        # optimistically mark as running
        if j.status == "pending":
            j.status = "running"
            j.updated_at = datetime.utcnow()

    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "server": server,
            "count": len(payload),
            "items": payload,
            "server_time": datetime.utcnow().isoformat() + "Z",
        }
    )


# -------------------------------------------------------------------
# API (AGENT): Post discovery results from proxy -> NMS
# -------------------------------------------------------------------
@discovery_bp.post("/api/discovery/report")
def api_discovery_report():
    data = request.get_json(silent=True) or {}

    server = (data.get("server") or "").strip()
    job_id = data.get("job_id")
    assets = data.get("assets") or []
    job_done = data.get("job_done", False)   # <<< IMPORTANT
    error_text = (data.get("error") or "").strip()

    if not server:
        return jsonify({"ok": False, "error": "server is required"}), 400
    if not job_id:
        return jsonify({"ok": False, "error": "job_id is required"}), 400

    job = DiscoveryJob.query.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "invalid job_id"}), 404

    # Upsert discovered assets
    for a in assets:
        ip = (a.get("ip") or "").strip()
        if not ip:
            continue

        obj = DiscoveredAsset.query.filter_by(ip_address=ip).first()
        if not obj:
            obj = DiscoveredAsset(ip_address=ip)
            db.session.add(obj)

        obj.hostname = a.get("hostname") or obj.hostname
        obj.vendor = a.get("vendor") or obj.vendor
        obj.model = a.get("model") or obj.model
        obj.device_type = a.get("device_type") or obj.device_type
        obj.sys_object_id = a.get("sysObjectID") or obj.sys_object_id
        obj.sys_descr = a.get("sysDescr") or obj.sys_descr
        obj.snmp_version = a.get("snmp_version") or obj.snmp_version
        obj.snmp_reachable = a.get("snmp_reachable", False)
        obj.snmp_last_error = a.get("snmp_last_error")
        obj.last_seen = datetime.utcnow()
        obj.is_active = True

    # ---- Correct Job Status Handling ----
    if job_done:
        job.status = "completed"
    else:
        job.status = "running"

    job.last_run = datetime.utcnow()
    job.last_error = error_text or None
    job.updated_at = datetime.utcnow()

    db.session.commit()

    return jsonify({"ok": True})

