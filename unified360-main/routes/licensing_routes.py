from datetime import datetime

from flask import Blueprint, jsonify, request, session, render_template, redirect, url_for

from extensions import db
from models.customer import Customer
from models.license import License, LicenseItem
from services.licensing import get_license_snapshot, MONITORING_TYPES

license_bp = Blueprint("license", __name__)


def _current_user():
    return session.get("user") or {}


def _require_login():
    if "user" not in session:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return None


def _require_admin():
    user = _current_user()
    if not user.get("is_admin"):
        return jsonify({"ok": False, "error": "Forbidden - Admin access required"}), 403
    return None

def _require_superadmin():
    user = _current_user()
    if not user.get("is_superadmin"):
        return jsonify({"ok": False, "error": "Forbidden - SuperAdmin access required"}), 403
    return None

# ------------------------------------------------------------
# Page: License Management (Admin only)
# ------------------------------------------------------------
@license_bp.get("/administration/licenses")
def licenses_page():
    if "user" not in session:
        return redirect(url_for("auth.login"))

    user = _current_user()
    if not user.get("is_superadmin"):
        return ("Forbidden - SuperAdmin access required", 403)

    return render_template("administration_licenses.html")


# ------------------------------------------------------------
# Tenant-safe license status
# ------------------------------------------------------------
@license_bp.get("/api/license/status")
def api_license_status():
    err = _require_login() or _require_superadmin()
    if err:
        return err

    user = _current_user()
    req_customer_id = request.args.get("customer_id", type=int)

    if user.get("is_admin") and req_customer_id:
        customer_id = req_customer_id
    else:
        customer_id = user.get("customer_id")

    if not customer_id:
        return jsonify({"ok": False, "error": "Customer scope missing"}), 400

    snap = get_license_snapshot(customer_id)
    return jsonify({"ok": True, "data": snap})


# ------------------------------------------------------------
# Admin: list licenses
# ------------------------------------------------------------
@license_bp.get("/api/licenses")
def api_license_list():
    err = _require_login() or _require_superadmin()
    if err:
        return err

    customer_id = request.args.get("customer_id", type=int)

    query = License.query
    if customer_id:
        query = query.filter(License.customer_id == customer_id)

    items = query.order_by(License.expires_at.desc()).all()
    return jsonify({"ok": True, "items": [i.to_dict() for i in items]})


# ------------------------------------------------------------
# Admin: create license
# ------------------------------------------------------------
@license_bp.post("/api/licenses")
def api_license_create():
    err = _require_login() or _require_superadmin()
    if err:
        return err

    payload = request.get_json(silent=True) or {}

    try:
        customer_id = int(payload.get("customer_id"))
    except Exception:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    if not Customer.query.get(customer_id):
        return jsonify({"ok": False, "error": "Customer not found"}), 404

    name = (payload.get("name") or "").strip()
    starts_at = payload.get("starts_at")
    expires_at = payload.get("expires_at")
    grace_days = int(payload.get("grace_days") or 30)

    if not expires_at:
        return jsonify({"ok": False, "error": "expires_at is required"}), 400

    try:
        starts_dt = datetime.fromisoformat(starts_at) if starts_at else datetime.utcnow()
        expires_dt = datetime.fromisoformat(expires_at)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid date format"}), 400

    license_obj = License(
        customer_id=customer_id,
        name=name or None,
        starts_at=starts_dt,
        expires_at=expires_dt,
        grace_days=grace_days,
        status="active",
    )

    items = payload.get("items") or []
    for item in items:
        mtype = (item.get("monitoring_type") or "").strip()
        if mtype not in MONITORING_TYPES:
            return jsonify({"ok": False, "error": f"Unknown monitoring_type: {mtype}"}), 400
        max_count = int(item.get("max_count") or 0)
        license_obj.items.append(
            LicenseItem(monitoring_type=mtype, max_count=max_count)
        )

    db.session.add(license_obj)
    db.session.commit()
    return jsonify({"ok": True, "item": license_obj.to_dict()}), 201


# ------------------------------------------------------------
# Admin: update license
# ------------------------------------------------------------
@license_bp.put("/api/licenses/<int:license_id>")
def api_license_update(license_id: int):
    err = _require_login() or _require_superadmin()
    if err:
        return err

    lic = License.query.get_or_404(license_id)
    payload = request.get_json(silent=True) or {}

    if "name" in payload:
        lic.name = (payload.get("name") or "").strip() or None

    if "starts_at" in payload:
        try:
            lic.starts_at = datetime.fromisoformat(payload["starts_at"])
        except Exception:
            return jsonify({"ok": False, "error": "Invalid starts_at"}), 400

    if "expires_at" in payload:
        try:
            lic.expires_at = datetime.fromisoformat(payload["expires_at"])
        except Exception:
            return jsonify({"ok": False, "error": "Invalid expires_at"}), 400

    if "grace_days" in payload:
        lic.grace_days = int(payload.get("grace_days") or 30)

    if "status" in payload:
        lic.status = (payload.get("status") or "active").strip()

    if "items" in payload:
        lic.items = []
        for item in payload.get("items") or []:
            mtype = (item.get("monitoring_type") or "").strip()
            if mtype not in MONITORING_TYPES:
                return jsonify({"ok": False, "error": f"Unknown monitoring_type: {mtype}"}), 400
            max_count = int(item.get("max_count") or 0)
            lic.items.append(LicenseItem(monitoring_type=mtype, max_count=max_count))

    db.session.commit()
    return jsonify({"ok": True, "item": lic.to_dict()}), 200


# ------------------------------------------------------------
# Admin: delete license
# ------------------------------------------------------------
@license_bp.delete("/api/licenses/<int:license_id>")
def api_license_delete(license_id: int):
    err = _require_login() or _require_superadmin()
    if err:
        return err

    lic = License.query.get_or_404(license_id)
    db.session.delete(lic)
    db.session.commit()
    return jsonify({"ok": True})
