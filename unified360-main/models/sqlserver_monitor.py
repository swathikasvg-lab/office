#models/sqlserver_monitor.py
from extensions import db
from datetime import datetime
import os
import base64

# ============================================================
# Password encryption helper
# - Preferred: cryptography.Fernet (strong)
# - Fallback: base64 obfuscation (NOT secure) to avoid runtime breaks
#   -> Install cryptography for production:
#      pip install cryptography
# - Set env var: NMS_SECRET_KEY (recommended)
# ============================================================

def _get_secret_key_bytes() -> bytes:
    # Use a stable secret from environment. Final fallback is host-specific to avoid
    # a universal hardcoded key across deployments.
    raw = (
        os.environ.get("NMS_SECRET_KEY")
        or os.environ.get("SECRET_KEY")
        or os.environ.get("FLASK_SECRET_KEY")
        or f"{os.environ.get('COMPUTERNAME', 'local')}-nms-secret"
    )
    # Fernet needs 32 urlsafe base64-encoded bytes; we derive from raw.
    # This is not perfect KDF but good enough for app-level secret.
    padded = (raw * 4).encode("utf-8")[:32]
    return base64.urlsafe_b64encode(padded)


try:
    from cryptography.fernet import Fernet
    _FERNET = Fernet(_get_secret_key_bytes())

    def encrypt_secret(plain: str) -> str:
        if not plain:
            return ""
        token = _FERNET.encrypt(plain.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt_secret(token: str) -> str:
        if not token:
            return ""
        plain = _FERNET.decrypt(token.encode("utf-8"))
        return plain.decode("utf-8")

except Exception:
    # Fallback (NOT secure). Keeps app working even if cryptography is not installed.
    def encrypt_secret(plain: str) -> str:
        if not plain:
            return ""
        return base64.b64encode(plain.encode("utf-8")).decode("utf-8")

    def decrypt_secret(token: str) -> str:
        if not token:
            return ""
        try:
            return base64.b64decode(token.encode("utf-8")).decode("utf-8")
        except Exception:
            return ""


class SqlServerMonitor(db.Model):
    __tablename__ = "sqlserver_monitor"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    friendly_name = db.Column(db.String(150), nullable=False)

    monitoring_server = db.Column(db.String(50), nullable=False)  # proxy/collector
    ip_address = db.Column(db.String(64), nullable=False)
    port = db.Column(db.Integer, default=1433)

    username = db.Column(db.String(150), nullable=True)
    password_enc = db.Column(db.Text, nullable=True)

    # SQLServer / AzureSQLDB / AzureSQLManagedInstance / AzureSQLPool
    db_type = db.Column(db.String(64), default="SQLServer", nullable=False)

    active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship("Customer", lazy="joined")

    # -----------------------------
    # Password helpers
    # -----------------------------
    def set_password(self, plain: str):
        self.password_enc = encrypt_secret(plain or "")

    def get_password(self) -> str:
        return decrypt_secret(self.password_enc or "")

    def to_dict(self, include_secret: bool = False):
        d = {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else "",
            "friendly_name": self.friendly_name,
            "monitoring_server": self.monitoring_server,
            "ip_address": self.ip_address,
            "port": self.port,
            "username": self.username or "",
            "db_type": self.db_type,
            "active": self.active,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_secret:
            d["password"] = self.get_password()
        return d

