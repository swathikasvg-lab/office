from datetime import datetime

from extensions import db


class ReportSchedule(db.Model):
    __tablename__ = "report_schedules"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(180), nullable=False)
    report_id = db.Column(db.Integer, nullable=False, index=True)
    frequency = db.Column(db.String(24), nullable=False, default="weekly")  # daily/weekly/monthly
    run_time = db.Column(db.String(8), nullable=False, default="09:00")  # HH:MM
    timezone = db.Column(db.String(64), nullable=False, default="UTC")
    output_format = db.Column(db.String(12), nullable=False, default="pdf")
    params_json = db.Column(db.JSON, nullable=False, default={})
    recipients_json = db.Column(db.JSON, nullable=False, default=[])
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    next_run_at = db.Column(db.DateTime, nullable=True)
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    customer = db.relationship("Customer", lazy="joined")
    created_by = db.relationship("Ops_User", foreign_keys=[created_by_user_id], lazy="joined")
    updated_by = db.relationship("Ops_User", foreign_keys=[updated_by_user_id], lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "name": self.name,
            "report_id": self.report_id,
            "frequency": self.frequency,
            "run_time": self.run_time,
            "timezone": self.timezone,
            "output_format": self.output_format,
            "params": self.params_json or {},
            "recipients": self.recipients_json or [],
            "is_active": self.is_active,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_by_user_id": self.created_by_user_id,
            "updated_by_user_id": self.updated_by_user_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ReportNarrative(db.Model):
    __tablename__ = "report_narratives"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.cid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_id = db.Column(db.Integer, nullable=False, index=True)
    from_ts = db.Column(db.String(32), nullable=False)
    to_ts = db.Column(db.String(32), nullable=False)
    output_format = db.Column(db.String(12), nullable=False, default="pdf")
    summary_text = db.Column(db.Text, nullable=False)
    highlights_json = db.Column(db.JSON, nullable=False, default=[])
    generated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("ops_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    customer = db.relationship("Customer", lazy="joined")
    generated_by = db.relationship("Ops_User", lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "report_id": self.report_id,
            "from": self.from_ts,
            "to": self.to_ts,
            "format": self.output_format,
            "summary_text": self.summary_text,
            "highlights": self.highlights_json or [],
            "generated_by_user_id": self.generated_by_user_id,
            "created_at": self.created_at.isoformat(),
        }
