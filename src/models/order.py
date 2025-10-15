from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Order(db.Model):
    __tablename__ = 'orders'
    
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True, nullable=False)
    
    # Customer info
    customer_email = db.Column(db.String(120), nullable=False)
    customer_name = db.Column(db.String(120), nullable=False)
    customer_phone = db.Column(db.String(20))
    
    # Shipping address
    shipping_address = db.Column(db.String(200), nullable=False)
    shipping_city = db.Column(db.String(100), nullable=False)
    shipping_postal_code = db.Column(db.String(20), nullable=False)
    shipping_country = db.Column(db.String(50), nullable=False, default='España')
    
    # Order details
    items = db.Column(db.JSON, nullable=False)  # Array of order items
    subtotal = db.Column(db.Float, nullable=False)
    shipping_cost = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='EUR')
    
    # Payment info
    stripe_payment_intent_id = db.Column(db.String(100))
    stripe_checkout_session_id = db.Column(db.String(100))
    payment_status = db.Column(db.String(20), default='pending')  # pending, paid, failed, refunded
    
    # Order status
    order_status = db.Column(db.String(20), default='processing')  # processing, shipped, delivered, cancelled
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    paid_at = db.Column(db.DateTime)
    
    # Notes
    customer_notes = db.Column(db.Text)
    admin_notes = db.Column(db.Text)
    
    def __repr__(self):
        return f'<Order {self.order_number}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'order_number': self.order_number,
            'customer_email': self.customer_email,
            'customer_name': self.customer_name,
            'customer_phone': self.customer_phone,
            'shipping_address': self.shipping_address,
            'shipping_city': self.shipping_city,
            'shipping_postal_code': self.shipping_postal_code,
            'shipping_country': self.shipping_country,
            'items': self.items,
            'subtotal': self.subtotal,
            'shipping_cost': self.shipping_cost,
            'total': self.total,
            'currency': self.currency,
            'payment_status': self.payment_status,
            'order_status': self.order_status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'paid_at': self.paid_at.isoformat() if self.paid_at else None
        }


class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    
    id = db.Column(db.Integer, primary_key=True)
    subscription_number = db.Column(db.String(50), unique=True, nullable=False)
    
    # Customer info
    customer_email = db.Column(db.String(120), nullable=False)
    customer_name = db.Column(db.String(120), nullable=False)
    
    # Subscription details
    product_id = db.Column(db.Integer, nullable=False)
    product_name = db.Column(db.String(200), nullable=False)
    product_slug = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    frequency = db.Column(db.String(20), nullable=False)  # weekly, biweekly, monthly, bimonthly
    
    # Stripe info
    stripe_subscription_id = db.Column(db.String(100), unique=True)
    stripe_customer_id = db.Column(db.String(100))
    stripe_price_id = db.Column(db.String(100))
    
    # Status
    status = db.Column(db.String(20), default='active')  # active, paused, cancelled, expired
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    next_billing_date = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    
    def __repr__(self):
        return f'<Subscription {self.subscription_number}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'subscription_number': self.subscription_number,
            'customer_email': self.customer_email,
            'customer_name': self.customer_name,
            'product_id': self.product_id,
            'product_name': self.product_name,
            'product_slug': self.product_slug,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'frequency': self.frequency,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'next_billing_date': self.next_billing_date.isoformat() if self.next_billing_date else None
        }

