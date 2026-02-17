from extensions import db
from datetime import datetime

class UrlMonitor(db.Model):
    __tablename__ = "url_monitors"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    name = db.Column(db.String(120))
    monitoring_server = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(1024), nullable=False)
    http_method = db.Column(db.String(8), default="GET")
    timeout = db.Column(db.Integer, default=5)
    expected_status_code = db.Column(db.Integer, default=200)
    response_string_match = db.Column(db.String(255))
    follow_redirects = db.Column(db.Boolean, default=True)
    check_cert_expiry = db.Column(db.Boolean, default=False)

    username = db.Column(db.String(128))
    password = db.Column(db.String(128))
    request_body = db.Column(db.Text)
    content_type = db.Column(db.String(128), default="application/json")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer = db.relationship("Customer", lazy="joined")

    def to_dict(self, masked=True):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else "",
            "customer_acct_id": self.customer.acct_id if self.customer else "",
            "name": self.name,
            "monitoring_server": self.monitoring_server,
            "url": self.url,
            "http_method": self.http_method,
            "timeout": self.timeout,
            "expected_status_code": self.expected_status_code,
            "response_string_match": self.response_string_match,
            "follow_redirects": self.follow_redirects,
            "check_cert_expiry": self.check_cert_expiry,
            "username": self.username or "",
            "password": "••••••" if masked and self.password else "",
            "request_body": self.request_body or "",
            "content_type": self.content_type,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

