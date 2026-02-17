from datetime import datetime

from extensions import db


class License(db.Model):
    __tablename__ = "licenses"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = db.Column(db.String(120))
    starts_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    grace_days = db.Column(db.Integer, nullable=False, default=30)
    status = db.Column(db.String(20), nullable=False, default="active")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")
    items = db.relationship(
        "LicenseItem",
        back_populates="license",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    def to_dict(self, include_items=True):
        data = {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "name": self.name or "",
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "grace_days": self.grace_days,
            "status": self.status,
        }
        if include_items:
            data["items"] = [i.to_dict() for i in self.items]
        return data


class LicenseItem(db.Model):
    __tablename__ = "license_items"

    id = db.Column(db.Integer, primary_key=True)

    license_id = db.Column(
        db.Integer,
        db.ForeignKey("licenses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    monitoring_type = db.Column(db.String(64), nullable=False, index=True)
    max_count = db.Column(db.Integer, nullable=False, default=0)

    license = db.relationship("License", back_populates="items")

    def to_dict(self):
        return {
            "id": self.id,
            "monitoring_type": self.monitoring_type,
            "max_count": self.max_count,
        }
