from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from models.customer import Customer


# Association tables for RBAC
user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("ops_users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)

    permissions = db.relationship(
        "Permission",
        secondary=role_permissions,
        back_populates="roles",
        lazy="selectin"
    )

    users = db.relationship(
        "Ops_User",
        secondary=user_roles,
        back_populates="roles"
    )


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(128), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)

    roles = db.relationship(
        "Role",
        secondary=role_permissions,
        back_populates="permissions"
    )


class Ops_User(db.Model):
    __tablename__ = "ops_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    # Customer scoping
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Global Admin flag (multi-tenant MSP user)
    is_admin = db.Column(db.Boolean, default=False)

    # User active/inactive
    is_active = db.Column(db.Boolean, default=True)

    customer = db.relationship("Customer", lazy="joined")

    # RBAC roles assigned to user
    roles = db.relationship(
        "Role",
        secondary=user_roles,
        back_populates="users",
        lazy="selectin",
    )

    # Password helpers
    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password, method="scrypt")

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    # RBAC utility shortcuts
    def has_role(self, role_name: str) -> bool:
        if self.is_admin:
            return True
        return any(r.name == role_name for r in self.roles)

    def has_permission(self, perm_code: str) -> bool:
        if self.is_admin:
            return True
        for role in self.roles:
            for perm in role.permissions:
                if perm.code == perm_code:
                    return True
        return False

    def to_session(self):
        """Used for storing user data in Flask session."""
        return {
            "id": self.id,
            "username": self.username,
            "customer_id": self.customer_id,
            "is_admin": self.is_admin,
            "roles": [r.name for r in self.roles],
        }

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "customer_id": self.customer_id,
            "is_admin": self.is_admin,
            "is_active": self.is_active,
            "roles": [r.name for r in self.roles],
        }

