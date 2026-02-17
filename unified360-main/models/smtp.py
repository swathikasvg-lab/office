from extensions import db
from datetime import datetime

class SmtpConfig(db.Model):
    __tablename__ = "smtp_config"

    id = db.Column(db.Integer, primary_key=True)

    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    security = db.Column(db.String(10), default="None")

    sender = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(255))
    password = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self, masked=True):
        return {
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "security": self.security,
            "sender": self.sender,
            "username": self.username or "",
            "password": "******" if masked and self.password else "",
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

