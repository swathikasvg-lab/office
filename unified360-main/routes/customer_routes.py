# routes/customer_routes.py

import re
from flask import Blueprint, request, jsonify, render_template
from sqlalchemy import or_
from extensions import db

from models.customer import Customer
import security

customer_bp = Blueprint("customers", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ----------------------
# Page (Admin UI)
# ----------------------
@customer_bp.get("/administration/customers")
@security.login_required_page
def customers_page():
    """
    Administration page for customers.
    Access control to the page is handled in front-end routing and here via security.login_required_page.
    The actual API endpoints are restricted to admin users only.
    """
    return render_template("administration_customers.html")


# ----------------------
# LIST (Admin + FULL_VIEWER)
# ----------------------
@customer_bp.get("/api/customers")
@security.login_required_api
def api_customers_list():
    """
    Admins and FULL_VIEWER may list all customers.
    Tenant users are forbidden.
    """
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)

    # Tenant users are restricted
    if allowed_cid is not None and not user.has_role("FULL_VIEWER"):
        return security.forbidden("Forbidden")

    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = Customer.query

    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Customer.acct_id.ilike(like),
            Customer.name.ilike(like),
            Customer.email.ilike(like),
        ))

    query = query.order_by(Customer.created_at.desc())
    pag = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "ok": True,
        "items": [c.to_dict() for c in pag.items],
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages or 1,
    })


# ----------------------
# CREATE (Admin only)
# ----------------------
@customer_bp.post("/api/customers")
@security.login_required_api
@security.require_permission("customers.manage")
def api_customer_create():
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None:
        return security.forbidden("Forbidden")

    data = request.get_json(silent=True) or {}

    acct_id = (data.get("acct_id") or "").strip()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()

    errors = {}
    if not acct_id:
        errors["acct_id"] = "Account ID required"
    if not name:
        errors["name"] = "Customer name required"
    if not EMAIL_RE.match(email):
        errors["email"] = "Valid email required"

    if Customer.query.filter_by(acct_id=acct_id).first():
        errors["acct_id"] = "Account ID must be unique"

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    c = Customer(
        acct_id=acct_id,
        name=name,
        email=email,
    )

    db.session.add(c)
    db.session.commit()

    return jsonify({"ok": True, "item": c.to_dict()}), 201


# ----------------------
# UPDATE (Admin only)
# ----------------------
@customer_bp.put("/api/customers/<int:cid>")
@security.login_required_api
@security.require_permission("customers.manage")
def api_customer_update(cid):
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None:
        return security.forbidden("Forbidden")

    c = Customer.query.get_or_404(cid)
    data = request.get_json(silent=True) or {}

    acct_id = (data.get("acct_id") or c.acct_id).strip()
    name = (data.get("name") or c.name).strip()
    email = (data.get("email") or c.email).strip()

    errors = {}
    if not acct_id:
        errors["acct_id"] = "Account ID required"
    if not name:
        errors["name"] = "Customer name required"
    if not EMAIL_RE.match(email):
        errors["email"] = "Valid email required"

    # Unique acct_id check (exclude current)
    existing = Customer.query.filter(Customer.acct_id == acct_id, Customer.cid != cid).first()
    if existing:
        errors["acct_id"] = "Account ID must be unique"

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    c.acct_id = acct_id
    c.name = name
    c.email = email

    db.session.commit()
    return jsonify({"ok": True, "item": c.to_dict()})


# ----------------------
# DELETE (Admin only)
# ----------------------
@customer_bp.delete("/api/customers/<int:cid>")
@security.login_required_api
@security.require_permission("customers.manage")
def api_customer_delete(cid):
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None:
        return security.forbidden("Forbidden")

    c = Customer.query.get_or_404(cid)
    db.session.delete(c)
    db.session.commit()
    return jsonify({"ok": True})

