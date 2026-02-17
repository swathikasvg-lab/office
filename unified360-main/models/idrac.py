from extensions import db
from datetime import datetime

class IdracConfig(db.Model):
    __tablename__ = "idrac_configs"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    device_ip = db.Column(db.String(45), unique=True, nullable=False)
    snmp_version = db.Column(db.String(10), default="v2c")
    community = db.Column(db.String(128))
    port = db.Column(db.Integer, default=161)
    monitoring_server = db.Column(db.String(255), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship("Customer", lazy="joined")

    def to_dict(self, masked=True):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else "",
            "device_ip": self.device_ip,
            "snmp_version": self.snmp_version,
            "community": "••••••" if masked else self.community,
            "monitoring_server": self.monitoring_server,
            "port": self.port,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

