from datetime import datetime

from extensions import db


class Runbook(db.Model):
    __tablename__ = "runbooks"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(180), nullable=False, index=True)
    trigger_type = db.Column(db.String(64), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    steps_json = db.Column(db.JSON, nullable=False, default=[])
    risk_level = db.Column(db.String(16), nullable=False, default="medium")
    requires_approval = db.Column(db.Boolean, nullable=False, default=True)
    allowed_roles_json = db.Column(db.JSON, nullable=False, default=[])
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    created_by = db.relationship("Ops_User", foreign_keys=[created_by_user_id], lazy="joined")
    updated_by = db.relationship("Ops_User", foreign_keys=[updated_by_user_id], lazy="joined")

    actions = db.relationship(
        "RemediationAction",
        back_populates="runbook",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "name": self.name,
            "trigger_type": self.trigger_type,
            "description": self.description or "",
            "steps": self.steps_json or [],
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "allowed_roles": self.allowed_roles_json or [],
            "is_active": self.is_active,
            "created_by_user_id": self.created_by_user_id,
            "updated_by_user_id": self.updated_by_user_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class RemediationAction(db.Model):
    __tablename__ = "remediation_actions"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    runbook_id = db.Column(
        db.Integer,
        db.ForeignKey("runbooks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_type = db.Column(db.String(64), nullable=False, index=True)
    source_ref = db.Column(db.String(255), nullable=True, index=True)
    summary = db.Column(db.Text, nullable=True)
    proposed_steps_json = db.Column(db.JSON, nullable=False, default=[])
    status = db.Column(db.String(24), nullable=False, default="proposed", index=True)
    requires_approval = db.Column(db.Boolean, nullable=False, default=True)
    requested_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    executed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    output_json = db.Column(db.JSON, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    executed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    runbook = db.relationship("Runbook", back_populates="actions", lazy="joined")
    requested_by = db.relationship("Ops_User", foreign_keys=[requested_by_user_id], lazy="joined")
    approved_by = db.relationship("Ops_User", foreign_keys=[approved_by_user_id], lazy="joined")
    executed_by = db.relationship("Ops_User", foreign_keys=[executed_by_user_id], lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "runbook_id": self.runbook_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "summary": self.summary or "",
            "proposed_steps": self.proposed_steps_json or [],
            "status": self.status,
            "requires_approval": self.requires_approval,
            "requested_by_user_id": self.requested_by_user_id,
            "approved_by_user_id": self.approved_by_user_id,
            "executed_by_user_id": self.executed_by_user_id,
            "output": self.output_json or {},
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
