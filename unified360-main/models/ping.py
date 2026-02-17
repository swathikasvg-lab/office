from extensions import db
from datetime import datetime

class PingConfig(db.Model):
    __tablename__ = "ping_configs"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    name = db.Column(db.String(120), nullable=False)
    host = db.Column(db.String(255), unique=True, nullable=False)
    monitoring_server = db.Column(db.String(255), nullable=False)
    timeout = db.Column(db.Integer, default=5)
    packet_count = db.Column(db.Integer, default=3)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else None,
            "name": self.name,
            "host": self.host,
            "monitoring_server": self.monitoring_server,
            "timeout": self.timeout,
            "packet_count": self.packet_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

