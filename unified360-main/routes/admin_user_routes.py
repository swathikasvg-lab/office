from flask import Blueprint, render_template, jsonify, request
from models.ops_user import Ops_User, Role
from models.customer import Customer
from security import login_required_page, scoped_api, enforce_customer_scope, forbidden, get_current_user
from extensions import db

admin_users_bp = Blueprint("admin_users", __name__)


# ------------------------------------------------------------
# PAGE: Roles Viewer (Read-only)
# ------------------------------------------------------------
@admin_users_bp.route("/admin/roles")
@login_required_page
def roles_page():
    return render_template("administration_roles.html")


# ------------------------------------------------------------
# API: List Roles
# ------------------------------------------------------------
@admin_users_bp.route("/api/admin/roles")
@scoped_api("manage_users")
def api_list_roles():
    roles = Role.query.order_by(Role.name).all()
    return jsonify({
        "ok": True,
        "data": [{"name": r.name, "description": r.description} for r in roles]
    })


# ------------------------------------------------------------
# API: List Customers (tenant-aware)
# ------------------------------------------------------------
@admin_users_bp.route("/api/admin/customers")
@scoped_api("manage_users")
def api_list_customers():
    user = get_current_user()

    query = Customer.query
    if not user.is_admin:
        query = query.filter(Customer.cid == user.customer_id)

    customers = query.order_by(Customer.name).all()
    return jsonify({
        "ok": True,
        "data": [{"id": c.cid, "name": c.name} for c in customers]
    })

# -------------------------------------------------------------------
# PAGE: User Management (Read-only)
# -------------------------------------------------------------------
@admin_users_bp.route("/admin/users")
@login_required_page
def users_page():
    """
    Renders the User Management page.
    Data is fetched via API to avoid embedding logic in templates.
    """
    return render_template("administration_users.html")


# -------------------------------------------------------------------
# API: List Users (Read-only, Tenant Scoped)
# -------------------------------------------------------------------
@admin_users_bp.route("/api/admin/users")
@scoped_api("view_admin")
def api_list_users():
    """
    Returns a tenant-safe list of users.
    No mutation. Read-only.
    """

    query = Ops_User.query
    query = enforce_customer_scope(query, Ops_User)

    users = query.order_by(Ops_User.username.asc()).all()

    data = []
    for u in users:
        data.append({
            "id": u.id,
            "username": u.username,
            "customer": u.customer.name if u.customer else "GLOBAL",
            "is_admin": u.is_admin,
            "is_active": u.is_active,
            "roles": [r.name for r in u.roles],
        })

    return jsonify({"ok": True, "data": data})


@admin_users_bp.route("/api/admin/users/<int:user_id>/toggle", methods=["POST"])
@scoped_api("manage_users")
def toggle_user(user_id):
    user = Ops_User.query.get_or_404(user_id)

    # Safety: prevent self-disable
    from security import get_current_user
    current = get_current_user()
    if current.id == user.id:
        return forbidden("You cannot disable your own account")

    user.is_active = not user.is_active
    db.session.commit()

    return jsonify({
        "ok": True,
        "id": user.id,
        "is_active": user.is_active
    })


#######################################################################
#  EDIT
######################################################################
@admin_users_bp.route("/api/admin/users/<int:user_id>", methods=["PUT"])
@scoped_api("manage_users")
def update_user(user_id):
    user = Ops_User.query.get_or_404(user_id)
    current = get_current_user()

    # Safety: prevent self-demotion from admin
    if current.id == user.id and user.is_admin and not bool(request.json.get("is_admin", True)):
        return forbidden("You cannot remove your own admin access")

    data = request.get_json() or {}

    # Update admin flag
    user.is_admin = bool(data.get("is_admin", user.is_admin))

    # Update role (single role enforced)
    role_name = data.get("role")
    if role_name:
        role = Role.query.filter_by(name=role_name).first()
        if not role:
            return forbidden("Invalid role")
        user.roles = [role]

    db.session.commit()

    return jsonify({"ok": True})


#
# Password Reset
#
@admin_users_bp.route("/api/admin/users/<int:user_id>/reset-password", methods=["POST"])
@scoped_api("manage_users")
def reset_password(user_id):
    user = Ops_User.query.get_or_404(user_id)
    data = request.get_json() or {}
    new_password = data.get("password")

    if not new_password or len(new_password) < 8:
        return forbidden("Password must be at least 8 characters")

    user.set_password(new_password)
    db.session.commit()
    return jsonify({"ok": True})


@admin_users_bp.route("/api/admin/roles/view")
@scoped_api("view_admin")
def api_view_roles():
    roles = Role.query.order_by(Role.name.asc()).all()

    return jsonify({
        "ok": True,
        "data": [
            {
                "name": r.name,
                "description": r.description,
                "permissions": [p.code for p in r.permissions]
            }
            for r in roles
        ]
    })

