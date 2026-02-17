from flask import Blueprint, request, jsonify, render_template
from sqlalchemy import or_
from extensions import db

from models.contact import Contact, ContactGroup
from models.customer import Customer

import security

contact_groups_bp = Blueprint("contact_groups_bp", __name__)


# ------------------------------------------------------------
# PAGE
# ------------------------------------------------------------
@contact_groups_bp.get("/administration/contact-groups")
@security.login_required_page
def contact_groups_page():
    return render_template("administration_contact_groups.html")


# ------------------------------------------------------------
# LIST GROUPS (TENANT SCOPED + ADMIN OVERRIDE)
# ------------------------------------------------------------
@contact_groups_bp.get("/api/contact-groups")
@security.login_required_api
def api_group_list():
    user = security.get_current_user()

    q = (request.args.get("q") or "").strip()
    requested_customer_id = request.args.get("customer_id", type=int)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = ContactGroup.query.join(Customer).order_by(ContactGroup.created_at.desc())

    allowed_cid = security.get_allowed_customer_id(user)

    if allowed_cid is None:
        # unrestricted admin
        if requested_customer_id:
            query = query.filter(ContactGroup.customer_id == requested_customer_id)
    else:
        # tenant forced to their own customer
        query = query.filter(ContactGroup.customer_id == allowed_cid)

    if q:
        like = f"%{q}%"
        query = query.filter(or_(ContactGroup.name.ilike(like),
                                 ContactGroup.description.ilike(like)))

    pag = query.paginate(page=page, per_page=per_page, error_out=False)

    def serialize_group(g: ContactGroup):
        return {
            "id": g.id,
            "customer_id": g.customer_id,
            "customer_name": g.customer.name if g.customer else None,
            "name": g.name,
            "description": g.description,
            "members_count": len(g.contacts),
            "contacts": [{"id": c.id, "display_name": c.display_name} for c in g.contacts],
        }

    return jsonify({
        "items": [serialize_group(g) for g in pag.items],
        "page": pag.page,
        "pages": pag.pages or 1,
        "total": pag.total,
        "per_page": pag.per_page,
    })


# ------------------------------------------------------------
# GET SINGLE (TENANT SCOPED)
# ------------------------------------------------------------
@contact_groups_bp.get("/api/contact-groups/<int:gid>")
@security.login_required_api
def api_group_get(gid):
    user = security.get_current_user()

    g = ContactGroup.query.get_or_404(gid)

    allowed_cid = security.get_allowed_customer_id(user)
    if allowed_cid is not None and g.customer_id != allowed_cid:
        return security.forbidden("Forbidden")

    return jsonify({
        "ok": True,
        "item": {
            "id": g.id,
            "customer_id": g.customer_id,
            "name": g.name,
            "description": g.description,
            "contacts": [c.id for c in g.contacts],
        }
    })


# ------------------------------------------------------------
# CREATE GROUP (TENANT SCOPED)
# ------------------------------------------------------------
@contact_groups_bp.post("/api/contact-groups")
@security.login_required_api
@security.require_permission("contacts.manage")
def api_group_create():
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)

    payload = request.get_json(silent=True) or {}

    customer_id = payload.get("customer_id")
    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()
    contact_ids = payload.get("contacts") or []

    # Tenant enforcement
    if allowed_cid is not None and customer_id != allowed_cid:
        return security.forbidden("Unauthorized customer assignment")

    errors = {}
    if not customer_id:
        errors["customer_id"] = "Customer is required."
    if not name:
        errors["name"] = "Group name is required."

    customer = Customer.query.get(customer_id)
    if not customer:
        errors["customer_id"] = "Invalid customer."

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Contacts MUST belong to same customer
    contacts = []
    if contact_ids:
        contacts = Contact.query.filter(
            Contact.id.in_(contact_ids),
            Contact.customer_id == customer_id
        ).all()

    g = ContactGroup(
        customer_id=customer_id,
        name=name,
        description=description,
    )
    g.contacts = contacts

    db.session.add(g)
    db.session.commit()

    return jsonify({"ok": True, "item": {"id": g.id}}), 201


# ------------------------------------------------------------
# UPDATE GROUP (TENANT SCOPED)
# ------------------------------------------------------------
@contact_groups_bp.put("/api/contact-groups/<int:gid>")
@security.login_required_api
@security.require_permission("contacts.manage")
def api_group_update(gid):
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)

    g = ContactGroup.query.get_or_404(gid)
    payload = request.get_json(silent=True) or {}

    customer_id = payload.get("customer_id")
    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()
    contact_ids = payload.get("contacts") or []

    # Tenant cannot modify another customer's group
    if allowed_cid is not None and g.customer_id != allowed_cid:
        return security.forbidden("Forbidden")

    # Tenant cannot reassign customer_id
    if allowed_cid is not None and customer_id != g.customer_id:
        return security.forbidden("Cannot change customer_id")

    errors = {}
    if not customer_id:
        errors["customer_id"] = "Customer is required."
    if not name:
        errors["name"] = "Group name is required."

    customer = Customer.query.get(customer_id)
    if not customer:
        errors["customer_id"] = "Invalid customer."

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Update fields
    g.name = name
    g.description = description

    # Contacts MUST belong to same customer
    contacts = []
    if isinstance(contact_ids, list) and contact_ids:
        contacts = Contact.query.filter(
            Contact.id.in_(contact_ids),
            Contact.customer_id == customer_id
        ).all()

    g.contacts = contacts

    db.session.commit()
    return jsonify({"ok": True, "item": {"id": g.id}})


# ------------------------------------------------------------
# DELETE GROUP (TENANT SCOPED)
# ------------------------------------------------------------
@contact_groups_bp.delete("/api/contact-groups/<int:gid>")
@security.login_required_api
@security.require_permission("contacts.manage")
def api_group_delete(gid):
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)

    g = ContactGroup.query.get_or_404(gid)

    if allowed_cid is not None and g.customer_id != allowed_cid:
        return security.forbidden("Forbidden")

    db.session.delete(g)
    db.session.commit()

    return jsonify({"ok": True})

