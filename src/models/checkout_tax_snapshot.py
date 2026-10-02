from datetime import datetime

from src.models.user import db


class CheckoutTaxSnapshot(db.Model):
    """Tax rules captured before a Stripe Checkout session is opened.

    The record is keyed by the same opaque checkout token as stock reservations.
    It avoids placing fiscal JSON in Stripe metadata, whose value length is
    limited, while keeping the post-payment receipt independent of later master
    data changes.
    """

    __tablename__ = 'checkout_tax_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    checkout_token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    rules = db.Column(db.JSON, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
