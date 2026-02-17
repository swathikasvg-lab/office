from extensions import db
from datetime import datetime


class DeviceUpDownRule(db.Model):
    __tablename__ = "device_updown_rules"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source = db.Column(
        db.String(16),
        nullable=False,
        index=True,  # snmp | server
    )

    device = db.Column(
        db.String(255),
        nullable=False,
        index=True,  # hostname / instance
    )

    contact_group_id = db.Column(
        db.Integer,
        db.ForeignKey("contact_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    is_enabled = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    customer = db.relationship("Customer", lazy="joined")
    contact_group = db.relationship("ContactGroup", lazy="joined")

    __table_args__ = (
        db.UniqueConstraint(
            "customer_id",
            "source",
            "device",
            name="uq_device_updown_rule_per_customer",
        ),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "source": self.source,
            "device": self.device,
            "contact_group_id": self.contact_group_id,
            "contact_group_name": (
                self.contact_group.name if self.contact_group else ""
            ),
            "is_enabled": self.is_enabled,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

