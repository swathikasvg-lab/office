from datetime import datetime
from flask import Blueprint, jsonify, render_template, request, session, redirect, url_for
from functools import wraps
from sqlalchemy import or_, desc, nulls_last
from extensions import db
from models.proxy import ProxyServer

proxy_bp = Blueprint("proxy", __name__)


# ============================================================
#  AUTH HELPERS
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


# ============================================================
#  PAGE (all authenticated users)
# ============================================================
@proxy_bp.get("/proxy/servers")
@login_required_page
def proxy_servers_page():
    return render_template("monitoring_proxy.html")


# ============================================================
#  LIST API (authenticated users)
#  NOTE: Proxy servers are global infra, not tenant-bound.
# ============================================================
@proxy_bp.get("/api/proxy-servers")
@login_required_api
def api_proxy_servers_list():
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = ProxyServer.query

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                ProxyServer.ip_address.ilike(like),
                ProxyServer.location.ilike(like),
                ProxyServer.dc_name.ilike(like),
                ProxyServer.geo_hash.ilike(like),
                ProxyServer.capabilities.ilike(like),
            )
        )

    # newest heartbeat first (nulls last)
    query = query.order_by(nulls_last(desc(ProxyServer.last_heartbeat)))

    pag = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "ok": True,
        "items": [x.to_dict() for x in pag.items],
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages,
    })


# ============================================================
#  HEARTBEAT (AGENT API — NO AUTH)
#  Agents call this endpoint periodically to register/update.
# ============================================================
@proxy_bp.post("/api/proxy-server/heartbeat")
def api_proxy_server_heartbeat():
    """
    NO login required — this is called by OpsDuty Agents.

    Upserts the Proxy record + updates last_heartbeat timestamp.
    """
    data = request.get_json(silent=True) or {}
    ip = (data.get("ip_address") or "").strip()

    if not ip:
        return jsonify({"ok": False, "error": "ip_address is required"}), 400

    now = datetime.utcnow()
    obj = ProxyServer.query.filter_by(ip_address=ip).first()

    if not obj:
        obj = ProxyServer(ip_address=ip)
        db.session.add(obj)

    # Optional metadata fields
    obj.location = data.get("location") or obj.location
    obj.dc_name = data.get("dc_name") or obj.dc_name
    obj.geo_hash = data.get("geo_hash") or obj.geo_hash
    obj.capabilities = data.get("capabilities") or obj.capabilities
    obj.last_heartbeat = now

    db.session.commit()

    return jsonify({"ok": True, "item": obj.to_dict()})

