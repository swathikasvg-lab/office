from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session
from functools import wraps
import re
import smtplib
from email.message import EmailMessage

from extensions import db
from models.smtp import SmtpConfig

smtp_bp = Blueprint("smtp", __name__)

# ============================================================
# AUTH HELPERS (Standardized)
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
    """
    SMTP configuration is a global infrastructure setting.
    Only Admins should be able to view or modify it.
    """
    @wraps(fn)
    def wrapper(*a, **kw):
        user = _current_user()
        if not user or not user.get("is_admin"):
            return jsonify({"ok": False, "error": "Forbidden – Admin access required"}), 403
        return fn(*a, **kw)
    return wrapper


# ============================================================
# PAGE
# ============================================================
@smtp_bp.route("/smtp/config")
@login_required_page
def smtp_config_page():
    user = _current_user()
    if not user or not user.get("is_admin"):
        return redirect(url_for("dashboard.dashboard_enterprise"))
    return render_template("smtp_config.html")


# ============================================================
# VALIDATION
# ============================================================
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_payload(data: dict):
    errors = {}
    host = (data.get("host") or "").strip()
    port = data.get("port", 25)
    security = (data.get("security") or "None").strip()
    sender = (data.get("sender") or "").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not host:
        errors["host"] = "SMTP host is required"

    try:
        port = int(port)
        if not (1 <= port <= 65535):
            raise ValueError()
    except Exception:
        errors["port"] = "Port must be 1–65535"

    if security not in ("None", "SSL", "TLS"):
        errors["security"] = "Security must be None, SSL, or TLS"

    if not sender or not EMAIL_RE.match(sender):
        errors["sender"] = "Valid sender email is required"

    return errors, host, port, security, sender, username, password


def _get_single_config():
    return SmtpConfig.query.order_by(SmtpConfig.id.asc()).first()


# ============================================================
# API: GET (Admin Only)
# ============================================================
@smtp_bp.get("/api/smtp-configs")
@login_required_api
@admin_required_api
def api_smtp_get():
    cfg = _get_single_config()
    return jsonify({"item": cfg.to_dict(masked=True) if cfg else None})


# ============================================================
# API: CREATE (Single Instance Only)
# ============================================================
@smtp_bp.post("/api/smtp-configs")
@login_required_api
@admin_required_api
def api_smtp_create():
    if _get_single_config():
        return jsonify({
            "ok": False,
            "errors": {"global": "SMTP configuration already exists"}
        }), 409

    data = request.get_json(silent=True) or {}
    errors, host, port, security, sender, username, password = _validate_payload(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    cfg = SmtpConfig(
        host=host,
        port=port,
        security=security,
        sender=sender,
        username=username or None,
        password=password or None
    )

    db.session.add(cfg)
    db.session.commit()

    return jsonify({"ok": True, "item": cfg.to_dict(masked=True)}), 201


# ============================================================
# API: UPDATE EXISTING (Admin Only)
# ============================================================
@smtp_bp.put("/api/smtp-configs/<int:item_id>")
@login_required_api
@admin_required_api
def api_smtp_update(item_id):
    cfg = SmtpConfig.query.get_or_404(item_id)

    data = request.get_json(silent=True) or {}
    errors, host, port, security, sender, username, password = _validate_payload(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    cfg.host = host
    cfg.port = port
    cfg.security = security
    cfg.sender = sender
    cfg.username = username or None
    cfg.password = password or None  # Explicit override allowed

    db.session.commit()
    return jsonify({"ok": True, "item": cfg.to_dict(masked=True)})


# ============================================================
# API: DELETE (Admin Only)
# ============================================================
@smtp_bp.delete("/api/smtp-configs/<int:item_id>")
@login_required_api
@admin_required_api
def api_smtp_delete(item_id):
    cfg = SmtpConfig.query.get_or_404(item_id)
    db.session.delete(cfg)
    db.session.commit()
    return jsonify({"ok": True})


# ============================================================
# API: TEST EMAIL (Admin Only)
# ============================================================
@smtp_bp.post("/api/smtp-configs/test")
@login_required_api
@admin_required_api
def api_smtp_test():
    cfg = _get_single_config()
    if not cfg:
        return jsonify({"ok": False, "error": "No SMTP configuration set"}), 400

    data = request.get_json(silent=True) or {}
    recipient = (data.get("recipient") or "").strip()

    if not recipient or not EMAIL_RE.match(recipient):
        return jsonify({"ok": False, "error": "Provide a valid recipient email"}), 400

    try:
        msg = EmailMessage()
        msg["Subject"] = "SMTP Test - Autointelli"
        msg["From"] = cfg.sender
        msg["To"] = recipient
        msg.set_content("This is a test email from Autointelli SMTP configuration.")

        if cfg.security == "SSL":
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=15) as smtp:
                if cfg.username:
                    smtp.login(cfg.username, cfg.password or "")
                smtp.send_message(msg)

        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=15) as smtp:
                if cfg.security == "TLS":
                    smtp.starttls()
                if cfg.username:
                    smtp.login(cfg.username, cfg.password or "")
                smtp.send_message(msg)

        return jsonify({"ok": True, "message": f"Test email sent to {recipient}"})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

