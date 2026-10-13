"""Auditable Black Friday VIP signups collected with explicit consent."""
from datetime import datetime

from src.models.user import db


class VipSignup(db.Model):
    """One consent record per VIP landing submission.

    Repeated submissions are deliberately retained: a consent audit must not
    overwrite a prior declaration just because a person submits the form again.
    """

    __tablename__ = 'vip_signups'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    first_name = db.Column(db.String(120), nullable=True)
    consent_marketing = db.Column(db.Boolean, nullable=False, default=False)
    consented_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    source_ip = db.Column(db.String(45), nullable=False)
    source = db.Column(db.String(100), nullable=False, default='vip_black_friday_2026')

    klaviyo_subscription_status = db.Column(db.String(20), nullable=False, default='pending')
    klaviyo_event_status = db.Column(db.String(20), nullable=False, default='pending')
    klaviyo_error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self):
        return f'<VipSignup {self.email} #{self.id}>'
