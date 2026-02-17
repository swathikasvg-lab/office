from datetime import datetime

from extensions import db


class ItomGraphLayout(db.Model):
    __tablename__ = "itom_graph_layout"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(
        db.Integer,
        db.ForeignKey("business_applications.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    layout_json = db.Column(db.JSON, nullable=False, default={})
    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    application = db.relationship("BusinessApplication", lazy="joined")
    customer = db.relationship("Customer", lazy="joined")
    updated_by = db.relationship("Ops_User", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "application_id": self.application_id,
            "customer_id": self.customer_id,
            "layout": self.layout_json or {},
            "updated_by_user_id": self.updated_by_user_id,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
