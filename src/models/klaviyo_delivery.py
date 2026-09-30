"""Persistent audit trail and retry queue for outbound Klaviyo events."""
from datetime import datetime

from src.models.user import db


class KlaviyoDelivery(db.Model):
    """One immutable outbound event, with its delivery attempts and outcome."""

    __tablename__ = "klaviyo_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    event_name = db.Column(db.String(255), nullable=False, index=True)
    profile_email = db.Column(db.String(255), nullable=False, index=True)
    properties = db.Column(db.JSON, nullable=False, default=dict)
    profile_attributes = db.Column(db.JSON, nullable=True)
    value = db.Column(db.Float, nullable=True)
    idempotency_key = db.Column(db.String(255), nullable=False, unique=True, index=True)
    critical = db.Column(db.Boolean, nullable=False, default=False)

    # pending -> accepted | retrying -> failed (terminal after retry budget)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    http_status = db.Column(db.Integer, nullable=True)
    failure_reason = db.Column(db.String(1000), nullable=True)
    response_summary = db.Column(db.String(1000), nullable=True)
    next_attempt_at = db.Column(db.DateTime, nullable=True, index=True)
    accepted_at = db.Column(db.DateTime, nullable=True)

    # A critical failure is never hidden: record whether the independent alert
    # channel accepted the alert, or why it could not be attempted.
    alert_status = db.Column(db.String(32), nullable=True)
    alerted_at = db.Column(db.DateTime, nullable=True)
    alert_error = db.Column(db.String(1000), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "event_name": self.event_name,
            "profile_email": self.profile_email,
            "idempotency_key": self.idempotency_key,
            "critical": self.critical,
            "status": self.status,
            "attempts": self.attempts,
            "http_status": self.http_status,
            "failure_reason": self.failure_reason,
            "response_summary": self.response_summary,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
            "alert_status": self.alert_status,
            "alerted_at": self.alerted_at.isoformat() if self.alerted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
