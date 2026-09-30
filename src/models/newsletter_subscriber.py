"""One welcome-coupon identity per newsletter subscriber."""
from datetime import datetime

from src.models.user import db


class NewsletterSubscriber(db.Model):
    """Reserves one canonical email identity before a welcome coupon is issued."""

    __tablename__ = 'newsletter_subscribers'

    id = db.Column(db.Integer, primary_key=True)
    email_key = db.Column(db.String(255), nullable=False, unique=True, index=True)
    email = db.Column(db.String(255), nullable=False)
    welcome_coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    welcome_coupon = db.relationship('Coupon', foreign_keys=[welcome_coupon_id])

    def __repr__(self):
        return f'<NewsletterSubscriber {self.email_key}>'
