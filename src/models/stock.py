from datetime import datetime

from src.models.user import db


class StockReservation(db.Model):
    """A short-lived allocation made before a Stripe Checkout payment.

    Product stock is not decremented at reservation time. The allocation instead
    prevents concurrent checkout sessions from selling the same web stock. It is
    consumed atomically when Stripe confirms payment.
    """

    __tablename__ = 'stock_reservations'

    id = db.Column(db.Integer, primary_key=True)
    checkout_token = db.Column(db.String(64), nullable=False, index=True)
    # A single Stripe Checkout session can reserve several product rows.
    stripe_session_id = db.Column(db.String(255), nullable=True, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('web_products.id'), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='active', index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    consumed_at = db.Column(db.DateTime, nullable=True)

    product = db.relationship('WebProduct', foreign_keys=[product_id])
    order = db.relationship('Order', foreign_keys=[order_id])


class StockMovement(db.Model):
    """Immutable audit line for paid stock deductions and their reversals."""

    __tablename__ = 'stock_movements'

    id = db.Column(db.Integer, primary_key=True)
    # Not every auditable movement is a customer order: an authorised harvest
    # allocation can correct the web catalogue before any sale occurs.
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('web_products.id'), nullable=False, index=True)
    quantity_delta = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(40), nullable=False, index=True)
    reference = db.Column(db.String(255), nullable=True, index=True)
    reversal_of_id = db.Column(db.Integer, db.ForeignKey('stock_movements.id'), nullable=True, unique=True)
    stock_before = db.Column(db.Integer, nullable=False)
    stock_after = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    order = db.relationship('Order', foreign_keys=[order_id])
    product = db.relationship('WebProduct', foreign_keys=[product_id])
    reversal_of = db.relationship('StockMovement', remote_side=[id], foreign_keys=[reversal_of_id])

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else None,
            'sku': self.product.sku if self.product else None,
            'quantity_delta': self.quantity_delta,
            'reason': self.reason,
            'reference': self.reference,
            'stock_before': self.stock_before,
            'stock_after': self.stock_after,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
