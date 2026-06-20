"""
Modelo WebProduct - Productos del catálogo web de Mikel's Earth.
Almacena toda la información necesaria para renderizar los productos en la web.
Los campos JSON permiten flexibilidad para datos estructurados variables.
"""
from datetime import datetime
from src.models.user import db


class WebProduct(db.Model):
    __tablename__ = 'web_products'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Identificación
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    sku = db.Column(db.String(50), unique=True)
    
    # Descripciones
    description = db.Column(db.Text)  # Descripción corta
    long_description = db.Column(db.Text)  # Descripción larga (markdown)
    
    # Precios
    price = db.Column(db.Float, nullable=False)  # Precio con IVA
    original_price = db.Column(db.Float)  # Precio original (tachado)
    currency = db.Column(db.String(3), default='EUR')
    
    # Imágenes
    image = db.Column(db.String(500))  # Imagen principal
    images = db.Column(db.JSON)  # Array de rutas de imágenes
    
    # Clasificación
    category = db.Column(db.String(50), nullable=False)  # Aceites, Conservas, Packs
    tags = db.Column(db.JSON)  # Array de tags
    
    # Stock y peso
    stock = db.Column(db.Integer, default=0)
    weight = db.Column(db.String(100))
    sold_out = db.Column(db.Boolean, default=False)
    sold_out_message = db.Column(db.String(200))
    
    # Contenido
    ingredients = db.Column(db.Text)
    nutritional_info = db.Column(db.JSON)  # Objeto flexible
    
    # Suscripciones
    subscription_available = db.Column(db.Boolean, default=False)
    subscription_discount = db.Column(db.Integer)
    subscription_frequencies = db.Column(db.JSON)  # Array de {value, label, discount}
    subscription_terms = db.Column(db.JSON)  # {duration, renewalPolicy}
    
    # Descuentos
    volume_discount = db.Column(db.JSON)  # {minQuantity, discount}
    tiered_discount = db.Column(db.JSON)  # Array de descuentos escalonados
    
    # Extras
    addons = db.Column(db.JSON)  # Array de addons
    variants = db.Column(db.JSON)  # Array de variantes
    includes = db.Column(db.JSON)  # Array de contenido del pack
    related_products = db.Column(db.JSON)  # Array de slugs relacionados
    claims = db.Column(db.JSON)  # Array de claims/beneficios
    badges = db.Column(db.JSON)  # Array de {text, color, action?}
    
    # Flags
    featured = db.Column(db.Boolean, default=False)
    free_shipping = db.Column(db.Boolean, default=False)
    limited_edition = db.Column(db.Boolean, default=False)
    award = db.Column(db.String(200))
    active = db.Column(db.Boolean, default=True)  # Para ocultar sin borrar
    
    # Orden de visualización
    display_order = db.Column(db.Integer, default=0)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<WebProduct {self.name}>'
    
    def to_frontend_dict(self):
        """
        Devuelve el producto en el formato exacto que espera el frontend (products.js).
        Esto garantiza compatibilidad total con el carrito, la tienda y las páginas de producto.
        """
        result = {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'longDescription': self.long_description,
            'price': self.price,
            'currency': self.currency,
            'image': self.image,
            'images': self.images or [],
            'category': self.category,
            'tags': self.tags or [],
            'stock': self.stock,
            'weight': self.weight,
            'ingredients': self.ingredients,
            'nutritionalInfo': self.nutritional_info,
            'subscriptionAvailable': self.subscription_available or False,
            'subscriptionDiscount': self.subscription_discount,
            'subscriptionFrequencies': self.subscription_frequencies or [],
        }
        
        # Campos opcionales - solo incluir si tienen valor
        if self.sold_out:
            result['soldOut'] = True
        if self.sold_out_message:
            result['soldOutMessage'] = self.sold_out_message
        if self.original_price:
            result['originalPrice'] = self.original_price
        if self.volume_discount:
            result['volumeDiscount'] = self.volume_discount
        if self.tiered_discount:
            result['tieredDiscount'] = self.tiered_discount
        if self.addons:
            result['addons'] = self.addons
        if self.variants:
            result['variants'] = self.variants
        if self.includes:
            result['includes'] = self.includes
        if self.related_products:
            result['relatedProducts'] = self.related_products
        if self.claims:
            result['claims'] = self.claims
        if self.badges:
            result['badges'] = self.badges
        if self.featured:
            result['featured'] = True
        if self.free_shipping:
            result['freeShipping'] = True
        if self.limited_edition:
            result['limitedEdition'] = True
        if self.award:
            result['award'] = self.award
        if self.subscription_terms:
            result['subscriptionTerms'] = self.subscription_terms
        
        return result
    
    def to_admin_dict(self):
        """Devuelve el producto con todos los campos para el panel admin."""
        d = self.to_frontend_dict()
        d['sku'] = self.sku
        d['active'] = self.active
        d['displayOrder'] = self.display_order
        d['createdAt'] = self.created_at.isoformat() if self.created_at else None
        d['updatedAt'] = self.updated_at.isoformat() if self.updated_at else None
        return d
