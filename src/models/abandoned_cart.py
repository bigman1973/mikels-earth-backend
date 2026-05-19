"""
Modelo de Carrito Abandonado para recuperación vía email
Guarda el estado del carrito cuando el usuario llega al checkout,
permitiendo generar URLs persistentes de recuperación.
"""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import uuid
import json

# Importar la instancia de db desde user.py
from src.models.user import db


class AbandonedCart(db.Model):
    """
    Modelo para carritos abandonados.
    Se crea cuando el usuario llega al checkout con email.
    La URL de recuperación usa el cart_token como identificador único.
    """
    __tablename__ = 'abandoned_carts'
    
    id = db.Column(db.Integer, primary_key=True)
    cart_token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    customer_name = db.Column(db.String(255), nullable=True)
    items_json = db.Column(db.Text, nullable=False)  # JSON con los productos del carrito
    total = db.Column(db.Float, nullable=False)
    discount_code = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    recovered = db.Column(db.Boolean, default=False, nullable=False)
    recovered_at = db.Column(db.DateTime, nullable=True)
    converted = db.Column(db.Boolean, default=False, nullable=False)  # Si finalmente compró
    
    def __repr__(self):
        return f'<AbandonedCart {self.cart_token} for {self.email}>'
    
    @staticmethod
    def generate_token():
        """Generar token único para el carrito"""
        return uuid.uuid4().hex
    
    @property
    def items(self):
        """Deserializar items del JSON"""
        try:
            return json.loads(self.items_json)
        except:
            return []
    
    @items.setter
    def items(self, value):
        """Serializar items a JSON"""
        self.items_json = json.dumps(value)
    
    def get_checkout_url(self):
        """Generar URL de recuperación del carrito"""
        return f"https://www.mikels.es/recuperar-carrito/{self.cart_token}"
    
    def get_items_html(self):
        """Generar HTML con los productos para el email de Klaviyo"""
        items = self.items
        if not items:
            return "<p>Tu carrito está vacío</p>"
        
        html = '<table style="width:100%; border-collapse:collapse;">'
        for item in items:
            name = item.get('name', 'Producto')
            image = item.get('image', '')
            price = item.get('price', 0)
            quantity = item.get('quantity', 1)
            
            html += f'''
            <tr style="border-bottom: 1px solid #eee; padding: 12px 0;">
                <td style="padding: 12px 0; width: 80px;">
                    <img src="{image}" alt="{name}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px;" />
                </td>
                <td style="padding: 12px 8px;">
                    <strong style="color: #2d5016; font-size: 14px;">{name}</strong><br/>
                    <span style="color: #666; font-size: 13px;">Cantidad: {quantity}</span>
                </td>
                <td style="padding: 12px 0; text-align: right;">
                    <strong style="color: #333; font-size: 14px;">{price:.2f}€</strong>
                </td>
            </tr>'''
        
        html += '</table>'
        return html
    
    def to_dict(self):
        """Convertir a diccionario"""
        return {
            'id': self.id,
            'cart_token': self.cart_token,
            'email': self.email,
            'customer_name': self.customer_name,
            'items': self.items,
            'total': self.total,
            'discount_code': self.discount_code,
            'checkout_url': self.get_checkout_url(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'recovered': self.recovered,
            'converted': self.converted
        }
    
    @classmethod
    def create_or_update(cls, email, items, total, customer_name=None, discount_code=None):
        """
        Crear o actualizar un carrito abandonado para un email.
        Si ya existe uno reciente (< 4 horas) sin convertir, lo actualiza.
        """
        from datetime import timedelta
        
        # Buscar carrito reciente no convertido para este email
        recent_cutoff = datetime.utcnow() - timedelta(hours=4)
        existing = cls.query.filter(
            cls.email == email,
            cls.converted == False,
            cls.created_at > recent_cutoff
        ).order_by(cls.created_at.desc()).first()
        
        if existing:
            # Actualizar el carrito existente
            existing.items_json = json.dumps(items)
            existing.total = total
            existing.customer_name = customer_name
            existing.discount_code = discount_code
            existing.updated_at = datetime.utcnow()
            db.session.commit()
            return existing
        else:
            # Crear nuevo carrito
            cart = cls(
                cart_token=cls.generate_token(),
                email=email,
                customer_name=customer_name,
                items_json=json.dumps(items),
                total=total,
                discount_code=discount_code
            )
            db.session.add(cart)
            db.session.commit()
            return cart
