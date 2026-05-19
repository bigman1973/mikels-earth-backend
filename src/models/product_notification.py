from src.models.user import db
from datetime import datetime
import uuid


class ProductNotification(db.Model):
    __tablename__ = 'product_notifications'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100))
    product_name = db.Column(db.String(255), nullable=False)
    product_id = db.Column(db.String(100), nullable=False)
    notified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'product_name': self.product_name,
            'product_id': self.product_id,
            'notified': self.notified,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
