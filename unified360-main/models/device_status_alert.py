from extensions import db
from datetime import datetime

class DeviceStatusAlert(db.Model):
    __tablename__ = "device_status_alert"

    id = db.Column(db.Integer, primary_key=True)

    source = db.Column(db.String(16), nullable=False, index=True)  # server | snmp
    device = db.Column(db.String(255), nullable=False, index=True)

    last_status = db.Column(db.String(8), nullable=False, default="UP")
    is_active = db.Column(db.Boolean, default=False)

    last_change = db.Column(db.DateTime, default=datetime.utcnow)
    last_recovered = db.Column(db.DateTime)
    down_since = db.Column(db.DateTime)

    total_downtime_sec = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint("source", "device", name="uq_device_status_source_device"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "source": self.source,
            "device": self.device,
            "last_status": self.last_status,
            "is_active": self.is_active,
            "last_change": self.last_change.isoformat() if self.last_change else None,
            "last_recovered": self.last_recovered.isoformat() if self.last_recovered else None,
            "down_since": self.down_since.isoformat() if self.down_since else None,
            "total_downtime_sec": self.total_downtime_sec,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

