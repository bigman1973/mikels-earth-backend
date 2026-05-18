"""
Modelo de Reseña para Mikel's Earth
Permite a los clientes dejar reseñas de productos y recibir un cupón de agradecimiento.
"""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# Importar la instancia de db desde user.py
from src.models.user import db


class Review(db.Model):
    """
    Modelo para reseñas de productos.
    Cada reseña está asociada a un email, un producto, y opcionalmente a un pedido.
    """
    __tablename__ = 'reviews'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Información del cliente
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    customer_name = db.Column(db.String(120), nullable=False)
    
    # Información del producto
    product_slug = db.Column(db.String(200), nullable=False, index=True)
    product_name = db.Column(db.String(200), nullable=False)
    
    # Contenido de la reseña
    rating = db.Column(db.Integer, nullable=False)  # 1-5 estrellas
    title = db.Column(db.String(200), nullable=True)
    comment = db.Column(db.Text, nullable=False)
    
    # Estado y moderación
    status = db.Column(db.String(20), default='approved', nullable=False)  # pending, approved, rejected
    is_verified_purchase = db.Column(db.Boolean, default=False, nullable=False)
    
    # Referencia al pedido (opcional)
    order_number = db.Column(db.String(50), nullable=True)
    
    # Cupón de agradecimiento generado
    reward_coupon_code = db.Column(db.String(50), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Review {self.customer_name} - {self.product_name} ({self.rating}★)>'
    
    def to_dict(self):
        """Convertir reseña a diccionario para la API"""
        return {
            'id': self.id,
            'customer_name': self.customer_name,
            'customer_email': self.customer_email,
            'product_slug': self.product_slug,
            'product_name': self.product_name,
            'rating': self.rating,
            'title': self.title,
            'comment': self.comment,
            'status': self.status,
            'is_verified_purchase': self.is_verified_purchase,
            'order_number': self.order_number,
            'reward_coupon_code': self.reward_coupon_code,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    def to_public_dict(self):
        """Versión pública de la reseña (sin email ni cupón)"""
        return {
            'id': self.id,
            'customer_name': self.customer_name,
            'product_slug': self.product_slug,
            'product_name': self.product_name,
            'rating': self.rating,
            'title': self.title,
            'comment': self.comment,
            'is_verified_purchase': self.is_verified_purchase,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
