#models/alert_rule.py
from extensions import db
from datetime import datetime

class AlertRule(db.Model):
    __tablename__ = "alert_rules"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = db.Column(db.String(255), nullable=False)
    monitoring_type = db.Column(db.String(50), nullable=False)
    logic_json = db.Column(db.JSON, nullable=False)

    contact_group_id = db.Column(
        db.Integer,
        db.ForeignKey("contact_groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_enabled = db.Column(db.Boolean, default=True)
    evaluation_count = db.Column(db.Integer, default=1)

    # Existing bandwidth fields
    bw_hostname = db.Column(db.String(255))
    bw_interface = db.Column(db.String(255))

    # ✅ NEW: Service Down targets
    svc_instance = db.Column(db.String(255), nullable=True, index=True)

    oracle_monitor_id = db.Column(db.String(64), nullable=True)
    oracle_tablespace = db.Column(db.String(128), nullable=True)  # "__ALL__" or tablespace name


    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer", lazy="joined")
    contact_group = db.relationship("ContactGroup", lazy="joined")

    states = db.relationship(
        "AlertRuleState",
        back_populates="rule",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "name": self.name,
            "monitoring_type": self.monitoring_type,
            "logic": self.logic_json,
            "contact_group": self.contact_group.to_dict() if self.contact_group else None,
            "is_enabled": self.is_enabled,
            "evaluation_count": self.evaluation_count,
            "bw_hostname": self.bw_hostname,
            "bw_interface": self.bw_interface,
            "svc_instance": self.svc_instance,
            "oracle_monitor_id": self.oracle_monitor_id,
            "oracle_tablespace": self.oracle_tablespace,
            "created_at": self.created_at.isoformat(),
        }

