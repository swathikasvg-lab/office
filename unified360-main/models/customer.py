from extensions import db
from datetime import datetime

class Customer(db.Model):
    __tablename__ = "customers"

    cid = db.Column(db.Integer, primary_key=True)
    acct_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "cid": self.cid,
            "acct_id": self.acct_id,
            "name": self.name,
            "email": self.email,
            "created_at": self.created_at.isoformat(),
        }

