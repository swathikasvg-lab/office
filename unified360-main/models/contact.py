from extensions import db
from datetime import datetime

contact_group_members = db.Table(
    "contact_group_members",
    db.Column("contact_id", db.Integer, db.ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    db.Column("group_id", db.Integer, db.ForeignKey("contact_groups.id", ondelete="CASCADE"), primary_key=True),
)

class Contact(db.Model):
    __tablename__ = "contacts"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    display_name = db.Column(db.String(120), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    phone = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer", lazy="joined")

    groups = db.relationship(
        "ContactGroup",
        secondary=contact_group_members,
        back_populates="contacts",
        lazy="selectin",
    )

    __table_args__ = (
        db.UniqueConstraint("customer_id", "email", name="uq_contact_email_per_customer"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "display_name": self.display_name,
            "email": self.email,
            "phone": self.phone,
            "created_at": self.created_at.isoformat(),
        }


class ContactGroup(db.Model):
    __tablename__ = "contact_groups"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = db.relationship("Customer", lazy="joined")

    contacts = db.relationship(
        "Contact",
        secondary=contact_group_members,
        back_populates="groups",
        lazy="selectin",
    )

    __table_args__ = (
        db.UniqueConstraint("customer_id", "name", name="uq_group_name_per_customer"),
    )

    def to_dict(self, include_contacts=True):
        contacts = []
        if include_contacts:
            contacts = [
                {
                    "id": c.id,
                    "display_name": c.display_name,
                    "email": c.email,
                    "phone": c.phone,
                }
                for c in self.contacts
            ]

        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "members_count": len(self.contacts),
            "contacts": contacts,
        }

