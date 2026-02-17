# security.py
from functools import wraps
from flask import session, redirect, url_for, jsonify
from models.ops_user import Ops_User

PERMISSION_ALIASES = {
    # Legacy dotted-style permissions used in old routes/templates.
    "contacts.manage": {"edit_contacts", "manage_users"},
    "alert.manage": {"manage_alerts", "edit_alerts", "view_alerts"},
    "customers.manage": {"manage_users", "view_admin"},

    # User/admin surfaces.
    "manage_users": {"customers.manage", "view_admin"},
    "view_admin": {"manage_users", "customers.manage"},
    "view_tools": {"manage_users", "view_admin"},

    # Alert surfaces.
    "manage_alerts": {"alert.manage", "edit_alerts", "view_alerts"},
    "edit_alerts": {"manage_alerts", "alert.manage"},
    "view_alerts": {"manage_alerts", "alert.manage", "edit_alerts"},

    # Contacts.
    "edit_contacts": {"contacts.manage", "manage_users"},
    "view_contacts": {"edit_contacts", "contacts.manage", "manage_users"},

    # URL route-level permissions.
    "view_urls": {"view_servers", "view_monitoring"},
    "edit_urls": {"manage_alerts", "alert.manage", "edit_snmp"},

    # Monitoring navigation/view permissions used by templates.
    "view_monitoring": {"view_servers"},
    "view_discovery": {"view_servers", "view_monitoring"},
    "view_proxy": {"view_servers", "view_monitoring"},
    "view_idrac": {"view_servers", "view_monitoring"},
    "view_ilo": {"view_servers", "view_monitoring"},
    "view_desktops": {"view_servers", "view_monitoring"},
    "view_sqlserver": {"view_servers", "view_monitoring"},
    "view_oracle": {"view_servers", "view_monitoring"},
    "view_snmp": {"view_servers", "view_monitoring", "edit_snmp"},
    "view_link": {"view_servers", "view_monitoring"},
    "view_iis": {"view_servers", "view_monitoring"},
    "view_ports": {"view_servers", "view_monitoring"},
    "view_ping": {"view_servers", "view_monitoring"},

    # Monitoring edit permissions used by templates.
    "edit_idrac": {"manage_alerts", "alert.manage", "edit_snmp"},
    "edit_ilo": {"manage_alerts", "alert.manage", "edit_snmp"},
    "edit_link": {"manage_alerts", "alert.manage", "edit_snmp"},
    "edit_oracle": {"manage_alerts", "alert.manage", "edit_snmp"},
    "edit_ping": {"manage_alerts", "alert.manage", "edit_snmp"},
    "edit_ports": {"manage_alerts", "alert.manage", "edit_snmp"},
    "edit_sqlserver": {"manage_alerts", "alert.manage", "edit_snmp"},
}


class _SuperAdminUser:
    def __init__(self, username: str):
        self.id = None
        self.username = username
        self.customer_id = None
        self.is_admin = True
        self.is_active = True
        self.roles = ["superadmin"]

    def has_role(self, role_name: str) -> bool:
        return True

    def has_permission(self, perm_code: str) -> bool:
        return True


# ------------------------------------------------------------
# Session & User Helpers
# ------------------------------------------------------------
def get_session_payload():
    """
    Returns the raw session["user"] dictionary.
    Expected format:
    {
        "id": 1,
        "username": "...",
        "customer_id": 10,
        "is_admin": true,
        "roles": ["admin"]
    }
    """
    return session.get("user")


def get_current_user():
    """
    Fetch Ops_User object from session.
    Returns None if not logged in.
    """
    payload = get_session_payload()
    if not payload:
        return None

    if payload.get("is_superadmin"):
        return _SuperAdminUser(payload.get("username") or "superadmin")

    uid = payload.get("id")
    if not uid:
        return None

    return Ops_User.query.get(uid)


# ------------------------------------------------------------
# Login Enforcement
# ------------------------------------------------------------
def login_required_page(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper


def login_required_api(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------
# RBAC Permission/Role Helpers
# ------------------------------------------------------------
def require_role(role_name):
    """
    Requires user to have a specific role.
    Admin users bypass role checks.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()

            if not user:
                return jsonify({"ok": False, "error": "Unauthorized"}), 401

            # Admin override
            if user.is_admin:
                return fn(*args, **kwargs)

            if not user.has_role(role_name):
                return jsonify({"ok": False, "error": f"Forbidden: Missing role '{role_name}'"}), 403

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_permission(perm_code):
    """
    Requires user to have a specific permission.
    Admin users bypass permission checks.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()

            if not user:
                return jsonify({"ok": False, "error": "Unauthorized"}), 401

            # Admin override
            if user.is_admin:
                return fn(*args, **kwargs)

            if not has_permission(user, perm_code):
                return jsonify({"ok": False, "error": f"Forbidden: Missing permission '{perm_code}'"}), 403

            return fn(*args, **kwargs)

        return wrapper

    return decorator


# ------------------------------------------------------------
# Customer / Tenant Scoping Helpers
# ------------------------------------------------------------
def get_allowed_customer_id(user=None):
    """
    Determines which customer_id the user is restricted to.
    Rules:
        - If user.is_admin AND user.customer_id is NULL → unrestricted (returns None)
        - If user.is_admin AND user.customer_id is set → restricted to that customer
        - Regular users always restricted to their customer_id

    Returns:
        customer_id (int) or None for unlimited scope
    """
    if user is None:
        user = get_current_user()
    if not user:
        return None

    # Global admin not tied to customer → unrestricted
    if user.is_admin and user.customer_id is None:
        return None

    return user.customer_id


def has_permission(user, perm_code: str) -> bool:
    """
    Permission checker with backward-compatible alias support.
    """
    if not user:
        return False
    if user.is_admin:
        return True
    if user.has_permission(perm_code):
        return True

    for alias in PERMISSION_ALIASES.get(perm_code, set()):
        if user.has_permission(alias):
            return True
    return False


def enforce_customer_scope(query_or_user, model_cls_or_customer_id=None, column="customer_id"):
    """
    Dual-mode helper:
      1) Query mode:
         enforce_customer_scope(query, Model)
         -> applies tenant filter on Model.customer_id.
      2) Boolean mode:
         enforce_customer_scope(user, resource_customer_id)
         -> returns True/False if user may access that customer resource.

    This keeps compatibility with existing route modules that used this helper
    as an access-check function.
    """
    # Query mode (legacy/original behavior)
    if model_cls_or_customer_id is not None and hasattr(query_or_user, "filter"):
        query = query_or_user
        model_cls = model_cls_or_customer_id

        user = get_current_user()
        cid = get_allowed_customer_id(user)

        if cid is None:
            return query  # unrestricted admin

        col = getattr(model_cls, column)
        return query.filter(col == cid)

    # Boolean mode
    user = query_or_user
    resource_customer_id = model_cls_or_customer_id
    cid = get_allowed_customer_id(user)

    if cid is None:
        return True
    return cid == resource_customer_id


# ------------------------------------------------------------
# Combined decorators (used in many modules)
# ------------------------------------------------------------
def scoped_api(permission_code=None):
    """
    Common decorator combining:
      - login_required_api
      - permission check (required unless explicitly None)
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Login required
            if "user" not in session:
                return jsonify({"ok": False, "error": "Unauthorized"}), 401

            user = get_current_user()
            if not user:
                return jsonify({"ok": False, "error": "Unauthorized"}), 401

            # Permission enforcement
            if permission_code:
                if not has_permission(user, permission_code):
                    return jsonify(
                        {"ok": False, "error": f"Forbidden: Missing permission '{permission_code}'"}
                    ), 403

            return fn(*args, **kwargs)

        return wrapper
    return decorator


# ------------------------------------------------------------
# Utility for consistent error responses
# ------------------------------------------------------------
def forbidden(message="Forbidden"):
    return jsonify({"ok": False, "error": message}), 403


def unauthorized(message="Unauthorized"):
    return jsonify({"ok": False, "error": message}), 401

# ------------------------------------------------------------
# Template & Inline Permission Helper
# ------------------------------------------------------------
def can(perm_code):
    """
    Returns True if current user has the given permission.
    Admin users automatically pass.
    Safe to use in templates and routes.
    """
    user = get_current_user()
    if not user:
        return False
    return has_permission(user, perm_code)

