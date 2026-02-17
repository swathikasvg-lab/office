# routes/auth_routes.py

import os

from flask import (
    Blueprint,
    Response,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    current_app
)
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db
from models.ops_user import Ops_User, Role
from models.customer import Customer
from security import login_required_api, require_role

auth_bp = Blueprint("auth", __name__)

SUPERADMIN_USERNAME = os.environ.get("SUPERADMIN_USERNAME")
SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD")


# =====================================================================
# LOGIN
# =====================================================================
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Authenticates the user and stores a full RBAC session payload:
        {
            "id": <int>,
            "username": "...",
            "customer_id": <nullable>,
            "is_admin": bool,
            "roles": ["role1","role2"]
        }
    """
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password")

        if (
            SUPERADMIN_USERNAME
            and SUPERADMIN_PASSWORD
            and username == SUPERADMIN_USERNAME
            and password == SUPERADMIN_PASSWORD
        ):
            session["user"] = {
                "id": None,
                "username": SUPERADMIN_USERNAME,
                "customer_id": None,
                "is_admin": True,
                "is_superadmin": True,
                "roles": ["superadmin"],
            }
            return redirect(url_for("dashboard.dashboard_enterprise"))

        user: Ops_User = Ops_User.query.filter_by(username=username).first()

        if not user or not user.check_password(password):
            flash("Invalid username or password", "danger")
            return render_template("login.html")

        if not user.is_active:
            flash("User account is disabled.", "danger")
            return render_template("login.html")

        # Store properly structured session payload
        session["user"] = user.to_session()

        return redirect(url_for("dashboard.dashboard_enterprise"))

    return render_template("login.html")


@auth_bp.route("/auth/grafana", methods=["GET"])
def auth_grafana():
    #cookie_name = current_app.config.get("SESSION_COOKIE_NAME", "session")
    #print("Cookie header:", request.headers.get("Cookie"))
    #print("session cookie name:", cookie_name)
    #print("session cookie:", request.cookies.get(cookie_name))
    #print("session keys:", list(session.keys()))
    #print("user:", session.get("user"))
    user = session.get("user")  # adjust to your app (maybe dict/object)
    print(user)
    if not user:
        return Response("Unauthorized", status=401)

    username = user.get("username") if isinstance(user, dict) else str(user)

    resp = Response("OK", status=200)
    resp.headers["X-WEBAUTH-USER"] = username
    return resp


# =====================================================================
# LOGOUT
# =====================================================================
@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# =====================================================================
# API: CREATE USER
# =====================================================================
# Only ADMIN or global admin can create users.
@auth_bp.route("/api/users/add", methods=["POST"])
@login_required_api
@require_role("ADMIN")   # Require ADMIN role
def api_add_user():
    """
    Create a new user with optional roles and customer scope.

    Example JSON:
    {
        "username": "john",
        "password": "secret@123",
        "customer_id": 10,         # optional
        "is_admin": false,         # optional; only admins may create admins
        "roles": ["NOC_OPERATOR"]
    }
    """
    data = request.get_json(silent=True) or request.form

    username = (data.get("username") or "").strip()
    password = data.get("password")
    requested_customer_id = data.get("customer_id")
    is_admin = bool(data.get("is_admin", False))
    incoming_roles = data.get("roles", [])

    # ------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------
    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password are required"}), 400

    if Ops_User.query.filter_by(username=username).first():
        return jsonify({"ok": False, "error": "User already exists"}), 409

    # ------------------------------------------------------------
    # Validate customer scoping for non-admin users
    # ------------------------------------------------------------
    if not is_admin:
        if not requested_customer_id:
            return jsonify({"ok": False, "error": "customer_id is required for non-admin users"}), 400

        customer = Customer.query.get(requested_customer_id)
        if not customer:
            return jsonify({"ok": False, "error": "Invalid customer_id"}), 400
    else:
        # Global admin must have customer_id = None
        requested_customer_id = None

    # ------------------------------------------------------------
    # Create new user
    # ------------------------------------------------------------
    user = Ops_User(
        username=username,
        customer_id=requested_customer_id,
        is_admin=is_admin,
        is_active=True,
    )
    user.set_password(password)

    # ------------------------------------------------------------
    # Role Assignment (validated)
    # ------------------------------------------------------------
    if incoming_roles:
        valid_roles = Role.query.filter(Role.name.in_(incoming_roles)).all()
        user.roles = valid_roles

    db.session.add(user)
    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "User created successfully",
        "id": user.id,
        "username": user.username,
        "customer_id": user.customer_id,
        "is_admin": user.is_admin,
        "roles": [r.name for r in user.roles],
    }), 201

