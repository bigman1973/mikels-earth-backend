"""
Rutas públicas del catálogo de productos.
Endpoint GET /api/products devuelve exactamente la misma estructura que products.js
para que el frontend funcione sin cambios en el carrito ni en la tienda.
"""
from flask import Blueprint, jsonify, request
from src.models.user import db
from src.models.web_product import WebProduct

product_bp = Blueprint('products', __name__)


@product_bp.route('/products', methods=['GET'])
def get_products():
    """
    Devuelve el catálogo completo de productos activos.
    Formato idéntico al antiguo products.js para compatibilidad total.
    """
    lang = request.args.get('lang', 'es')
    products = WebProduct.query.filter_by(active=True).order_by(WebProduct.display_order).all()
    
    products_list = [p.to_frontend_dict(lang=lang) for p in products]
    
    # También devolver categories y tags como antes
    if lang == 'en':
        categories = [
            {"id": "all", "name": "All", "slug": "all"},
            {"id": "conservas", "name": "Preserves", "slug": "conservas"},
            {"id": "aceites", "name": "Olive Oils", "slug": "aceites"},
            {"id": "packs", "name": "Packs", "slug": "packs"}
        ]
    else:
        categories = [
            {"id": "all", "name": "Todos", "slug": "all"},
            {"id": "conservas", "name": "Conservas", "slug": "conservas"},
            {"id": "aceites", "name": "Aceites", "slug": "aceites"},
            {"id": "packs", "name": "Packs", "slug": "packs"}
        ]
    
    tags = [
        "Vegano", "Sin Gluten", "Artesanal", "Local", "Prensado en Frío",
        "Ecológico", "Premiado", "Alto en Polifenoles", "Versátil", "Regalo",
        "Premium", "Degustación", "Sin Filtrar", "Edición Limitada",
        "Alto en Fruta", "Formato Familiar", "Uso Cotidiano", "Presentación",
        "Navidad", "Pack Completo"
    ]
    
    return jsonify({
        'products': products_list,
        'categories': categories,
        'tags': tags
    })


@product_bp.route('/products/<slug>', methods=['GET'])
def get_product_by_slug(slug):
    """Devuelve un producto por su slug."""
    lang = request.args.get('lang', 'es')
    product = WebProduct.query.filter_by(slug=slug, active=True).first()
    if not product:
        return jsonify({'error': 'Producto no encontrado'}), 404
    return jsonify(product.to_frontend_dict(lang=lang))
