from extensions import db
from datetime import datetime

class IloConfig(db.Model):
    __tablename__ = "ilo_configs"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    device_ip = db.Column(db.String(45), unique=True, nullable=False)

    # Restrict SNMP strictly to v2c (explicit, future-safe)
    snmp_version = db.Column(
        db.String(10),
        nullable=False,
        default="v2c"
    )

    community = db.Column(db.String(128), nullable=False)

    port = db.Column(db.Integer, nullable=False, default=161)

    monitoring_server = db.Column(db.String(255), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")

    def to_dict(self, masked=True):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else "",
            "device_ip": self.device_ip,
            "snmp_version": "v2c",  # enforced
            "community": "••••••" if masked else self.community,
            "monitoring_server": self.monitoring_server,
            "port": self.port,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

