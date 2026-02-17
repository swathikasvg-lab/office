from extensions import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

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
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(255))

    permissions = db.relationship(
        "Permission",
        secondary=role_permissions,
        back_populates="roles",
        lazy="selectin",
    )

    users = db.relationship(
        "OpsUser",
        secondary=user_roles,
        back_populates="roles",
    )


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(128), unique=True, nullable=False)
    description = db.Column(db.String(255))

    roles = db.relationship(
        "Role",
        secondary=role_permissions,
        back_populates="permissions",
    )


class OpsUser(db.Model):
    __tablename__ = "ops_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)

    customer = db.relationship("Customer", lazy="joined")

    roles = db.relationship(
        "Role",
        secondary=user_roles,
        back_populates="users",
        lazy="selectin",
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password, method="scrypt")

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def has_role(self, role_name):
        if self.is_admin:
            return True
        return any(r.name == role_name for r in self.roles)

    def has_permission(self, perm_code):
        if self.is_admin:
            return True
        return any(
            perm.code == perm_code
            for r in self.roles
            for perm in r.permissions
        )

    def to_session(self):
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

