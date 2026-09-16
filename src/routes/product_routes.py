"""
Rutas públicas del catálogo de productos.

`GET /api/products` devuelve únicamente productos activos y visibles en tienda.
Los complementos se resuelven por separado: pueden estar activos para venderse aunque
no estén listados en el catálogo. Un complemento que no se pueda resolver con un
precio válido nunca se incluye en la respuesta pública.
"""
from flask import Blueprint, jsonify, request
from src.models.web_product import WebProduct

product_bp = Blueprint('products', __name__)


def _resolve_addons(products, lang):
    """Sustituye las referencias de addons por productos activos y vendibles."""
    addon_slugs = {
        addon.get('productSlug')
        for product in products
        for addon in (product.addons or [])
        if isinstance(addon, dict) and addon.get('productSlug')
    }

    if not addon_slugs:
        return {}

    addon_products = WebProduct.query.filter(
        WebProduct.slug.in_(addon_slugs),
        WebProduct.active.is_(True)
    ).all()
    return {product.slug: product for product in addon_products}


def _serialize_product(product, addon_products, lang):
    """Serializa un producto y conserva solo addons completamente resolubles."""
    data = product.to_frontend_dict(lang=lang)
    resolved_addons = []

    for addon in product.addons or []:
        if not isinstance(addon, dict):
            continue

        addon_product = addon_products.get(addon.get('productSlug'))
        if (
            addon_product is None
            or addon_product.sold_out
            or addon_product.price is None
            or addon_product.price <= 0
        ):
            continue

        variant_id = addon.get('variantId')
        variants = addon_product.variants or []
        if variant_id and not any(
            isinstance(variant, dict) and variant.get('id') == variant_id
            for variant in variants
        ):
            continue

        addon_data = dict(addon)
        addon_product_data = addon_product.to_frontend_dict(lang=lang)
        addon_product_data.pop('addons', None)
        addon_data['product'] = addon_product_data
        resolved_addons.append(addon_data)

    if product.addons is not None:
        data['addons'] = resolved_addons

    return data


def _categories_for(lang):
    if lang == 'en':
        return [
            {"id": "all", "name": "All", "slug": "all"},
            {"id": "conservas", "name": "Preserves", "slug": "conservas"},
            {"id": "aceites", "name": "Olive Oils", "slug": "aceites"},
            {"id": "packs", "name": "Packs", "slug": "packs"}
        ]

    return [
        {"id": "all", "name": "Todos", "slug": "all"},
        {"id": "conservas", "name": "Conservas", "slug": "conservas"},
        {"id": "aceites", "name": "Aceites", "slug": "aceites"},
        {"id": "packs", "name": "Packs", "slug": "packs"}
    ]


@product_bp.route('/products', methods=['GET'])
def get_products():
    """Devuelve el catálogo activo y visible, con sus addons vendibles resueltos."""
    lang = request.args.get('lang', 'es')
    products = WebProduct.query.filter_by(
        active=True,
        visible_in_store=True
    ).order_by(WebProduct.display_order).all()
    addon_products = _resolve_addons(products, lang)
    products_list = [
        _serialize_product(product, addon_products, lang)
        for product in products
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
        'categories': _categories_for(lang),
        'tags': tags
    })


@product_bp.route('/products/<slug>', methods=['GET'])
def get_product_by_slug(slug):
    """Devuelve una ficha solo si el producto está activo y visible en tienda."""
    lang = request.args.get('lang', 'es')
    product = WebProduct.query.filter_by(
        slug=slug,
        active=True,
        visible_in_store=True
    ).first()
    if not product:
        return jsonify({'error': 'Producto no encontrado'}), 404

    addon_products = _resolve_addons([product], lang)
    return jsonify(_serialize_product(product, addon_products, lang))
