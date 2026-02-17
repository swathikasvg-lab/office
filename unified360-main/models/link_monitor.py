from extensions import db
from datetime import datetime

class LinkMonitor(db.Model):
    __tablename__ = "link_monitors"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    link_name = db.Column(db.String(120), nullable=False)
    monitoring_server = db.Column(db.String(255), nullable=False)
    ip_address = db.Column(db.String(64), nullable=False)
    if_index = db.Column(db.String(32), nullable=False)

    link_type = db.Column(db.String(16), default="ISP")
    site = db.Column(db.String(120))
    provisioned_bandwidth_mbps = db.Column(db.Integer)

    snmp_version = db.Column(db.String(4), default="2c")
    snmp_community = db.Column(db.String(128))

    snmpv3_sec_level = db.Column(db.String(16))
    snmpv3_username = db.Column(db.String(128))
    snmpv3_auth_protocol = db.Column(db.String(16))
    snmpv3_auth_password = db.Column(db.String(256))
    snmpv3_priv_protocol = db.Column(db.String(16))
    snmpv3_priv_password = db.Column(db.String(256))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")

    def _mask(self, v, masked=True):
        return "••••••" if masked and v else (v or "")

    def to_dict(self, masked=True):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else "",
            "link_name": self.link_name,
            "site": self.site,
            "monitoring_server": self.monitoring_server,
            "ip_address": self.ip_address,
            "if_index": self.if_index,
            "link_type": self.link_type,
            "provisioned_bandwidth_mbps": self.provisioned_bandwidth_mbps,
            "snmp_version": self.snmp_version,
            "snmp_community": self._mask(self.snmp_community, masked),
            "snmpv3_sec_level": self.snmpv3_sec_level or "",
            "snmpv3_username": self.snmpv3_username or "",
            "snmpv3_auth_protocol": self.snmpv3_auth_protocol or "",
            "snmpv3_auth_password": self._mask(self.snmpv3_auth_password, masked),
            "snmpv3_priv_protocol": self.snmpv3_priv_protocol or "",
            "snmpv3_priv_password": self._mask(self.snmpv3_priv_password, masked),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

