from extensions import db
from datetime import datetime

class SnmpCredential(db.Model):
    __tablename__ = "snmp_credentials"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

    snmp_version = db.Column(db.String(10), nullable=False)  # v2c / v3

    # v2c fields
    community = db.Column(db.String(128))

    # v3 fields
    username = db.Column(db.String(255))
    auth_protocol = db.Column(db.String(16))    # MD5 / SHA
    auth_password = db.Column(db.String(255))
    priv_protocol = db.Column(db.String(16))    # AES / DES
    priv_password = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

