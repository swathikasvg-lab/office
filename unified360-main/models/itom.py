from datetime import datetime

from extensions import db


class BusinessApplication(db.Model):
    __tablename__ = "business_applications"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(150), nullable=False, index=True)
    code = db.Column(db.String(64), nullable=True, index=True)
    owner = db.Column(db.String(120), nullable=True)
    tier = db.Column(db.String(32), nullable=True)  # e.g. Tier-1/Tier-2
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")
    services = db.relationship(
        "ApplicationService",
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id", "name", name="uq_business_app_customer_name"
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "name": self.name,
            "code": self.code,
            "owner": self.owner,
            "tier": self.tier,
            "description": self.description or "",
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ApplicationService(db.Model):
    __tablename__ = "application_services"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(
        db.Integer,
        db.ForeignKey("business_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(150), nullable=False, index=True)
    service_type = db.Column(db.String(64), nullable=True)  # api/db/queue/frontend/etc
    criticality = db.Column(db.String(16), default="high")  # low/medium/high/critical
    description = db.Column(db.Text, nullable=True)
    runbook_url = db.Column(db.String(512), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")
    application = db.relationship("BusinessApplication", back_populates="services")
    bindings = db.relationship(
        "ServiceBinding",
        back_populates="service",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    dependencies = db.relationship(
        "ServiceDependency",
        foreign_keys="ServiceDependency.parent_service_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "application_id", "name", name="uq_application_service_name"
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "application_id": self.application_id,
            "customer_id": self.customer_id,
            "name": self.name,
            "service_type": self.service_type,
            "criticality": self.criticality,
            "description": self.description or "",
            "runbook_url": self.runbook_url,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ServiceBinding(db.Model):
    __tablename__ = "service_bindings"

    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(
        db.Integer,
        db.ForeignKey("application_services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    monitor_type = db.Column(db.String(32), nullable=False, index=True)
    monitor_ref = db.Column(db.String(255), nullable=False, index=True)
    display_name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")
    service = db.relationship("ApplicationService", back_populates="bindings")

    __table_args__ = (
        db.UniqueConstraint(
            "service_id",
            "monitor_type",
            "monitor_ref",
            name="uq_service_binding_unique",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "service_id": self.service_id,
            "customer_id": self.customer_id,
            "monitor_type": self.monitor_type,
            "monitor_ref": self.monitor_ref,
            "display_name": self.display_name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ServiceDependency(db.Model):
    __tablename__ = "service_dependencies"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_service_id = db.Column(
        db.Integer,
        db.ForeignKey("application_services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    child_service_id = db.Column(
        db.Integer,
        db.ForeignKey("application_services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dependency_type = db.Column(db.String(16), default="hard")  # hard | soft
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer", lazy="joined")
    parent_service = db.relationship(
        "ApplicationService", foreign_keys=[parent_service_id], lazy="joined"
    )
    child_service = db.relationship(
        "ApplicationService", foreign_keys=[child_service_id], lazy="joined"
    )

    __table_args__ = (
        db.UniqueConstraint(
            "parent_service_id",
            "child_service_id",
            name="uq_service_dependency_unique",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "parent_service_id": self.parent_service_id,
            "child_service_id": self.child_service_id,
            "dependency_type": self.dependency_type,
            "created_at": self.created_at.isoformat(),
        }
