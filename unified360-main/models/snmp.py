from extensions import db
from datetime import datetime

SNMP_TEMPLATES = (
    "Generic",
    "Fortiweb WAF",
    "Array",
    "Imperva",
    "Palo Alto FW",
    "Arista Switch",
    "Fortigate",
    "Cisco 9000",
    "NVIDIA",
    "Dell SmartFabric",
    "Dell N2048",
    "Cumulus Networks"
)


class SnmpConfig(db.Model):
    __tablename__ = "snmp_configs"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    name = db.Column(db.String(120), nullable=False)
    device_ip = db.Column(db.String(64), unique=True, nullable=False)
    monitoring_server = db.Column(db.String(255), nullable=False)
    snmp_version = db.Column(db.String(10), nullable=False)
    port = db.Column(db.Integer, default=161)
    template = db.Column(db.String(32), default="Generic")

    # v2c
    community = db.Column(db.String(128))

    # v3
    v3_username = db.Column(db.String(128))
    v3_auth_protocol = db.Column(db.String(16))
    v3_auth_password = db.Column(db.String(128))
    v3_priv_protocol = db.Column(db.String(16))
    v3_priv_password = db.Column(db.String(128))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")

    def to_dict(self, masked=True):
        if self.snmp_version == "v2c":
            creds = {"community": ("••••••" if masked else self.community)}
        else:
            creds = {
                "username": self.v3_username or "",
                "auth_protocol": self.v3_auth_protocol or "",
                "priv_protocol": self.v3_priv_protocol or "",
                "auth_password": "••••••" if masked else self.v3_auth_password,
                "priv_password": "••••••" if masked else self.v3_priv_password,
            }

        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else "",
            "name": self.name,
            "device_ip": self.device_ip,
            "monitoring_server": self.monitoring_server,
            "snmp_version": self.snmp_version,
            "port": self.port,
            "template": self.template,
            "creds": creds,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class SnmpCredential(db.Model):
    __tablename__ = "snmp_credentials"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

    snmp_version = db.Column(db.String(10), nullable=False)
    community = db.Column(db.String(128))

    username = db.Column(db.String(255))
    auth_protocol = db.Column(db.String(16))
    auth_password = db.Column(db.String(255))
    priv_protocol = db.Column(db.String(16))
    priv_password = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

