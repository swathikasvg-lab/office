from extensions import db
from datetime import datetime

class PortMonitor(db.Model):
    __tablename__ = "port_monitor"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    friendly_name = db.Column(db.String(150))
    host_ip = db.Column(db.String(64), nullable=False)
    ports = db.Column(db.String(255), nullable=False)
    protocol = db.Column(db.String(10), default="tcp")
    timeout = db.Column(db.Integer, default=5)
    monitoring_server = db.Column(db.String(50), nullable=False)
    active = db.Column(db.Boolean, default=True)

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
            "customer_acct_id": self.customer.acct_id if self.customer else "",
            "friendly_name": self.friendly_name,
            "host_ip": self.host_ip,
            "ports": self.ports,
            "protocol": self.protocol,
            "timeout": self.timeout,
            "monitoring_server": self.monitoring_server,
            "active": self.active,
            "updated_at": self.updated_at.isoformat(),
        }

