from extensions import db
from datetime import datetime


class OracleDbMonitor(db.Model):
    __tablename__ = "oracle_db_monitor"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    friendly_name = db.Column(db.String(150))

    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False, default=1521)

    # SID / SERVICE / PDB (XE / XEPDB1 etc.)
    service_name = db.Column(db.String(128), nullable=False)

    username = db.Column(db.String(128), nullable=False)
    password = db.Column(db.Text, nullable=False)

    monitoring_server = db.Column(db.String(50), nullable=False)
    active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")

    # ============================================================
    # SERIALIZER (IMPORTANT)
    # ============================================================
    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else "",

            "friendly_name": self.friendly_name,

            "host": self.host,
            "port": self.port,
            "service_name": self.service_name,
            "username": self.username,

            "monitoring_server": self.monitoring_server,
            "active": self.active,

            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_agent_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else "",
    
            "friendly_name": self.friendly_name,
            "host": self.host,
            "port": self.port,
            "service_name": self.service_name,
            "username": self.username,
            "password": self.password,
    
            "monitoring_server": self.monitoring_server,
            "active": self.active,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

