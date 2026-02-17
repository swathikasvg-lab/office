# routes/url_routes.py
from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session, abort
from functools import wraps
from urllib.parse import urlparse
from ipaddress import ip_address, ip_network
from sqlalchemy import or_
from extensions import db
from models.url_monitor import UrlMonitor
from models.customer import Customer
from services.licensing import can_add_monitor
from flask import abort
from flask import current_app, abort
import security
from security import (
    login_required_page,
    login_required_api,
    require_permission,
)


url_bp = Blueprint("urls", __name__)

# ---------------- SECURITY HELPERS ----------------
_PRIVATE_NETS = [
    ip_network("127.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("169.254.0.0/16"),   # link local
    ip_network("224.0.0.0/4"),      # multicast
]

def _current_user():
    return session.get("user")

def _is_private_ip(host):
    try:
        ip = ip_address(host)
        return any(ip in net for net in _PRIVATE_NETS)
    except Exception:
        return False

def _validate_url(url, require_public=True):
    """
    Validate structure + SSRF restrictions.
    require_public = False for admin override.
    """
    try:
        p = urlparse(url.strip())
        if p.scheme not in ("http", "https"):
            return False, "URL must begin with http:// or https://"
        if not p.netloc:
            return False, "Invalid URL format"

        host = p.hostname
        if not host:
            return False, "Invalid URL host"

        # Admins allowed internal URLs
        user = _current_user()
        is_admin = user.get("is_admin") if user else False

        if require_public and not is_admin:
            if _is_private_ip(host):
                return False, "Monitoring internal/private IP URLs is restricted to admins"

        return True, None
    except Exception as e:
        print(str(e))
        return False, "Invalid URL"

def _coerce_bool(v):
    return str(v).lower() in ("1", "true", "yes", "on")

# ---------------- PAGE ----------------
@url_bp.get("/monitoring/url")
@login_required_page
@require_permission("view_urls")
def monitoring_url():
    return render_template("monitoring_url.html")

# ---------------- LIST ----------------
@url_bp.get("/api/url-monitors")
@login_required_api
@require_permission("view_urls")
def api_url_list():
    q = (request.args.get("q") or "").strip()
    customer_id = request.args.get("customer_id", type=int)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = UrlMonitor.query

    if customer_id:
        query = query.filter(UrlMonitor.customer_id == customer_id)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                UrlMonitor.name.ilike(like),
                UrlMonitor.url.ilike(like),
                UrlMonitor.monitoring_server.ilike(like),
            )
        )

    pag = query.order_by(UrlMonitor.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "items": [x.to_dict() for x in pag.items],
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages or 1
    })

# ---------------- CREATE ----------------
@url_bp.post("/api/url-monitors")
@login_required_api
@require_permission("edit_urls")
def api_url_create():
    d = request.get_json(silent=True) or {}
    errors = {}

    # 1. Customer validation
    customer_id = d.get("customer_id")
    if not Customer.query.get(customer_id):
        errors["customer_id"] = "Valid customer is mandatory."

    # 2. URL validation + SSRF check
    raw_url = (d.get("url") or "").strip()
    ok, err = _validate_url(raw_url)
    if not ok:
        errors["url"] = err

    # 3. Duplicate check
    if UrlMonitor.query.filter_by(url=raw_url, customer_id=customer_id).first():
        errors["url"] = "URL already exists for this customer."

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    allowed, lic = can_add_monitor(customer_id, "url")
    if not allowed:
        return jsonify({"ok": False, "error": lic.get("message"), "license": lic}), 403

    obj = UrlMonitor(
        customer_id=customer_id,
        name=(d.get("name") or "").strip(),
        monitoring_server=(d.get("monitoring_server") or "").strip(),
        url=raw_url,
        http_method=(d.get("http_method") or "GET").upper(),
        timeout=int(d.get("timeout", 5)),
        expected_status_code=d.get("expected_status_code"),
        response_string_match=(d.get("response_string_match") or "").strip() or None,
        follow_redirects=_coerce_bool(d.get("follow_redirects")),
        check_cert_expiry=_coerce_bool(d.get("check_cert_expiry")),
        username=(d.get("username") or "").strip(),
        password=(d.get("password") or "").strip(),
        request_body=(d.get("request_body") or "").strip()[:5000],  # limit for safety
        content_type=(d.get("content_type") or "application/json")[:200],
    )

    db.session.add(obj)
    db.session.commit()

    return jsonify({"ok": True, "item": obj.to_dict()}), 201

# ---------------- GET ----------------
@url_bp.get("/api/url-monitors/<int:item_id>")
@login_required_api
@require_permission("view_urls")
def api_url_get(item_id):
    return jsonify({"ok": True, "item": UrlMonitor.query.get_or_404(item_id).to_dict()})

# ---------------- UPDATE ----------------
@url_bp.put("/api/url-monitors/<int:item_id>")
@login_required_api
@require_permission("edit_urls")
def api_url_update(item_id):
    item = UrlMonitor.query.get_or_404(item_id)
    d = request.get_json(silent=True) or {}

    # 1. Customer
    customer_id = d.get("customer_id")
    if not Customer.query.get(customer_id):
        return jsonify({"ok": False, "errors": {"customer_id": "Invalid customer"}}), 400

    # 2. URL validation
    raw_url = (d.get("url") or "").strip()
    ok, err = _validate_url(raw_url)
    if not ok:
        return jsonify({"ok": False, "errors": {"url": err}}), 400

    # 3. Duplicate URL validation (ignore self)
    exists = UrlMonitor.query.filter(
        UrlMonitor.id != item_id,
        UrlMonitor.url == raw_url,
        UrlMonitor.customer_id == customer_id
    ).first()
    if exists:
        return jsonify({"ok": False, "errors": {"url": "URL already exists for this customer"}}), 409

    # 4. Update
    item.customer_id = customer_id
    item.name = (d.get("name") or "").strip()
    item.monitoring_server = (d.get("monitoring_server") or "").strip()
    item.url = raw_url
    item.http_method = (d.get("http_method") or "GET").upper()
    item.timeout = int(d.get("timeout", item.timeout))
    item.expected_status_code = d.get("expected_status_code")
    item.response_string_match = (d.get("response_string_match") or "").strip() or None
    item.follow_redirects = _coerce_bool(d.get("follow_redirects"))
    item.check_cert_expiry = _coerce_bool(d.get("check_cert_expiry"))

    db.session.commit()

    return jsonify({"ok": True, "item": item.to_dict()})

# ---------------- DELETE ----------------
@url_bp.delete("/api/url-monitors/<int:item_id>")
@login_required_api
@require_permission("edit_urls")
def api_url_delete(item_id):
    db.session.delete(UrlMonitor.query.get_or_404(item_id))
    db.session.commit()
    return jsonify({"ok": True})

# ---------------- GRAFANA DASHBOARD (POC) ----------------
@url_bp.get("/monitoring/url/<int:item_id>/dashboard")
@login_required_page
def open_url_dashboard(item_id):
    monitor = UrlMonitor.query.get_or_404(item_id)

    user = _current_user()
    is_admin = user.get("is_admin", False)

    # Customer isolation
    if not is_admin and monitor.customer_id != user.get("customer_id"):
        abort(403)

    customer = Customer.query.get(monitor.customer_id)
    if not customer:
        abort(404)


    grafana_base = current_app.config["GRAFANA_BASE_URL"]
    dashboard_uid = current_app.config["GRAFANA_URL_DASHBOARD_UID"]
    
    grafana_url = (
        f"{grafana_base}/d/{dashboard_uid}"
        f"?var-customer={customer.name}"
        f"&var-url={monitor.url}&kiosk"
    )

    print(grafana_url)

    response = redirect(grafana_url)

    # Header consumed by Nginx → Grafana Auth Proxy
    response.headers["X-Autointelli-User"] = user.get("username", "autointelli")

    return response

