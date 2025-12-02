"""
Modelo de Cupón para descuentos únicos por email
"""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# Importar la instancia de db desde user.py
from src.models.user import db


class Coupon(db.Model):
    """
    Modelo para cupones de descuento únicos
    Cada email solo puede tener un cupón activo
    """
    __tablename__ = 'coupons'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    discount_percent = db.Column(db.Integer, default=10, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    
    def __repr__(self):
        return f'<Coupon {self.code} for {self.email}>'
    
    def to_dict(self):
        """Convertir cupón a diccionario"""
        return {
            'id': self.id,
            'code': self.code,
            'email': self.email,
            'discount_percentage': self.discount_percent,
            'used': self.used,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'used_at': self.used_at.isoformat() if self.used_at else None
        }
    
    @staticmethod
    def generate_code():
        """Generar código único de cupón"""
        import random
        import string
        # Formato: MIKELS10-XXXXXXXX (8 caracteres alfanuméricos)
        chars = string.ascii_uppercase + string.digits
        random_part = ''.join(random.choice(chars) for _ in range(8))
        return f'MIKELS10-{random_part}'
    
    @classmethod
    def create_coupon(cls, email, discount_percent=10):
        """
        Crear un nuevo cupón para un email
        Retorna el cupón creado o None si el email ya tiene un cupón
        """
        # Verificar si el email ya tiene un cupón
        existing = cls.query.filter_by(email=email).first()
        if existing:
            return existing  # Retornar el cupón existente
        
        # Generar código único
        max_attempts = 10
        for _ in range(max_attempts):
            code = cls.generate_code()
            if not cls.query.filter_by(code=code).first():
                break
        else:
            raise Exception("No se pudo generar un código único después de varios intentos")
        
        # Crear nuevo cupón
        coupon = cls(
            code=code,
            email=email,
            discount_percent=discount_percent
        )
        
        db.session.add(coupon)
        db.session.commit()
        
        return coupon
    
    @classmethod
    def validate_coupon(cls, code, email=None):
        """
        Validar si un cupón es válido
        Retorna (is_valid, coupon_or_error_message)
        """
        coupon = cls.query.filter_by(code=code).first()
        
        if not coupon:
            return False, "Cupón no encontrado"
        
        if coupon.used:
            return False, "Este cupón ya ha sido utilizado"
        
        # Si se proporciona email, verificar que coincida
        if email and coupon.email.lower() != email.lower():
            return False, "Este cupón no está asociado a tu email"
        
        return True, coupon
    
    def mark_as_used(self):
        """Marcar cupón como usado"""
        self.used = True
        self.used_at = datetime.utcnow()
        db.session.commit()
