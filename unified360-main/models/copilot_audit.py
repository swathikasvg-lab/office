from datetime import datetime

from extensions import db


class CopilotAuditLog(db.Model):
    __tablename__ = "copilot_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    username = db.Column(db.String(80), nullable=True, index=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default="ok", index=True)
    query_text = db.Column(db.Text, nullable=True)
    details_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("Ops_User", lazy="joined")
    customer = db.relationship("Customer", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "customer_id": self.customer_id,
            "action": self.action,
            "status": self.status,
            "query_text": self.query_text,
            "details_json": self.details_json or {},
            "created_at": self.created_at.isoformat(),
        }
