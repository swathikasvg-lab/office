from extensions import db
from datetime import datetime

class ProxyServer(db.Model):
    __tablename__ = "proxy_servers"

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(64), unique=True, nullable=False)
    location = db.Column(db.String(120))
    dc_name = db.Column(db.String(120))
    geo_hash = db.Column(db.String(120))
    capabilities = db.Column(db.String(255))
    last_heartbeat = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "location": self.location or "",
            "dc_name": self.dc_name or "",
            "geo_hash": self.geo_hash or "",
            "capabilities": self.capabilities or "",
            "last_heartbeat": self.last_heartbeat.isoformat() + "Z" if self.last_heartbeat else None,
            "created_at": self.created_at.isoformat(),
        }

