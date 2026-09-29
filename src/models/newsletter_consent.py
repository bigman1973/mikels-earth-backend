"""Auditable consent record for newsletter popup submissions."""
from datetime import datetime

from src.models.user import db


class NewsletterConsent(db.Model):
    """Stores the exact choices made in the newsletter popup at submission time."""

    __tablename__ = 'newsletter_consents'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    first_name = db.Column(db.String(120), nullable=False)
    last_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50), nullable=True)
    source = db.Column(db.String(100), nullable=False, default='website')

    privacy_policy_accepted = db.Column(db.Boolean, nullable=False)
    privacy_policy_recorded_at = db.Column(db.DateTime, nullable=False)
    whatsapp_marketing_accepted = db.Column(db.Boolean, nullable=False, default=False)
    whatsapp_marketing_recorded_at = db.Column(db.DateTime, nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self):
        return f'<NewsletterConsent {self.email} #{self.id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'phone': self.phone,
            'source': self.source,
            'privacy_policy_accepted': self.privacy_policy_accepted,
            'privacy_policy_recorded_at': self.privacy_policy_recorded_at.isoformat(),
            'whatsapp_marketing_accepted': self.whatsapp_marketing_accepted,
            'whatsapp_marketing_recorded_at': self.whatsapp_marketing_recorded_at.isoformat(),
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }
