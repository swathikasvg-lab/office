from extensions import db
from datetime import datetime

class DiscoveredAsset(db.Model):
    __tablename__ = "discovered_assets"

    id = db.Column(db.Integer, primary_key=True)

    ip_address = db.Column(db.String(64), unique=True, nullable=False)
    hostname = db.Column(db.String(255))
    sys_object_id = db.Column(db.String(255))
    sys_descr = db.Column(db.Text)

    vendor = db.Column(db.String(255))
    model = db.Column(db.String(255))
    device_type = db.Column(db.String(255))

    snmp_version = db.Column(db.String(10), default="v2c")
    community = db.Column(db.String(128))
    v3_username = db.Column(db.String(128))
    v3_auth_protocol = db.Column(db.String(16))
    v3_auth_password = db.Column(db.String(128))
    v3_priv_protocol = db.Column(db.String(16))
    v3_priv_password = db.Column(db.String(128))

    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    is_active = db.Column(db.Boolean, default=True)
    snmp_reachable = db.Column(db.Boolean, default=False)
    snmp_last_error = db.Column(db.String(255))

    def to_dict(self):
        return {
            "id": self.id,
            "ip": self.ip_address,
            "hostname": self.hostname,
            "vendor": self.vendor,
            "model": self.model,
            "device_type": self.device_type,
            "sysObjectID": self.sys_object_id,
            "sysDescr": self.sys_descr,
            "snmp_version": self.snmp_version,
            "snmp_reachable": self.snmp_reachable,
            "snmp_last_error": self.snmp_last_error,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
        }


class DiscoveryJob(db.Model):
    __tablename__ = "discovery_jobs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    monitoring_server = db.Column(db.String(64), nullable=False)
    ip_range = db.Column(db.String(255), nullable=False)

    snmp_version = db.Column(db.String(10), default="v2c")
    community = db.Column(db.String(128))

    v3_username = db.Column(db.String(128))
    v3_auth_protocol = db.Column(db.String(16))
    v3_auth_password = db.Column(db.String(128))
    v3_priv_protocol = db.Column(db.String(16))
    v3_priv_password = db.Column(db.String(128))

    status = db.Column(db.String(32), default="pending")
    last_run = db.Column(db.DateTime)
    last_error = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "monitoring_server": self.monitoring_server,
            "ip_range": self.ip_range,
            "snmp_version": self.snmp_version,
            "status": self.status,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

