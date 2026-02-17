# routes/contact_routes.py

import re
from flask import Blueprint, request, jsonify, render_template
from functools import wraps
from sqlalchemy import or_
from extensions import db

from models.contact import Contact
from models.customer import Customer

import security

contacts_bp = Blueprint("contacts_bp", __name__)

# Validation regexes
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DIGITS_RE = re.compile(r"^\d{7,15}$")  # digits only, 7–15 (e.g., 917766776677)


# -----------------------
# Page (UI)
# -----------------------
@contacts_bp.get("/administration/contacts")
@security.login_required_page
def contacts_page():
    return render_template("administration_contacts.html")


# -----------------------
# Helpers
# -----------------------
def _validate_contact_payload(payload):
    errors = {}
    display_name = (payload.get("display_name") or "").strip()
    email = (payload.get("email") or "").strip()
    phone = (payload.get("phone") or "").strip()
    customer_id = payload.get("customer_id")

    if not display_name:
        errors["display_name"] = "Display Name is required."
    if not email or not EMAIL_RE.match(email):
        errors["email"] = "Valid Email ID is required."
    if not phone or not DIGITS_RE.match(phone):
        errors["phone"] = "Phone must be digits only (7–15)."
    if not customer_id:
        errors["customer_id"] = "Customer is required."
    else:
        if not Customer.query.get(customer_id):
            errors["customer_id"] = "Invalid customer."

    return errors


# -----------------------
# LIST (Tenant scoped + pagination + search)
# -----------------------
@contacts_bp.get("/api/contacts")
@security.login_required_api
def api_contacts_list():
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)

    query = Contact.query

    # Tenant scoping
    if allowed_cid is not None:
        query = query.filter(Contact.customer_id == allowed_cid)
    else:
        # admin may optionally filter by customer_id
        requested_customer_id = request.args.get("customer_id", type=int)
        if requested_customer_id:
            query = query.filter(Contact.customer_id == requested_customer_id)

    # Search
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Contact.display_name.ilike(like),
            Contact.email.ilike(like)
        ))

    query = query.order_by(Contact.created_at.desc())
    pag = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "items": [c.to_dict() for c in pag.items],
        "page": pag.page,
        "per_page": pag.per_page,
        "total": pag.total,
        "pages": pag.pages or 1,
    })


# -----------------------
# CREATE
# -----------------------
@contacts_bp.post("/api/contacts")
@security.login_required_api
@security.require_permission("contacts.manage")
def api_contacts_create():
    payload = request.get_json(silent=True) or {}

    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)

    # Admin → must provide customer_id
    if allowed_cid is None:
        customer_id = payload.get("customer_id")
        if not customer_id:
            return jsonify({
                "ok": False,
                "errors": {"customer_id": "Customer is required"}
            }), 400
    else:
        # Tenant user → auto-assign
        customer_id = allowed_cid

    # If tenant user, force customer_id to their tenant
    if allowed_cid is not None:
        payload["customer_id"] = allowed_cid

    errors = _validate_contact_payload(payload)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Avoid duplicate email within same customer (optional)
    exists = Contact.query.filter(
        Contact.email == payload["email"],
        Contact.customer_id == payload["customer_id"]
    ).first()
    if exists:
        return jsonify({"ok": False, "errors": {"email": "Contact with this email already exists for the customer."}}), 409

    c = Contact(
        display_name=payload["display_name"].strip(),
        email=payload["email"].strip(),
        phone=payload["phone"].strip(),
        customer_id=payload["customer_id"]
    )
    db.session.add(c)
    db.session.commit()
    return jsonify({"ok": True, "item": c.to_dict()}), 201


# -----------------------
# UPDATE
# -----------------------
@contacts_bp.put("/api/contacts/<int:cid>")
@security.login_required_api
@security.require_permission("contacts.manage")
def api_contacts_update(cid):
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)

    c = Contact.query.get_or_404(cid)

    # Tenant cannot modify other customer's contact
    if allowed_cid is not None and c.customer_id != allowed_cid:
        return security.forbidden("Forbidden")

    payload = request.get_json(silent=True) or {}

    # Prevent tenant reassigning contact to different customer
    if "customer_id" in payload and allowed_cid is not None and payload.get("customer_id") != c.customer_id:
        return security.forbidden("Cannot change customer_id")

    # If tenant, ensure payload uses their cid
    if allowed_cid is not None:
        payload["customer_id"] = allowed_cid

    errors = _validate_contact_payload(payload)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Optional: check duplicate email within same customer except current
    exists = Contact.query.filter(
        Contact.email == payload["email"],
        Contact.customer_id == payload["customer_id"],
        Contact.id != c.id
    ).first()
    if exists:
        return jsonify({"ok": False, "errors": {"email": "Another contact with this email exists for the customer."}}), 409

    c.display_name = payload["display_name"].strip()
    c.email = payload["email"].strip()
    c.phone = payload["phone"].strip()
    # customer_id remains unchanged (or set by admin)
    c.customer_id = payload["customer_id"]

    db.session.commit()
    return jsonify({"ok": True, "item": c.to_dict()})


# -----------------------
# DELETE
# -----------------------
@contacts_bp.delete("/api/contacts/<int:cid>")
@security.login_required_api
@security.require_permission("contacts.manage")
def api_contacts_delete(cid):
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)

    c = Contact.query.get_or_404(cid)

    if allowed_cid is not None and c.customer_id != allowed_cid:
        return security.forbidden("Forbidden")

    db.session.delete(c)
    db.session.commit()
    return jsonify({"ok": True})

