"""
Modelo de Cupón para descuentos - Sistema completo de gestión
"""
from datetime import datetime
from src.database.db import db


class Coupon(db.Model):
    """
    Modelo para cupones de descuento
    Soporta: porcentaje/fijo, fecha caducidad, límite de usos, activar/desactivar
    """
    __tablename__ = 'coupons'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.String(500), nullable=True)  # Nota interna
    
    # Tipo de descuento
    discount_type = db.Column(db.String(20), nullable=False, default='percentage')  # 'percentage' or 'fixed'
    discount_value = db.Column(db.Float, nullable=False, default=10)  # 10 = 10% or 10€
    
    # Restricciones
    min_order_amount = db.Column(db.Float, default=0)  # Pedido mínimo
    max_uses = db.Column(db.Integer, nullable=True)  # None = ilimitado
    current_uses = db.Column(db.Integer, default=0)
    max_uses_per_customer = db.Column(db.Integer, nullable=True)  # None = ilimitado por cliente
    
    # Estado
    active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)  # None = sin caducidad
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Compatibilidad con el sistema antiguo (cupones por email)
    email = db.Column(db.String(255), nullable=True, index=True)  # None = cupón público
    used = db.Column(db.Boolean, default=False)  # Para cupones de un solo uso
    used_at = db.Column(db.DateTime, nullable=True)
    
    def __repr__(self):
        return f'<Coupon {self.code} ({self.display_discount})>'
    
    @property
    def display_discount(self):
        if self.discount_type == 'percentage':
            return f"{int(self.discount_value)}%"
        return f"{self.discount_value}€"
    
    @property
    def is_expired(self):
        if not self.expires_at:
            return False
        return datetime.utcnow() > self.expires_at
    
    def is_valid(self, order_amount=0, customer_email=None):
        """Check if coupon is valid for use"""
        if not self.active:
            return False, "Cupón desactivado"
        
        if self.is_expired:
            return False, "Cupón caducado"
        
        if self.max_uses and self.current_uses >= self.max_uses:
            return False, "Cupón agotado (máximo de usos alcanzado)"
        
        if self.used:  # Compatibilidad con cupones de un solo uso
            return False, "Este cupón ya ha sido utilizado"
        
        if order_amount > 0 and order_amount < self.min_order_amount:
            return False, f"Pedido mínimo de {self.min_order_amount}€ para este cupón"
        
        # Si el cupón está asociado a un email específico, verificar
        if self.email and customer_email:
            if self.email.lower() != customer_email.lower():
                return False, "Este cupón no está asociado a tu email"
        
        return True, "Válido"
    
    def calculate_discount(self, order_amount):
        """Calculate discount amount for a given order"""
        if self.discount_type == 'percentage':
            return round(order_amount * (self.discount_value / 100), 2)
        elif self.discount_type == 'fixed':
            return min(self.discount_value, order_amount)
        return 0
    
    def mark_as_used(self):
        """Incrementar uso del cupón"""
        self.current_uses += 1
        if self.max_uses and self.max_uses == 1:
            self.used = True
            self.used_at = datetime.utcnow()
        db.session.commit()
    
    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'description': self.description,
            'discount_type': self.discount_type,
            'discount_value': self.discount_value,
            'display_discount': self.display_discount,
            'min_order_amount': self.min_order_amount,
            'max_uses': self.max_uses,
            'current_uses': self.current_uses,
            'max_uses_per_customer': self.max_uses_per_customer,
            'active': self.active,
            'is_expired': self.is_expired,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'email': self.email,
            'used': self.used,
            'used_at': self.used_at.isoformat() if self.used_at else None,
        }
    
    @staticmethod
    def generate_code():
        """Generar código único de cupón"""
        import random
        import string
        chars = string.ascii_uppercase + string.digits
        random_part = ''.join(random.choice(chars) for _ in range(8))
        return f'MIKELS-{random_part}'
    
    @classmethod
    def create_coupon(cls, email=None, discount_percent=10):
        """
        Crear un nuevo cupón (compatibilidad con sistema antiguo)
        """
        if email:
            existing = cls.query.filter_by(email=email, used=False).first()
            if existing:
                return existing
        
        max_attempts = 10
        for _ in range(max_attempts):
            code = cls.generate_code()
            if not cls.query.filter_by(code=code).first():
                break
        
        coupon = cls(
            code=code,
            email=email,
            discount_type='percentage',
            discount_value=discount_percent,
            max_uses=1
        )
        
        db.session.add(coupon)
        db.session.commit()
        
        return coupon
    
    @classmethod
    def validate_coupon(cls, code, email=None):
        """
        Validar si un cupón es válido
        """
        coupon = cls.query.filter(
            db.func.lower(cls.code) == code.lower().strip()
        ).first()
        
        if not coupon:
            return False, "Cupón no encontrado"
        
        is_valid, message = coupon.is_valid(customer_email=email)
        if not is_valid:
            return False, message
        
        return True, coupon
