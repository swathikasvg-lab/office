#!/usr/bin/env python3
"""
Autointelli OpsDuty – Bootstrap Script
--------------------------------------

This script initializes:
 - Base RBAC roles
 - Permissions
 - Admin user (global)
 - Optional test users (only when CREATE_TEST_USERS=true)

Safe for production. Idempotent. No data overwritten.
"""

import os
from getpass import getpass

from app import app
from extensions import db
from models.ops_user import Ops_User, Role, Permission
from models.customer import Customer


# ----------------------------------------------------
# Utility helpers
# ----------------------------------------------------
def get_or_create(model, defaults=None, **kwargs):
    """Return existing record or create new one."""
    instance = model.query.filter_by(**kwargs).first()
    if instance:
        return instance, False
    params = dict((defaults or {}), **kwargs)
    instance = model(**params)
    db.session.add(instance)
    db.session.commit()
    return instance, True


# ----------------------------------------------------
# 1. Create Base Roles
# ----------------------------------------------------
def seed_roles():
    roles = [
        ("admin", "Full system administrator"),
        ("noc", "NOC team – manage monitoring"),
        ("readonly", "Dashboard read-only access"),
        ("devops", "Manage backend integrations, systems"),
    ]

    print("\n[+] Seeding roles:")
    for name, desc in roles:
        r, created = get_or_create(Role, name=name, defaults={"description": desc})
        print(f"   - {name} ({'created' if created else 'exists'})")


# ----------------------------------------------------
# 2. Create Base Permissions
# ----------------------------------------------------
def seed_permissions():
    perms = [
        # Core seeded permissions
        ("view_servers", "View server monitoring"),
        ("view_reports", "Access all reports"),
        ("copilot.use", "Use NOC Copilot"),
        ("copilot.run_reports", "Run reports from NOC Copilot"),
        ("copilot.remediate", "Approve or execute Copilot remediation actions"),
        ("edit_snmp", "Manage SNMP devices"),
        ("manage_alerts", "Create/update alert rules"),
        ("edit_contacts", "Manage contacts and groups"),
        ("manage_users", "Create or manage users"),

        # Legacy/route/template compatibility permissions
        ("view_monitoring", "View monitoring module navigation"),
        ("view_idrac", "View iDRAC monitoring"),
        ("view_ilo", "View iLO monitoring"),
        ("view_desktops", "View desktop monitoring"),
        ("view_sqlserver", "View SQL Server monitoring"),
        ("view_oracle", "View Oracle monitoring"),
        ("view_snmp", "View SNMP monitoring"),
        ("view_link", "View link monitoring"),
        ("view_iis", "View IIS monitoring"),
        ("view_ports", "View port monitoring"),
        ("view_ping", "View ping monitoring"),
        ("view_urls", "View URL monitoring"),
        ("view_proxy", "View proxy monitoring"),
        ("view_discovery", "View discovery module"),
        ("view_alerts", "View alert module"),
        ("view_contacts", "View contacts module"),
        ("view_tools", "View tools module"),
        ("view_admin", "View administration module"),
        ("edit_alerts", "Edit alert settings"),
        ("edit_idrac", "Edit iDRAC monitoring"),
        ("edit_ilo", "Edit iLO monitoring"),
        ("edit_link", "Edit link monitoring"),
        ("edit_oracle", "Edit Oracle monitoring"),
        ("edit_ping", "Edit ping monitoring"),
        ("edit_ports", "Edit port monitoring"),
        ("edit_sqlserver", "Edit SQL Server monitoring"),
        ("edit_urls", "Edit URL monitoring"),
        ("contacts.manage", "Legacy contacts management permission"),
        ("alert.manage", "Legacy alert management permission"),
        ("customers.manage", "Legacy customer/user management permission"),
    ]

    print("\n[+] Seeding permissions:")
    for code, desc in perms:
        p, created = get_or_create(Permission, code=code, defaults={"description": desc})
        print(f"   - {code} ({'created' if created else 'exists'})")


# ----------------------------------------------------
# 3. Assign Permissions to Roles
# ----------------------------------------------------
def assign_permissions():
    print("\n[+] Assigning permissions to roles...")

    def add_perms(role_name, perm_codes):
        role = Role.query.filter_by(name=role_name).first()
        if not role:
            print(f"   [ERROR] Role {role_name} missing!")
            return

        for code in perm_codes:
            perm = Permission.query.filter_by(code=code).first()
            if perm and perm not in role.permissions:
                role.permissions.append(perm)
        db.session.commit()

    assign_map = {
        "noc": [
            "view_servers",
            "view_monitoring",
            "view_idrac",
            "view_ilo",
            "view_desktops",
            "view_sqlserver",
            "view_oracle",
            "view_snmp",
            "view_link",
            "view_iis",
            "view_ports",
            "view_ping",
            "view_urls",
            "view_proxy",
            "view_discovery",
            "view_reports",
            "view_alerts",
            "view_contacts",
            "edit_snmp",
            "edit_idrac",
            "edit_ilo",
            "edit_link",
            "edit_oracle",
            "edit_ping",
            "edit_ports",
            "edit_sqlserver",
            "edit_urls",
            "edit_contacts",
            "edit_alerts",
            "manage_alerts",
            "copilot.use",
            "copilot.run_reports",
            "copilot.remediate",
        ],
        "readonly": [
            "view_servers",
            "view_monitoring",
            "view_idrac",
            "view_ilo",
            "view_desktops",
            "view_sqlserver",
            "view_oracle",
            "view_snmp",
            "view_link",
            "view_iis",
            "view_ports",
            "view_ping",
            "view_urls",
            "view_proxy",
            "view_discovery",
            "view_reports",
            "view_alerts",
            "view_contacts",
            "copilot.use",
        ],
        "devops": [
            "view_servers",
            "view_monitoring",
            "view_tools",
            "view_admin",
            "manage_users",
            "copilot.use",
            "copilot.remediate",
            "copilot.run_reports",
        ],
    }

    for role_name, perms in assign_map.items():
        add_perms(role_name, perms)
        print(f"   - Role '{role_name}' updated")


# ----------------------------------------------------
# 4. Create Admin User
# ----------------------------------------------------
def create_admin_user():
    print("\n[+] Checking for Admin user...")

    admin = Ops_User.query.filter_by(username="admin").first()
    if admin:
        print("   - Admin user already exists.")
        return

    # Ask for admin password (or default)
    pwd = os.environ.get("ADMIN_PASSWORD")
    if not pwd:
        print("\nNo ADMIN_PASSWORD env var detected.")
        pwd = getpass("Enter password for admin user (default=Admin@123): ") or "Admin@123"

    admin = Ops_User(
        username="admin",
        customer_id=None,
        is_admin=True,
        is_active=True
    )
    admin.set_password(pwd)
    db.session.add(admin)
    db.session.commit()

    print(f"   - Admin user created with username 'admin'")


# ----------------------------------------------------
# 5. Optional Test Users
# ----------------------------------------------------
def create_test_users():
    print("\n[+] Creating optional test users...")

    # ----- NOC User -----
    noc_role = Role.query.filter_by(name="noc").first()
    if not Ops_User.query.filter_by(username="nocuser").first():
        u = Ops_User(username="nocuser", customer_id=None, is_active=True)
        u.set_password("Noc@123")
        u.roles.append(noc_role)
        db.session.add(u)
        print("   - nocuser / Noc@123 created")

    # ----- Readonly -----
    readonly_role = Role.query.filter_by(name="readonly").first()
    if not Ops_User.query.filter_by(username="readonly").first():
        u = Ops_User(username="readonly", customer_id=None, is_active=True)
        u.set_password("Read@123")
        u.roles.append(readonly_role)
        db.session.add(u)
        print("   - readonly / Read@123 created")

    # ----- Customer-scoped user -----
    cust = Customer.query.first()
    if cust and not Ops_User.query.filter_by(username="cust1").first():
        u = Ops_User(username="cust1", customer_id=cust.cid, is_active=True)
        u.set_password("Cust@123")
        u.roles.append(readonly_role)
        db.session.add(u)
        print(f"   - cust1 / Cust@123 created (customer_id={cust.cid})")

    db.session.commit()


# ----------------------------------------------------
# MASTER RUNNER
# ----------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        print("===================================================")
        print("   AUTOINTERLLI OPERATIONAL SERVER – BOOTSTRAP     ")
        print("===================================================")

        seed_roles()
        seed_permissions()
        assign_permissions()
        create_admin_user()

        create_test_users_enabled = (
            os.environ.get("CREATE_TEST_USERS", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if create_test_users_enabled:
            create_test_users()
        else:
            print("   - Skipping test users (set CREATE_TEST_USERS=true to enable)")

        print("\n[BOOTSTRAP COMPLETED SUCCESSFULLY]")

