from extensions import db
from datetime import datetime
from sqlalchemy.ext.mutable import MutableDict


class AlertRuleState(db.Model):
    __tablename__ = "alert_rule_state"

    id = db.Column(db.Integer, primary_key=True)

    rule_id = db.Column(
        db.Integer,
        db.ForeignKey("alert_rules.id", ondelete="CASCADE"),
        nullable=False,
    )

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


    is_active = db.Column(db.Boolean, default=False)
    consecutive = db.Column(db.Integer, default=0)

    last_triggered = db.Column(db.DateTime)
    last_recovered = db.Column(db.DateTime)

    target_value = db.Column(db.String(255), index=True)
    extended_state = db.Column(MutableDict.as_mutable(db.JSON))
    #extended_state = db.Column(db.JSON)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rule = db.relationship("AlertRule", back_populates="states")

    customer = db.relationship("Customer")

    def to_dict(self):
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "is_active": self.is_active,
            "consecutive": self.consecutive,
            "last_triggered": self.last_triggered.isoformat() if self.last_triggered else None,
            "last_recovered": self.last_recovered.isoformat() if self.last_recovered else None,
            "extended_state": self.extended_state or {},
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "target_value": self.target_value,
        }

