"""
Rutas del Panel de Administración - Gestión de productos, precios, stock y pedidos.
Requiere autenticación Microsoft Entra ID.
"""
from flask import Blueprint, request, jsonify
from src.routes.auth_routes import admin_required, role_required
from src.services.holded_service import (
    holded_get_products,
    holded_get_product,
    holded_update_product,
    holded_get_contacts,
    holded_get_contact,
    holded_find_contact_by_email,
    holded_create_sales_order,
    holded_create_invoice,
    holded_create_salesreceipt,
    holded_get_invoice_pdf,
    holded_get_warehouses,
    holded_get_or_create_contact,
    holded_get_contact_invoices,
    holded_get_contact_salesorders,
    holded_get_contact_salesreceipts,
    holded_get_all_salesreceipts,
    holded_get_document,
    holded_send_document_email,
    HOLDED_BASE_URL,
    HOLDED_API_KEY
)
from src.models.user import db
from datetime import datetime
import json
import os

admin_panel_bp = Blueprint('admin_panel', __name__)


# ============================================================
# PRODUCTOS Y PRECIOS
# ============================================================

@admin_panel_bp.route('/products', methods=['GET'])
@admin_required
def get_products():
    """
    Devuelve los productos de Holded junto con los precios web (si existen).
    Incluye también productos web que no tienen match en Holded.
    Precios: coste y holded_price son sin IVA. web_price es con IVA.
    Se incluye iva_rate para que el frontend pueda calcular el margen correctamente.
    """
    holded_products = holded_get_products()

    # Leer precios web desde el archivo de productos del frontend
    web_prices = _get_web_prices()
    
    # Leer costes de portes y preparación
    product_costs = _get_product_costs()

    # Calcular costes de packs basados en componentes
    pack_costs = _calculate_pack_costs(holded_products)

    result = []
    matched_web_skus = set()

    for p in holded_products:
        sku = p.get('sku', '')
        web_match = web_prices.get(sku, {})
        if web_match:
            matched_web_skus.add(sku)

        # Determinar IVA: aceites alimentarios 4%, conservas 10%, otros 21%
        iva_rate = 0.04  # Por defecto 4% para AOVE
        taxes = p.get('taxes', [])
        if taxes:
            # Si tiene info de impuestos en Holded, usarla
            for tax in taxes:
                if isinstance(tax, dict):
                    tax_val = tax.get('tax', '')
                    if '10' in str(tax_val):
                        iva_rate = 0.10
                    elif '21' in str(tax_val):
                        iva_rate = 0.21
                    elif '4' in str(tax_val):
                        iva_rate = 0.04

        sku_costs = product_costs.get(sku, {})
        # Para packs: usar coste calculado de componentes si es mayor que el coste de Holded
        raw_cost = p.get('cost', 0) or 0
        if sku in pack_costs and pack_costs[sku] > 0:
            effective_cost = pack_costs[sku]
        else:
            effective_cost = raw_cost

        product_data = {
            'holded_id': p.get('id'),
            'id': web_match.get('id'),  # ID de web_products para el editor
            'name': p.get('name', ''),
            'sku': sku,
            'barcode': p.get('barcode', ''),
            'holded_price': p.get('price', 0),
            'holded_total': p.get('total', 0),  # Precio con IVA
            'stock': p.get('stock', 0),
            'has_stock': p.get('hasStock', False),
            'cost': effective_cost,
            'cost_source': 'pack_components' if (sku in pack_costs and pack_costs[sku] > 0) else 'holded',
            'tax': taxes,
            'iva_rate': iva_rate,
            'web_price': web_match.get('price'),
            'web_name': web_match.get('name'),
            'web_category': web_match.get('category'),
            'synced': _is_price_synced(p, web_prices),
            'source': 'holded',
            'shipping_cost': web_match.get('shipping_cost', sku_costs.get('shipping_cost', 0)),
            'preparation_cost': web_match.get('preparation_cost', sku_costs.get('preparation_cost', 0)),
            'active': web_match.get('active', True)
        }
        result.append(product_data)

    # Añadir productos web que NO tienen match en Holded
    for sku, wp in web_prices.items():
        if sku not in matched_web_skus:
            # Determinar IVA por categoría
            cat = wp.get('category', '').lower()
            if cat in ('conservas',):
                iva_rate = 0.10
            elif cat in ('aceites',):
                iva_rate = 0.04
            else:
                iva_rate = 0.04  # Packs de aceite/conserva → 4% por defecto

            sku_costs = product_costs.get(sku, {})
            # Para packs web_only: usar coste calculado de componentes
            effective_cost = pack_costs.get(sku, 0) if sku in pack_costs else 0
            product_data = {
                'holded_id': None,
                'id': wp.get('id'),  # ID de web_products para el editor
                'name': wp.get('name', ''),
                'sku': sku,
                'barcode': '',
                'holded_price': None,
                'holded_total': None,
                'stock': wp.get('stock', 0),
                'has_stock': True,
                'cost': effective_cost,
                'cost_source': 'pack_components' if effective_cost > 0 else 'none',
                'tax': [],
                'iva_rate': iva_rate,
                'web_price': wp.get('price'),
                'web_name': wp.get('name'),
                'web_category': wp.get('category'),
                'synced': None,
                'source': 'web_only',
                'shipping_cost': wp.get('shipping_cost', 0),
                'preparation_cost': wp.get('preparation_cost', 0),
                'active': wp.get('active', True)
            }
            result.append(product_data)

    return jsonify({
        'products': result,
        'total_holded': len(holded_products),
        'total_web': len(web_prices)
    })


@admin_panel_bp.route('/products/sync-to-holded', methods=['POST'])
@admin_required
@role_required('admin')
def sync_prices_to_holded():
    """
    Sincroniza precios de la web a Holded (manual, con botón).
    Recibe una lista de productos con sus nuevos precios.
    """
    data = request.get_json()
    products_to_sync = data.get('products', [])

    results = []
    for product in products_to_sync:
        holded_id = product.get('holded_id')
        new_price = product.get('price')

        if not holded_id or new_price is None:
            results.append({'holded_id': holded_id, 'success': False, 'error': 'Datos incompletos'})
            continue

        success, response = holded_update_product(holded_id, {'price': new_price})
        results.append({
            'holded_id': holded_id,
            'success': success,
            'new_price': new_price
        })

    return jsonify({
        'results': results,
        'synced_count': sum(1 for r in results if r['success']),
        'failed_count': sum(1 for r in results if not r['success'])
    })


@admin_panel_bp.route('/products/sync-from-holded', methods=['POST'])
@admin_required
@role_required('admin')
def sync_prices_from_holded():
    """
    Trae los precios de Holded y los muestra para que el admin decida
    si quiere actualizarlos en la web.
    """
    holded_products = holded_get_products()
    web_prices = _get_web_prices()

    differences = []
    for p in holded_products:
        sku = p.get('sku', '')
        if sku in web_prices:
            holded_price = p.get('price', 0)
            web_price = web_prices[sku].get('price', 0)
            if abs(holded_price - web_price) > 0.01:
                differences.append({
                    'sku': sku,
                    'name': p.get('name', ''),
                    'holded_price': holded_price,
                    'web_price': web_price,
                    'difference': round(holded_price - web_price, 2)
                })

    return jsonify({
        'differences': differences,
        'total_compared': len([p for p in holded_products if p.get('sku', '') in web_prices]),
        'total_different': len(differences)
    })


# ============================================================
# COSTES DE PORTES Y PREPARACIÓN POR PRODUCTO
# ============================================================

def _get_product_costs():
    """Lee los costes de portes y preparación por SKU desde la DB."""
    from src.models.web_product import WebProduct
    try:
        products = WebProduct.query.all()
        costs = {}
        for p in products:
            if p.sku:
                costs[p.sku] = {
                    'shipping_cost': p.shipping_cost or 0,
                    'preparation_cost': p.preparation_cost or 0
                }
        return costs
    except Exception as e:
        print(f"[Admin] Error leyendo costes de DB: {e}")
    return {}


def _save_product_costs(costs):
    """Guarda los costes de portes y preparación por SKU en la DB."""
    from src.models.web_product import WebProduct
    try:
        for sku, cost_data in costs.items():
            product = WebProduct.query.filter_by(sku=sku).first()
            if product:
                product.shipping_cost = cost_data.get('shipping_cost', 0)
                product.preparation_cost = cost_data.get('preparation_cost', 0)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"[Admin] Error guardando costes en DB: {e}")
        return False


@admin_panel_bp.route('/products/costs', methods=['GET'])
@admin_required
def get_product_costs():
    """Devuelve los costes de portes y preparación por producto."""
    costs = _get_product_costs()
    return jsonify({'costs': costs})


@admin_panel_bp.route('/products/<sku>/costs', methods=['PUT'])
@admin_required
@role_required('admin')
def update_product_costs(sku):
    """
    Actualiza los costes de portes y preparación de un producto.
    Body: { shipping_cost: float, preparation_cost: float }
    """
    try:
        data = request.get_json()
        shipping_cost = float(data.get('shipping_cost', 0))
        preparation_cost = float(data.get('preparation_cost', 0))

        costs = _get_product_costs()
        costs[sku] = {
            'shipping_cost': shipping_cost,
            'preparation_cost': preparation_cost
        }

        if _save_product_costs(costs):
            return jsonify({
                'success': True,
                'sku': sku,
                'shipping_cost': shipping_cost,
                'preparation_cost': preparation_cost
            })
        else:
            return jsonify({'error': 'Error guardando costes'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# COMPOSICIÓN DE PACKS Y COSTES DE COMPONENTES
# ============================================================

@admin_panel_bp.route('/products/pack-components', methods=['GET'])
@admin_required
def get_pack_components():
    """
    Devuelve la composición de todos los packs con sus costes.
    Para componentes con SKU: coste de Holded.
    Para componentes manuales: coste de pack_component_costs.json.
    """
    holded_products = holded_get_products()
    
    # Mapa de costes por SKU desde Holded
    cost_by_sku = {}
    for p in holded_products:
        sku = p.get('sku', '')
        if sku:
            cost_by_sku[sku] = p.get('cost', 0) or 0

    # Costes manuales
    manual_costs = _get_pack_component_costs()

    result = {}
    for pack_sku, components in PACK_COMPONENTS.items():
        pack_detail = []
        total_cost = 0
        for comp in components:
            if comp.get('manual'):
                comp_id = comp.get('id', '')
                comp_cost = manual_costs.get(comp_id, 0)
                pack_detail.append({
                    'id': comp_id,
                    'name': comp.get('name', ''),
                    'quantity': comp.get('quantity', 1),
                    'cost': comp_cost,
                    'source': 'manual',
                    'editable': True
                })
            else:
                comp_sku = comp.get('sku', '')
                holded_cost = cost_by_sku.get(comp_sku, 0)
                # Si Holded tiene coste > 0, usarlo. Si no, buscar coste manual.
                if holded_cost > 0:
                    comp_cost = holded_cost
                    source = 'holded'
                    editable = False
                else:
                    # Sin coste en Holded: permitir edición manual
                    comp_cost = manual_costs.get(comp_sku, 0)
                    source = 'manual' if comp_cost > 0 else 'sin_coste'
                    editable = True
                pack_detail.append({
                    'id': comp_sku,  # Usar SKU como id para guardar coste manual
                    'sku': comp_sku,
                    'name': comp.get('name', ''),
                    'quantity': comp.get('quantity', 1),
                    'cost': comp_cost,
                    'source': source,
                    'editable': editable
                })
            total_cost += comp_cost * comp.get('quantity', 1)
        
        result[pack_sku] = {
            'components': pack_detail,
            'total_cost': round(total_cost, 2)
        }

    return jsonify({'packs': result})


@admin_panel_bp.route('/products/pack-component-cost', methods=['PUT'])
@admin_required
@role_required('admin')
def update_pack_component_cost():
    """
    Actualiza el coste manual de un componente de pack.
    Body: { component_id: string, cost: float }
    """
    try:
        data = request.get_json()
        component_id = data.get('component_id', '')
        cost = float(data.get('cost', 0))

        if not component_id:
            return jsonify({'error': 'component_id es obligatorio'}), 400

        costs = _get_pack_component_costs()
        costs[component_id] = cost
        _save_pack_component_costs(costs)

        return jsonify({
            'success': True,
            'component_id': component_id,
            'cost': cost
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/products/<sku>/web-price', methods=['PUT'])
@admin_required
@role_required('admin')
def update_web_price(sku):
    """
    Actualiza el precio web de un producto en la base de datos Y en Holded.
    El cambio se refleja inmediatamente en la web (el frontend lee de la API)
    y también se sincroniza con la tarifa 'tienda online' de Holded.
    Body: { price: float }
    """
    try:
        from src.models.web_product import WebProduct
        data = request.get_json()
        new_price = float(data.get('price', 0))

        if new_price <= 0:
            return jsonify({'error': 'El precio debe ser mayor que 0'}), 400

        # Buscar producto por SKU en la DB
        product = WebProduct.query.filter_by(sku=sku).first()
        if not product:
            return jsonify({'error': f'Producto con SKU {sku} no encontrado en la DB'}), 404

        old_price = product.price
        product.price = new_price
        db.session.commit()

        # Sincronizar precio con Holded
        holded_updated = False
        holded_error = None
        try:
            holded_updated = _sync_price_to_holded(sku, new_price)
        except Exception as he:
            holded_error = str(he)

        return jsonify({
            'success': True,
            'sku': sku,
            'old_price': old_price,
            'new_price': new_price,
            'product_name': product.name,
            'db_updated': True,
            'holded_updated': holded_updated,
            'holded_error': holded_error
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Mapeo SKU → ID de producto en Holded
HOLDED_PRODUCT_IDS = {
    'MIKBIO19': '61c0f47e28ef0825ef14d130',
    'MIKVE500': '678d68dd797fed41420fa32f',
    'MIKVE5LP': '678d6b131ed596b63c07b503',
    'MIKPACKYPO': '67ac6644e1d1c0404801c340',
    'MIKPARJ250': '67b05a158d784f8805024aea',
    'MIKVET500': '68d56a35492a53699a0bf531',
    'MIKPARA450': '6a0578a83bccc347bd0bb32a',
    'MIKNECT450': '6a057b1c8111f9de8e0f0ab8',
}

# IVA por tipo de producto para calcular precio base (sin IVA) desde precio web (con IVA)
IVA_RATES_BY_SKU = {
    'MIKBIO19': 0.04,    # Aceite ecológico → 4%
    'MIKVE500': 0.04,    # Aceite equilibrado → 4%
    'MIKVE5LP': 0.04,    # Aceite 5L → 4%
    'MIKVET500': 0.04,   # Aceite temprano → 4%
    'MIKPARA450': 0.10,  # Paraguayo conserva → 10%
    'MIKNECT450': 0.10,  # Nectarina conserva → 10%
    'MIKPARJ250': 0.10,  # Mermelada → 10%
    'MIKPACKYPO': 0.04,  # Pack degustación → 4%
}

def _sync_price_to_holded(sku, web_price_with_iva):
    """
    Actualiza el precio del producto en Holded.
    web_price_with_iva: precio que se muestra en la web (IVA incluido)
    Holded espera el precio SIN IVA (base imponible).
    """
    import requests as req
    holded_id = HOLDED_PRODUCT_IDS.get(sku)
    if not holded_id:
        return False  # Producto no está en Holded (ej: packs sin SKU en Holded)

    iva_rate = IVA_RATES_BY_SKU.get(sku, 0.04)
    price_without_iva = round(web_price_with_iva / (1 + iva_rate), 5)

    response = req.put(
        f'https://api.holded.com/api/invoicing/v1/products/{holded_id}',
        headers={'key': HOLDED_API_KEY, 'Content-Type': 'application/json'},
        json={'price': price_without_iva}
    )

    if response.status_code == 200:
        result = response.json()
        return result.get('status') == 1
    return False


# ============================================================
# CRUD COMPLETO DE PRODUCTOS WEB
# ============================================================

@admin_panel_bp.route('/web-products', methods=['GET'])
@admin_required
def get_web_products():
    """Devuelve todos los productos web (activos e inactivos) para el panel admin."""
    from src.models.web_product import WebProduct
    products = WebProduct.query.order_by(WebProduct.display_order).all()
    return jsonify({'products': [p.to_admin_dict() for p in products]})


@admin_panel_bp.route('/web-products/<int:product_id>', methods=['GET'])
@admin_required
def get_web_product(product_id):
    """Devuelve un producto web por ID para edición."""
    from src.models.web_product import WebProduct
    product = WebProduct.query.get(product_id)
    if not product:
        return jsonify({'error': 'Producto no encontrado'}), 404
    return jsonify(product.to_admin_dict())


@admin_panel_bp.route('/web-products', methods=['POST'])
@admin_required
@role_required('admin')
def create_web_product():
    """Crea un nuevo producto web."""
    from src.models.web_product import WebProduct
    try:
        data = request.get_json()
        
        # Validaciones básicas
        if not data.get('name') or not data.get('slug') or not data.get('category'):
            return jsonify({'error': 'Nombre, slug y categoría son obligatorios'}), 400
        
        if not data.get('price') or float(data['price']) <= 0:
            return jsonify({'error': 'El precio debe ser mayor que 0'}), 400
        
        # Verificar slug único
        existing = WebProduct.query.filter_by(slug=data['slug']).first()
        if existing:
            return jsonify({'error': f'Ya existe un producto con slug "{data["slug"]}"'}), 409
        
        product = WebProduct(
            name=data['name'],
            slug=data['slug'],
            sku=data.get('sku'),
            description=data.get('description'),
            long_description=data.get('longDescription'),
            price=float(data['price']),
            original_price=float(data['originalPrice']) if data.get('originalPrice') else None,
            currency=data.get('currency', 'EUR'),
            image=data.get('image'),
            images=data.get('images'),
            category=data['category'],
            tags=data.get('tags'),
            stock=int(data.get('stock', 0)),
            weight=data.get('weight'),
            sold_out=data.get('soldOut', False),
            sold_out_message=data.get('soldOutMessage'),
            ingredients=data.get('ingredients'),
            nutritional_info=data.get('nutritionalInfo'),
            subscription_available=data.get('subscriptionAvailable', False),
            subscription_discount=data.get('subscriptionDiscount'),
            subscription_frequencies=data.get('subscriptionFrequencies'),
            subscription_terms=data.get('subscriptionTerms'),
            volume_discount=data.get('volumeDiscount'),
            tiered_discount=data.get('tieredDiscount'),
            addons=data.get('addons'),
            variants=data.get('variants'),
            includes=data.get('includes'),
            related_products=data.get('relatedProducts'),
            claims=data.get('claims'),
            badges=data.get('badges'),
            featured=data.get('featured', False),
            free_shipping=data.get('freeShipping', False),
            limited_edition=data.get('limitedEdition', False),
            award=data.get('award'),
            active=data.get('active', True),
            display_order=int(data.get('displayOrder', 0)),
            shipping_cost=float(data.get('shippingCost', 0)),
            preparation_cost=float(data.get('preparationCost', 0))
        )
        
        db.session.add(product)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'product': product.to_admin_dict()
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/web-products/<int:product_id>', methods=['PUT'])
@admin_required
@role_required('admin')
def update_web_product(product_id):
    """
    Actualiza un producto web completo (nombre, descripción, precio, fotos, etc.).
    El cambio se refleja inmediatamente en la web.
    """
    from src.models.web_product import WebProduct
    try:
        product = WebProduct.query.get(product_id)
        if not product:
            return jsonify({'error': 'Producto no encontrado'}), 404
        
        data = request.get_json()
        
        # Actualizar solo los campos que vienen en el body
        if 'name' in data:
            product.name = data['name']
        if 'slug' in data:
            # Verificar slug único (excepto el propio)
            existing = WebProduct.query.filter(WebProduct.slug == data['slug'], WebProduct.id != product_id).first()
            if existing:
                return jsonify({'error': f'Ya existe otro producto con slug "{data["slug"]}"'}), 409
            product.slug = data['slug']
        if 'sku' in data:
            product.sku = data['sku']
        if 'description' in data:
            product.description = data['description']
        if 'longDescription' in data:
            product.long_description = data['longDescription']
        if 'price' in data:
            product.price = float(data['price'])
        if 'originalPrice' in data:
            product.original_price = float(data['originalPrice']) if data['originalPrice'] else None
        if 'image' in data:
            product.image = data['image']
        if 'images' in data:
            product.images = data['images']
        if 'category' in data:
            product.category = data['category']
        if 'tags' in data:
            product.tags = data['tags']
        if 'stock' in data:
            product.stock = int(data['stock'])
        if 'weight' in data:
            product.weight = data['weight']
        if 'soldOut' in data:
            product.sold_out = data['soldOut']
        if 'soldOutMessage' in data:
            product.sold_out_message = data['soldOutMessage']
        if 'ingredients' in data:
            product.ingredients = data['ingredients']
        if 'nutritionalInfo' in data:
            product.nutritional_info = data['nutritionalInfo']
        if 'subscriptionAvailable' in data:
            product.subscription_available = data['subscriptionAvailable']
        if 'subscriptionDiscount' in data:
            product.subscription_discount = data['subscriptionDiscount']
        if 'subscriptionFrequencies' in data:
            product.subscription_frequencies = data['subscriptionFrequencies']
        if 'subscriptionTerms' in data:
            product.subscription_terms = data['subscriptionTerms']
        if 'volumeDiscount' in data:
            product.volume_discount = data['volumeDiscount']
        if 'tieredDiscount' in data:
            product.tiered_discount = data['tieredDiscount']
        if 'addons' in data:
            product.addons = data['addons']
        if 'variants' in data:
            product.variants = data['variants']
        if 'includes' in data:
            product.includes = data['includes']
        if 'relatedProducts' in data:
            product.related_products = data['relatedProducts']
        if 'claims' in data:
            product.claims = data['claims']
        if 'badges' in data:
            product.badges = data['badges']
        if 'featured' in data:
            product.featured = data['featured']
        if 'freeShipping' in data:
            product.free_shipping = data['freeShipping']
        if 'limitedEdition' in data:
            product.limited_edition = data['limitedEdition']
        if 'award' in data:
            product.award = data['award']
        if 'active' in data:
            product.active = data['active']
        if 'displayOrder' in data:
            product.display_order = int(data['displayOrder'])
        if 'shippingCost' in data:
            product.shipping_cost = float(data['shippingCost'])
        if 'preparationCost' in data:
            product.preparation_cost = float(data['preparationCost'])
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'product': product.to_admin_dict()
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/web-products/<int:product_id>', methods=['DELETE'])
@admin_required
@role_required('admin')
def delete_web_product(product_id):
    """Desactiva un producto (soft delete). No lo borra físicamente."""
    from src.models.web_product import WebProduct
    try:
        product = WebProduct.query.get(product_id)
        if not product:
            return jsonify({'error': 'Producto no encontrado'}), 404
        
        product.active = False
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Producto "{product.name}" desactivado',
            'product_id': product_id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/web-products/<int:product_id>/toggle-active', methods=['POST'])
@admin_required
@role_required('admin')
def toggle_web_product_active(product_id):
    """Activa/desactiva un producto."""
    from src.models.web_product import WebProduct
    try:
        product = WebProduct.query.get(product_id)
        if not product:
            return jsonify({'error': 'Producto no encontrado'}), 404
        
        product.active = not product.active
        db.session.commit()
        
        return jsonify({
            'success': True,
            'active': product.active,
            'message': f'Producto "{product.name}" {"activado" if product.active else "desactivado"}'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/web-products/<int:product_id>/image', methods=['POST'])
@admin_required
@role_required('admin')
def upload_product_image(product_id):
    """
    Sube una imagen para un producto a Cloudinary (almacenamiento permanente).
    Acepta multipart/form-data con campo 'image'.
    Las imágenes se almacenan en Cloudinary CDN y nunca se pierden entre deploys.
    """
    from src.models.web_product import WebProduct
    import uuid
    import cloudinary
    import cloudinary.uploader
    
    try:
        product = WebProduct.query.get(product_id)
        if not product:
            return jsonify({'error': 'Producto no encontrado'}), 404
        
        if 'image' not in request.files:
            return jsonify({'error': 'No se envió ninguna imagen'}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'error': 'Nombre de archivo vacío'}), 400
        
        # Validar extensión
        allowed_extensions = {'png', 'jpg', 'jpeg', 'webp', 'avif'}
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in allowed_extensions:
            return jsonify({'error': f'Extensión no permitida. Usa: {allowed_extensions}'}), 400
        
        # Configurar Cloudinary
        cloudinary.config(
            cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME', 'fe9u3inr'),
            api_key=os.environ.get('CLOUDINARY_API_KEY', ''),
            api_secret=os.environ.get('CLOUDINARY_API_SECRET', ''),
            secure=True
        )
        
        # Generar public_id único basado en el slug del producto
        public_id = f"mikels-products/{product.slug}_{uuid.uuid4().hex[:8]}"
        
        # Subir a Cloudinary
        upload_result = cloudinary.uploader.upload(
            file,
            public_id=public_id,
            folder='mikels-products',
            overwrite=True,
            resource_type='image',
            transformation=[
                {'quality': 'auto', 'fetch_format': 'auto'}
            ]
        )
        
        # URL permanente de Cloudinary (con optimización automática)
        image_url = upload_result['secure_url']
        
        # Actualizar DB según el campo 'field' del form
        field = request.form.get('field', 'main')  # 'main' o 'gallery'
        
        if field == 'main':
            product.image = image_url
        else:
            # Añadir a la galería
            current_images = product.images or []
            current_images.append(image_url)
            product.images = current_images
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'image_url': image_url,
            'field': field,
            'product_name': product.name,
            'storage': 'cloudinary'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ============================================================
# STOCK
# ============================================================

@admin_panel_bp.route('/stock', methods=['GET'])
@admin_required
def get_stock():
    """Devuelve el stock actual de todos los productos desde Holded"""
    holded_products = holded_get_products()
    warehouses = holded_get_warehouses()

    stock_data = []
    for p in holded_products:
        if p.get('hasStock', False):
            stock_data.append({
                'holded_id': p.get('id'),
                'name': p.get('name', ''),
                'sku': p.get('sku', ''),
                'stock': p.get('stock', 0),
                'status': _get_stock_status(p.get('stock', 0))
            })

    return jsonify({
        'stock': stock_data,
        'warehouses': [{'id': w.get('id'), 'name': w.get('name')} for w in warehouses],
        'last_updated': datetime.utcnow().isoformat()
    })


# ============================================================
# PEDIDOS
# ============================================================

@admin_panel_bp.route('/orders', methods=['GET'])
@admin_required
def get_orders():
    """Devuelve los pedidos de la web (desde la base de datos local)"""
    from src.models.order import Order
    orders = Order.query.order_by(Order.created_at.desc()).all()

    return jsonify({
        'orders': [o.to_dict() for o in orders] if orders else [],
        'total': len(orders) if orders else 0
    })


@admin_panel_bp.route('/orders/<int:order_id>/create-in-holded', methods=['POST'])
@admin_required
@role_required('admin', 'sales')
def create_order_in_holded(order_id):
    """Crea un pedido de venta en Holded a partir de un pedido web"""
    try:
        from src.models.order import Order
        order = Order.query.get(order_id)

        if not order:
            return jsonify({'error': 'Pedido no encontrado'}), 404

        # Buscar o crear contacto en Holded
        contact_id = holded_get_or_create_contact(
            email=order.customer_email,
            name=order.customer_name,
            phone=order.customer_phone or '',
            address_data={
                'address': order.shipping_address or '',
                'city': order.shipping_city or '',
                'postal_code': order.shipping_postal_code or '',
                'country': 'España'
            }
        )

        if not contact_id:
            return jsonify({'error': 'No se pudo crear/encontrar el contacto en Holded'}), 500

        # Preparar items del pedido
        # item['price'] es el precio unitario CON IVA (viene de Stripe)
        # Holded espera el precio unitario SIN IVA en 'subtotal'
        items = []
        if order.items:
            order_items = json.loads(order.items) if isinstance(order.items, str) else order.items
            for item in order_items:
                price_with_iva = item.get('price', 0)
                # Quitar IVA (4% para AOVE/conservas)
                price_without_iva = round(price_with_iva / 1.04, 2)
                items.append({
                    'name': item.get('name', ''),
                    'description': item.get('description', ''),
                    'units': item.get('quantity', 1),
                    'subtotal': price_without_iva,
                    'tax': 's_iva_4',
                    'sku': item.get('sku', '')
                })

        success, result = holded_create_sales_order(
            contact_id=contact_id,
            items=items,
            notes=f'Pedido web #{order.id} - Stripe: {order.stripe_checkout_session_id or "N/A"}'
        )

        if success:
            # Guardar holded_id en la DB
            order.holded_id = result.get('id', '') if isinstance(result, dict) else str(result)
            db.session.commit()
            return jsonify({'success': True, 'holded_order': result})
        else:
            return jsonify({'error': f'Error creando pedido en Holded: {result}'}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error interno: {str(e)}'}), 500


@admin_panel_bp.route('/orders/<int:order_id>/invoice', methods=['POST'])
@admin_required
@role_required('admin', 'sales')
def create_invoice_in_holded(order_id):
    """
    Genera un documento en Holded según los datos del pedido:
    - Si needs_invoice=True y tiene NIF/razón social → Factura (F)
    - Si needs_invoice=False o no tiene datos fiscales → Ticket (T)
    
    Guarda el ID del documento y número en la DB para no duplicar.
    """
    try:
        from src.models.order import Order
        order = Order.query.get(order_id)

        if not order:
            return jsonify({'error': 'Pedido no encontrado'}), 404
        
        # Verificar si ya se facturó/ticketó
        if order.holded_invoice_id:
            return jsonify({
                'error': f'Este pedido ya tiene documento en Holded: {order.holded_doc_number or order.holded_invoice_id}',
                'already_exists': True,
                'doc_number': order.holded_doc_number
            }), 409

        # Determinar tipo de documento
        doc_type = 'invoice' if (order.needs_invoice and order.fiscal_nif) else 'salesreceipt'
        
        # Buscar o crear contacto en Holded
        # Si tiene datos fiscales, usar razón social; si no, nombre del cliente
        contact_name = order.fiscal_name if (order.needs_invoice and order.fiscal_name) else order.customer_name
        address_data = None
        if order.needs_invoice and order.fiscal_address:
            address_data = {
                'address': order.fiscal_address,
                'city': order.fiscal_city or order.shipping_city or '',
                'postal_code': order.fiscal_postal_code or order.shipping_postal_code or '',
                'country': 'España'
            }
        else:
            address_data = {
                'address': order.shipping_address or '',
                'city': order.shipping_city or '',
                'postal_code': order.shipping_postal_code or '',
                'country': order.shipping_country or 'España'
            }
        
        contact_id = holded_get_or_create_contact(
            email=order.customer_email,
            name=contact_name,
            phone=order.customer_phone or '',
            address_data=address_data
        )

        if not contact_id:
            return jsonify({'error': 'No se pudo crear/encontrar el contacto en Holded'}), 500

        # Si es factura y tiene NIF, actualizar el contacto con el NIF
        if doc_type == 'invoice' and order.fiscal_nif:
            try:
                import requests as req
                req.put(
                    f'{HOLDED_BASE_URL}/contacts/{contact_id}',
                    headers={'key': HOLDED_API_KEY, 'Content-Type': 'application/json'},
                    json={'vatnumber': order.fiscal_nif},
                    timeout=10
                )
            except Exception:
                pass  # No bloquear si falla actualizar NIF

        # Preparar items
        # item['price'] es el precio unitario CON IVA (viene de Stripe)
        # Holded espera el precio unitario SIN IVA en 'subtotal'
        # IVA por tipo de producto:
        #   - Aceites AOVE → 4% (s_iva_4)
        #   - Conservas (paraguayo, nectarina, mermelada) → 10% (s_iva_10)
        #   - Estuches/packaging → 21% (s_iva_21)
        #   - Packs mixtos → 4% (mayoría aceite)
        items = []
        if order.items:
            order_items = json.loads(order.items) if isinstance(order.items, str) else order.items
            for item in order_items:
                price_with_iva = item.get('price', 0)
                item_name = (item.get('name', '') or '').lower()
                
                # Determinar IVA según producto
                if any(kw in item_name for kw in ['paraguayo', 'nectarina', 'mermelada', 'almíbar', 'almibar', 'conserva']):
                    iva_rate = 0.10
                    tax_id = 's_iva_10'
                elif any(kw in item_name for kw in ['estuche']):
                    iva_rate = 0.21
                    tax_id = 's_iva_21'
                else:
                    # Aceites y packs (mayoría aceite) → 4%
                    iva_rate = 0.04
                    tax_id = 's_iva_4'
                
                price_without_iva = round(price_with_iva / (1 + iva_rate), 2)
                items.append({
                    'name': item.get('name', ''),
                    'units': item.get('quantity', 1),
                    'subtotal': price_without_iva,
                    'tax': tax_id,
                    'sku': item.get('sku', '')
                })

        # Crear documento según tipo
        if doc_type == 'invoice':
            notes = f'Factura pedido web #{order.order_number} | NIF: {order.fiscal_nif}'
            success, result = holded_create_invoice(
                contact_id=contact_id,
                items=items,
                notes=notes
            )
        else:
            notes = f'Ticket pedido web #{order.order_number}'
            success, result = holded_create_salesreceipt(
                contact_id=contact_id,
                items=items,
                notes=notes
            )

        if success:
            # Guardar referencia en la DB
            doc_id = result.get('id', '')
            doc_number = result.get('docNumber', '') or result.get('num', '') or result.get('invoiceNum', '')
            
            # Si no viene docNumber en la respuesta, obtenerlo con GET al documento
            if not doc_number and doc_id:
                try:
                    import requests as req
                    doc_detail = req.get(
                        f'{HOLDED_BASE_URL}/documents/{doc_type}/{doc_id}',
                        headers={'key': HOLDED_API_KEY, 'Content-Type': 'application/json'},
                        timeout=10
                    )
                    if doc_detail.status_code == 200:
                        detail_data = doc_detail.json()
                        doc_number = detail_data.get('docNumber', '') or detail_data.get('invoiceNum', '') or detail_data.get('num', '')
                        print(f"[Holded] DocNumber obtenido via GET: {doc_number}")
                except Exception as detail_err:
                    print(f"[Holded] No se pudo obtener docNumber: {detail_err}")
            
            order.holded_invoice_id = doc_id
            order.holded_doc_number = doc_number
            try:
                db.session.commit()
                print(f"✅ Order {order.order_number} - {doc_type} created in Holded: {doc_number}")
            except Exception as db_err:
                print(f"⚠️ Error saving holded ref to DB: {db_err}")
            
            return jsonify({
                'success': True,
                'doc_type': doc_type,
                'doc_type_label': 'Factura' if doc_type == 'invoice' else 'Ticket',
                'doc_number': doc_number,
                'holded_id': doc_id
            })
        else:
            return jsonify({'error': f'Error creando {"factura" if doc_type == "invoice" else "ticket"} en Holded: {result}'}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error interno: {str(e)}'}), 500


@admin_panel_bp.route('/orders/<int:order_id>/reset-holded', methods=['POST'])
@admin_required
@role_required('admin')
def reset_order_holded(order_id):
    """Resetea los campos de Holded de un pedido para poder re-procesarlo"""
    try:
        from src.models.order import Order
        order = Order.query.get(order_id)
        if not order:
            return jsonify({'error': 'Pedido no encontrado'}), 404
        order.holded_id = None
        order.holded_invoice_id = None
        order.holded_doc_number = None
        db.session.commit()
        return jsonify({'success': True, 'message': f'Pedido {order.order_number} reseteado de Holded'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/orders/<int:order_id>/send-email', methods=['POST'])
@admin_required
@role_required('admin', 'sales')
def send_order_document_email(order_id):
    """
    Envía la factura/ticket del pedido por email al cliente.
    Usa la API de Holded para enviar el documento.
    """
    try:
        from src.models.order import Order
        order = Order.query.get(order_id)

        if not order:
            return jsonify({'error': 'Pedido no encontrado'}), 404

        if not order.holded_invoice_id:
            return jsonify({'error': 'Este pedido no tiene documento en Holded. Genera la factura/ticket primero.'}), 400

        # Determinar tipo de documento
        doc_type = 'invoice' if (order.needs_invoice and order.fiscal_nif) else 'salesreceipt'

        # Email del cliente
        customer_email = order.customer_email
        if not customer_email:
            return jsonify({'error': 'El pedido no tiene email de cliente'}), 400

        # Datos opcionales del body
        data = request.get_json(silent=True) or {}
        
        # Nombre del cliente para personalizar
        customer_name = order.customer_name or 'cliente'
        first_name = customer_name.split()[0] if customer_name else 'cliente'
        
        # Subject y message por defecto con CTA de reseña
        default_subject = f'Tu factura de Mikel\'s Earth - Gracias por tu compra, {first_name} \U0001f33f'
        default_message = (
            f'<p>Hola {first_name},</p>'
            f'<p>Te adjuntamos tu factura. Esperamos que disfrutes de nuestros productos '
            f'tanto como nosotros disfrutamos elabor\u00e1ndolos.</p>'
            f'<p>Si tienes un momento, nos encantar\u00eda conocer tu opini\u00f3n. '
            f'Puedes dejarnos tu rese\u00f1a aqu\u00ed:</p>'
            f'<p style="text-align:center;"><a href="https://www.mikels.es/opiniones" '
            f'style="display:inline-block;padding:12px 24px;background-color:#4a7c59;color:#ffffff;'
            f'text-decoration:none;border-radius:6px;font-weight:bold;">'
            f'Dejar mi opini\u00f3n \U0001f33f</a></p>'
            f'<p>Tu experiencia nos ayuda a seguir mejorando y a que m\u00e1s personas '
            f'descubran nuestro aceite y conservas artesanales.</p>'
            f'<p>\u00a1Gracias por confiar en Mikel\'s Earth!<br>'
            f'El equipo de Mikel\'s Earth</p>'
        )
        
        subject = data.get('subject', None) or default_subject
        message = data.get('message', None) or default_message

        # Enviar al cliente + copia a admin
        all_emails = [customer_email, 'adm@farmsplanet.es']

        success, result = holded_send_document_email(
            doc_type=doc_type,
            doc_id=order.holded_invoice_id,
            emails=all_emails,
            subject=subject,
            message=message
        )

        if success:
            # Marcar como enviado en la DB
            order.email_sent = True
            try:
                db.session.commit()
            except Exception as db_err:
                print(f"\u26a0\ufe0f Error guardando email_sent: {db_err}")
            
            return jsonify({
                'success': True,
                'message': f'Documento enviado por email a {customer_email} (copia a adm@farmsplanet.es)',
                'email': customer_email
            })
        else:
            return jsonify({'error': f'Error enviando email desde Holded: {result}'}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error interno: {str(e)}'}), 500


@admin_panel_bp.route('/orders/sync-stripe', methods=['POST'])
@admin_required
@role_required('admin', 'sales')
def sync_stripe_refunds():
    """
    Sincroniza el estado de pago de todos los pedidos con Stripe.
    Detecta reembolsos que se hicieron directamente en Stripe.
    """
    try:
        import stripe
        from src.models.order import Order
        
        orders = Order.query.filter(
            Order.stripe_payment_intent_id.isnot(None),
            Order.stripe_payment_intent_id != '',
            Order.payment_status.in_(['paid', 'pending'])
        ).all()
        
        updated = []
        errors = []
        
        for order in orders:
            try:
                # Expandir charges para poder ver reembolsos
                pi = stripe.PaymentIntent.retrieve(
                    order.stripe_payment_intent_id,
                    expand=['latest_charge']
                )
                
                # Verificar si el PI está cancelado
                if pi.status == 'canceled':
                    order.payment_status = 'cancelled'
                    order.order_status = 'cancelled'
                    order.admin_notes = (order.admin_notes or '') + f'\nSincronizado: Pago cancelado en Stripe - {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}'
                    updated.append({'order': order.order_number, 'new_status': 'cancelled'})
                else:
                    # Obtener el charge (latest_charge o via charges.data)
                    charge = None
                    if hasattr(pi, 'latest_charge') and pi.latest_charge and isinstance(pi.latest_charge, object) and hasattr(pi.latest_charge, 'refunded'):
                        charge = pi.latest_charge
                    elif hasattr(pi, 'charges') and pi.charges and pi.charges.data:
                        charge = pi.charges.data[0]
                    
                    if charge:
                        if charge.refunded:
                            order.payment_status = 'refunded'
                            order.order_status = 'cancelled'
                            refund_amount = charge.amount_refunded / 100
                            order.admin_notes = (order.admin_notes or '') + f'\nSincronizado: Reembolso total {refund_amount}\u20ac detectado - {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}'
                            updated.append({'order': order.order_number, 'new_status': 'refunded', 'amount': refund_amount})
                        elif charge.amount_refunded > 0:
                            order.payment_status = 'partially_refunded'
                            refund_amount = charge.amount_refunded / 100
                            order.admin_notes = (order.admin_notes or '') + f'\nSincronizado: Reembolso parcial {refund_amount}\u20ac detectado - {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}'
                            updated.append({'order': order.order_number, 'new_status': 'partially_refunded', 'amount': refund_amount})
            except Exception as e:
                errors.append({'order': order.order_number, 'error': str(e)})
        
        if updated:
            db.session.commit()
        
        return jsonify({
            'success': True,
            'synced': len(orders),
            'updated': updated,
            'errors': errors
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error sincronizando: {str(e)}'}), 500


@admin_panel_bp.route('/orders/fix-prices', methods=['POST'])
@admin_required
@role_required('admin')
def fix_order_prices():
    """
    Recorre todos los pedidos con stripe_session_id y recalcula los precios unitarios
    de los items consultando Stripe directamente.
    Corrige el bug donde se guardaba amount_total en vez del precio unitario.
    """
    try:
        import stripe
        from src.models.order import Order
        
        orders = Order.query.filter(
            Order.stripe_checkout_session_id.isnot(None),
            Order.stripe_checkout_session_id != ''
        ).all()
        
        fixed = 0
        errors = []
        
        for order in orders:
            try:
                # Obtener line_items de Stripe
                line_items = stripe.checkout.Session.list_line_items(
                    order.stripe_checkout_session_id, limit=100
                )
                
                new_items = []
                for item in line_items.data:
                    unit_price = (item.amount_total / 100) / item.quantity if item.quantity else item.amount_total / 100
                    new_items.append({
                        'name': item.description,
                        'quantity': item.quantity,
                        'price': round(unit_price, 2)
                    })
                
                if new_items:
                    order.items = new_items
                    fixed += 1
                    
            except Exception as e:
                errors.append({'order_number': order.order_number, 'error': str(e)})
                continue
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'total_orders': len(orders),
            'fixed': fixed,
            'errors': errors
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/orders/sync-doc-numbers', methods=['POST'])
@admin_required
@role_required('admin')
def sync_doc_numbers():
    """
    Sincroniza los números de documento (docNumber) desde Holded
    para pedidos que tienen holded_invoice_id pero no tienen holded_doc_number.
    """
    try:
        import requests as req
        from src.models.order import Order
        
        orders = Order.query.filter(
            Order.holded_invoice_id.isnot(None),
            Order.holded_invoice_id != '',
            (Order.holded_doc_number.is_(None) | (Order.holded_doc_number == ''))
        ).all()
        
        updated = 0
        errors = []
        
        for order in orders:
            try:
                doc_type = 'invoice' if (order.needs_invoice and order.fiscal_nif) else 'salesreceipt'
                response = req.get(
                    f'{HOLDED_BASE_URL}/documents/{doc_type}/{order.holded_invoice_id}',
                    headers={'key': HOLDED_API_KEY, 'Content-Type': 'application/json'},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    doc_number = data.get('docNumber', '') or data.get('invoiceNum', '') or data.get('num', '')
                    if doc_number:
                        order.holded_doc_number = doc_number
                        updated += 1
                else:
                    errors.append({'order': order.order_number, 'error': f'HTTP {response.status_code}'})
            except Exception as e:
                errors.append({'order': order.order_number, 'error': str(e)})
                continue
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'total_pending': len(orders),
            'updated': updated,
            'errors': errors
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================
# CONTACTOS / CLIENTES
# ============================================================

@admin_panel_bp.route('/clients', methods=['GET'])
@admin_required
def get_clients():
    """Devuelve clientes en dos categorías:
    - b2b: Contactos de Holded (B2B/HORECA/Contado)
    - web: Clientes de la DB local (pedidos online por Stripe)
    """
    from src.models.order import Order
    from sqlalchemy import func
    
    # === B2B / CONTADO: Clientes de Holded ===
    contacts = holded_get_contacts()
    holded_clients = [c for c in contacts if c.get('type') == 'client']
    
    # Descargar todas las facturas para calcular total por contacto
    try:
        all_invoices = holded_get_contact_invoices(None)  # None = todas
        all_salesreceipts = holded_get_all_salesreceipts()
        
        # Sumar totales por contacto
        invoiced_by_contact = {}
        for inv in all_invoices:
            cid = inv.get('contact')
            if cid:
                invoiced_by_contact[cid] = invoiced_by_contact.get(cid, 0) + float(inv.get('total', 0) or 0)
        for sr in all_salesreceipts:
            cid = sr.get('contact')
            if cid:
                invoiced_by_contact[cid] = invoiced_by_contact.get(cid, 0) + float(sr.get('total', 0) or 0)
    except Exception as e:
        print(f'Warning: Error calculando totales facturados: {e}')
        invoiced_by_contact = {}
    
    b2b_list = [{
        'id': c.get('id'),
        'name': c.get('name', ''),
        'email': c.get('email') or '',
        'phone': c.get('phone') or c.get('mobile') or '',
        'total_invoiced': invoiced_by_contact.get(c.get('id'), 0),
        'source': 'holded'
    } for c in holded_clients]
    
    # === WEB: Clientes de la DB local (Stripe) ===
    # Agrupar pedidos por email para obtener clientes únicos
    web_clients_query = db.session.query(
        Order.customer_email,
        Order.customer_name,
        func.count(Order.id).label('order_count'),
        func.sum(Order.total).label('total_spent'),
        func.max(Order.created_at).label('last_order')
    ).filter(
        Order.customer_email.isnot(None),
        Order.customer_email != ''
    ).group_by(
        Order.customer_email,
        Order.customer_name
    ).order_by(
        func.max(Order.created_at).desc()
    ).all()
    
    web_list = [{
        'id': f'web_{wc.customer_email}',
        'email': wc.customer_email,
        'name': wc.customer_name or wc.customer_email.split('@')[0],
        'order_count': wc.order_count,
        'total_spent': float(wc.total_spent or 0),
        'last_order': wc.last_order.isoformat() if wc.last_order else None,
        'source': 'web'
    } for wc in web_clients_query]
    
    return jsonify({
        'b2b': b2b_list,
        'web': web_list,
        'total_b2b': len(b2b_list),
        'total_web': len(web_list)
    })


@admin_panel_bp.route('/clients/<client_id>', methods=['GET'])
@admin_required
def get_client_detail(client_id):
    """Devuelve el detalle de un cliente.
    - Si client_id empieza por 'web_' → es un cliente web (DB local)
    - Si no → es un cliente B2B/Contado (Holded)
    """
    from src.models.order import Order
    
    try:
        return _get_client_detail_inner(client_id, Order)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error interno: {str(e)}'}), 500


def _get_client_detail_inner(client_id, Order):
    # ==========================================
    # CLIENTE WEB (DB local / Stripe)
    # ==========================================
    if client_id.startswith('web_'):
        client_email = client_id[4:]  # Quitar prefijo 'web_'
        
        # Obtener todos los pedidos de este email
        orders = Order.query.filter_by(customer_email=client_email).order_by(Order.created_at.desc()).all()
        if not orders:
            return jsonify({'error': 'Cliente web no encontrado'}), 404
        
        # Datos del cliente (del primer pedido)
        first_order = orders[0]
        client_data = {
            'id': client_id,
            'name': first_order.customer_name or client_email.split('@')[0],
            'email': client_email,
            'phone': first_order.customer_phone or '',
            'address': first_order.shipping_address or '',
            'city': first_order.shipping_city or '',
            'postal_code': first_order.shipping_postal_code or '',
            'province': '',
            'country': first_order.shipping_country or '',
            'source': 'web'
        }
        
        # Buscar si tiene tickets T en Holded (cruce por nombre/email)
        matched_tickets = []
        try:
            all_tickets = holded_get_all_salesreceipts()
            holded_contact = holded_find_contact_by_email(client_email)
            if holded_contact:
                holded_cid = holded_contact.get('id')
                matched_tickets = [t for t in all_tickets if t.get('contact') == holded_cid]
        except Exception as e:
            print(f'Warning: Error buscando tickets en Holded para {client_email}: {e}')
        
        # Procesar pedidos web
        web_orders = []
        for o in orders:
            # Intentar encontrar ticket T correspondiente (por fecha ±1 día)
            ticket_match = None
            if o.created_at:
                order_ts = int(o.created_at.timestamp())
                for t in matched_tickets:
                    t_date = t.get('date', 0)
                    if abs(t_date - order_ts) <= 86400:
                        ticket_match = t.get('docNumber')
                        break
            
            web_orders.append({
                'id': o.id,
                'order_number': o.order_number,
                'date': o.created_at.isoformat() if o.created_at else None,
                'total': float(o.total or 0),
                'status': o.status,
                'payment_status': o.payment_status,
                'items': o.items if o.items else [],
                'ticket_holded': ticket_match,  # None = pendiente de generar
                'has_ticket': ticket_match is not None
            })
        
        # Estadísticas
        total_spent = sum(float(o.total or 0) for o in orders)
        tickets_generated = len([wo for wo in web_orders if wo['has_ticket']])
        tickets_pending = len(web_orders) - tickets_generated
        
        return jsonify({
            'client': client_data,
            'orders': web_orders,
            'stats': {
                'total_orders': len(orders),
                'total_spent': total_spent,
                'tickets_generated': tickets_generated,
                'tickets_pending': tickets_pending
            },
            'source': 'web'
        })
    
    # ==========================================
    # CLIENTE B2B / CONTADO (Holded)
    # ==========================================
    contact = holded_get_contact(client_id)
    if not contact:
        return jsonify({'error': 'Cliente no encontrado en Holded'}), 404
    
    # Obtener facturas, pedidos de venta Y tickets del contacto
    invoices = holded_get_contact_invoices(client_id)
    salesorders = holded_get_contact_salesorders(client_id)
    salesreceipts = holded_get_contact_salesreceipts(client_id)
    
    def process_document(doc, doc_type):
        """Procesa un documento de Holded y lo formatea para el frontend."""
        items = []
        for item in (doc.get('items') or doc.get('products') or []):
            price = float(item.get('price', 0) or 0)
            units = float(item.get('units', 1) or 1)
            discount = float(item.get('discount', 0) or 0)
            subtotal = price * units * (1 - discount / 100)
            items.append({
                'name': item.get('name', ''),
                'desc': item.get('desc', ''),
                'units': units,
                'price': price,
                'subtotal': round(subtotal, 2),
                'discount': discount
            })
        
        doc_number = doc.get('docNumber') or ''
        is_ticket = doc_number.startswith('T') if doc_number else False
        is_draft = doc.get('draft', False)
        
        return {
            'id': doc.get('id'),
            'type': doc_type,
            'is_ticket': is_ticket,
            'is_draft': is_draft,
            'number': doc_number or '(Borrador)',
            'date': doc.get('date'),
            'total': doc.get('total', 0),
            'subtotal': doc.get('subtotal', 0),
            'status': doc.get('status', ''),
            'notes': doc.get('notes', ''),
            'desc': doc.get('desc', ''),
            'items': items,
            'currency': doc.get('currency', 'EUR')
        }
    
    # Procesar todos los documentos
    processed_invoices = [process_document(inv, 'invoice') for inv in invoices]
    processed_salesorders = [process_document(so, 'salesorder') for so in salesorders]
    processed_salesreceipts = [process_document(sr, 'salesreceipt') for sr in salesreceipts]
    
    # Combinar y ordenar por fecha (más reciente primero)
    all_documents = processed_invoices + processed_salesorders + processed_salesreceipts
    all_documents.sort(key=lambda d: d.get('date') or 0, reverse=True)
    
    # Estadísticas
    total_invoices = sum(d['total'] for d in processed_invoices)
    total_tickets = sum(d['total'] for d in processed_salesreceipts)
    total_salesorders = sum(d['total'] for d in processed_salesorders)
    
    # Datos del contacto
    bill_address = contact.get('billAddress') or {}
    
    return jsonify({
        'client': {
            'id': contact.get('id'),
            'name': contact.get('name', ''),
            'email': contact.get('email') or '',
            'phone': contact.get('phone') or '',
            'mobile': contact.get('mobile') or '',
            'vatnumber': contact.get('vatnumber', ''),
            'address': bill_address.get('address', ''),
            'city': bill_address.get('city', ''),
            'postal_code': bill_address.get('postalCode', ''),
            'province': bill_address.get('province', ''),
            'country': bill_address.get('country', ''),
            'total_invoiced': total_invoices + total_tickets + total_salesorders,
            'created_at': contact.get('createdAt'),
            'notes': contact.get('notes', ''),
            'source': 'holded'
        },
        'documents': all_documents,
        'stats': {
            'total_documents': len(all_documents),
            'count_invoices': len(processed_invoices),
            'count_tickets': len(processed_salesreceipts),
            'count_salesorders': len(processed_salesorders),
            'total_invoices': total_invoices,
            'total_tickets': total_tickets,
            'total_salesorders': total_salesorders,
            'total_all': total_invoices + total_tickets + total_salesorders
        },
        'source': 'holded'
    })


# ============================================================
# DOCUMENTO INDIVIDUAL (para cargar items bajo demanda)
# ============================================================

@admin_panel_bp.route('/documents/<doc_type>/<doc_id>', methods=['GET'])
@admin_required
def get_document_detail(doc_type, doc_id):
    """Obtiene un documento individual de Holded con sus items/products.
    doc_type: invoice, salesorder, salesreceipt"""
    try:
        doc = holded_get_document(doc_type, doc_id)
        if not doc:
            return jsonify({'error': 'Documento no encontrado'}), 404
        
        # Procesar items
        items = []
        for item in (doc.get('items') or doc.get('products') or []):
            price = float(item.get('price', 0) or 0)
            units = float(item.get('units', 1) or 1)
            discount = float(item.get('discount', 0) or 0)
            subtotal = price * units * (1 - discount / 100)
            items.append({
                'name': item.get('name', ''),
                'desc': item.get('desc', ''),
                'units': units,
                'price': price,
                'subtotal': round(subtotal, 2),
                'discount': discount
            })
        
        return jsonify({
            'id': doc.get('id'),
            'docNumber': doc.get('docNumber') or '(Borrador)',
            'total': doc.get('total', 0),
            'subtotal': doc.get('subtotal', 0),
            'items': items
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# DASHBOARD
# ============================================================

@admin_panel_bp.route('/dashboard', methods=['GET'])
@admin_required
def get_dashboard():
    """Devuelve datos resumidos para el dashboard del admin"""
    # Datos locales (con protección por si las tablas no existen)
    total_orders = 0
    total_reviews = 0
    recent_orders = []
    try:
        from src.models.order import Order
        total_orders = Order.query.count()
        # Últimos 5 pedidos
        latest = Order.query.order_by(Order.created_at.desc()).limit(5).all()
        recent_orders = [{
            'id': o.id,
            'order_number': o.order_number,
            'email': o.customer_email,
            'customer_name': o.customer_name,
            'total': o.total,
            'status': o.status,
            'date': o.created_at.isoformat() if o.created_at else None
        } for o in latest]
    except Exception as e:
        print(f'[Dashboard] Error cargando pedidos: {e}')

    try:
        from src.models.review import Review
        total_reviews = Review.query.count()
    except Exception as e:
        print(f'[Dashboard] Error cargando reseñas: {e}')

    # Datos de Holded (con protección por timeout)
    holded_products = []
    low_stock = []
    holded_status = 'connected'
    try:
        holded_products = holded_get_products()
        low_stock = [p for p in holded_products if p.get('hasStock') and p.get('stock', 0) < 50 and p.get('stock', 0) >= 0]
    except Exception as e:
        holded_status = 'error'
        print(f'[Dashboard] Error conectando con Holded: {e}')

    # Notificaciones de producto pendientes
    total_notifications = 0
    try:
        from src.models.product_notification import ProductNotification
        total_notifications = ProductNotification.query.filter_by(notified=False).count()
    except Exception as e:
        print(f'[Dashboard] Error cargando notificaciones: {e}')

    # Carritos abandonados
    total_abandoned = 0
    try:
        from src.models.abandoned_cart import AbandonedCart
        total_abandoned = AbandonedCart.query.filter_by(recovered=False).count()
    except Exception as e:
        print(f'[Dashboard] Error cargando carritos: {e}')

    return jsonify({
        'total_orders': total_orders,
        'total_reviews': total_reviews,
        'total_products_holded': len(holded_products),
        'total_notifications': total_notifications,
        'total_abandoned_carts': total_abandoned,
        'low_stock_alerts': [{
            'name': p.get('name'),
            'sku': p.get('sku'),
            'stock': p.get('stock')
        } for p in low_stock],
        'recent_orders': recent_orders,
        'holded_status': holded_status,
        'last_updated': datetime.utcnow().isoformat()
    })


# ============================================================
# CUPONES
# ============================================================

@admin_panel_bp.route('/coupons', methods=['GET'])
@admin_required
def get_coupons():
    """Listar todos los cupones con clasificación, estadísticas y datos de uso (incluye Stripe)"""
    from src.models.coupon import Coupon
    from src.models.order import Order
    from collections import defaultdict
    import stripe
    
    show_all = request.args.get('all', 'true').lower() == 'true'
    
    if show_all:
        coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    else:
        coupons = Coupon.query.filter_by(active=True).order_by(Coupon.created_at.desc()).all()
    
    # Obtener datos históricos de Stripe para cupones manuales
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    stripe_usage_by_name = {}
    try:
        coupons_list = stripe.Coupon.list(limit=100)
        for coup in coupons_list.auto_paging_iter():
            coup_name = (coup.name or '').upper()
            if coup_name:
                if coup_name not in stripe_usage_by_name:
                    stripe_usage_by_name[coup_name] = {
                        'total_redeemed': 0,
                        'total_amount_off_cents': 0,
                    }
                stripe_usage_by_name[coup_name]['total_redeemed'] += coup.times_redeemed
                if coup.amount_off:
                    stripe_usage_by_name[coup_name]['total_amount_off_cents'] += coup.amount_off * coup.times_redeemed
    except Exception:
        pass  # Si falla Stripe, seguimos con datos locales
    
    # Clasificar cupones por tipo basándose en prefijo/email/contexto
    def classify_coupon(c):
        code_lower = c.code.lower() if c.code else ''
        email = (c.email or '').lower()
        desc = (c.description or '').lower()
        if code_lower.startswith('mikels10-') or code_lower.startswith('mikels-'):
            return 'newsletter'
        if code_lower.startswith('vuelve10-') or code_lower.startswith('vuelve-'):
            return 'post_compra'
        if code_lower.startswith('gracias10-') or code_lower.startswith('gracias-'):
            return 'post_compra'
        if email and email.startswith('review-'):
            return 'resena'
        if 'review' in desc or 'reseña' in desc or 'rese' in desc:
            return 'resena'
        if 'newsletter' in desc and 'bienvenida' not in code_lower:
            return 'newsletter'
        if 'post-compra' in desc or 'post compra' in desc or 'vuelve' in desc:
            return 'post_compra'
        return 'manual'
    
    # Enriquecer datos de cada cupón
    enriched_coupons = []
    stats_by_month = defaultdict(lambda: {'count': 0, 'estimated_savings': 0})
    total_savings = 0
    category_stats = defaultdict(lambda: {'total': 0, 'used': 0, 'active': 0, 'savings': 0})
    
    for c in coupons:
        data = c.to_dict()
        category = classify_coupon(c)
        data['category'] = category
        
        # Obtener usos: priorizar datos de Stripe para cupones manuales
        code_upper = (c.code or '').upper()
        stripe_data = stripe_usage_by_name.get(code_upper)
        
        uses = c.current_uses or 0
        if c.used and uses == 0:
            uses = 1
        
        # Para cupones manuales, usar datos de Stripe si tienen más usos
        real_savings = 0
        if stripe_data and stripe_data['total_redeemed'] > uses:
            uses = stripe_data['total_redeemed']
            real_savings = stripe_data['total_amount_off_cents'] / 100  # cents to euros
        
        # Calcular ahorro
        avg_order = 45.0
        if real_savings > 0:
            # Datos reales de Stripe
            estimated_total_savings = round(real_savings, 2)
            estimated_per_use = round(real_savings / uses, 2) if uses > 0 else 0
            data['savings_source'] = 'stripe'
        else:
            # Estimación
            if c.discount_type == 'percentage':
                estimated_per_use = round(avg_order * (c.discount_value / 100), 2)
            else:
                estimated_per_use = c.discount_value
            estimated_total_savings = round(estimated_per_use * uses, 2)
            data['savings_source'] = 'estimated'
        
        data['estimated_savings'] = estimated_total_savings
        data['estimated_per_use'] = estimated_per_use
        data['total_uses'] = uses
        
        # Determinar si es de un solo uso
        data['single_use'] = (c.max_uses == 1) or (c.email is not None and c.max_uses is None)
        
        # Acumular estadísticas
        total_savings += estimated_total_savings
        category_stats[category]['total'] += 1
        category_stats[category]['savings'] += estimated_total_savings
        if c.active:
            category_stats[category]['active'] += 1
        if uses > 0:
            category_stats[category]['used'] += 1
        
        # Stats por mes (basado en created_at)
        if c.created_at:
            month_key = c.created_at.strftime('%Y-%m')
            if uses > 0:
                stats_by_month[month_key]['count'] += uses
                stats_by_month[month_key]['estimated_savings'] += estimated_total_savings
        
        enriched_coupons.append(data)
    
    # Ordenar stats por mes
    monthly_stats = []
    for month, stats in sorted(stats_by_month.items(), reverse=True):
        monthly_stats.append({
            'month': month,
            'uses': stats['count'],
            'estimated_savings': round(stats['estimated_savings'], 2)
        })
    
    return jsonify({
        'coupons': enriched_coupons,
        'total': len(enriched_coupons),
        'stats': {
            'total_coupons': len(coupons),
            'active_coupons': sum(1 for c in coupons if c.active),
            'used_coupons': sum(1 for c in coupons if (c.current_uses or 0) > 0 or c.used),
            'total_estimated_savings': round(total_savings, 2),
            'avg_order_estimate': 45.0,
            'by_category': dict(category_stats),
            'by_month': monthly_stats
        }
    })


@admin_panel_bp.route('/coupons', methods=['POST'])
@admin_required
def create_coupon():
    """Crear un nuevo cupón"""
    from src.models.coupon import Coupon
    
    data = request.json
    code = data.get('code', '').strip()
    
    if not code:
        return jsonify({'error': 'El código del cupón es obligatorio'}), 400
    
    # Verificar que no exista
    existing = Coupon.query.filter(db.func.lower(Coupon.code) == code.lower()).first()
    if existing:
        return jsonify({'error': f'Ya existe un cupón con el código "{code}"'}), 409
    
    # Parsear fecha de caducidad
    expires_at = None
    if data.get('expires_at'):
        try:
            expires_at = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            try:
                expires_at = datetime.strptime(data['expires_at'], '%Y-%m-%d')
            except:
                pass
    
    coupon = Coupon(
        code=code,
        description=data.get('description', ''),
        discount_type=data.get('discount_type', 'percentage'),
        discount_value=float(data.get('discount_value', 10)),
        min_order_amount=float(data.get('min_order_amount', 0)),
        max_uses=data.get('max_uses'),  # None = ilimitado
        max_uses_per_customer=data.get('max_uses_per_customer'),
        active=data.get('active', True),
        expires_at=expires_at,
        email=data.get('email'),  # None = cupón público
    )
    
    db.session.add(coupon)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Cupón "{code}" creado correctamente',
        'coupon': coupon.to_dict()
    }), 201


@admin_panel_bp.route('/coupons/<int:coupon_id>', methods=['PUT'])
@admin_required
def update_coupon(coupon_id):
    """Actualizar un cupón existente"""
    from src.models.coupon import Coupon
    
    coupon = Coupon.query.get(coupon_id)
    if not coupon:
        return jsonify({'error': 'Cupón no encontrado'}), 404
    
    data = request.json
    
    if 'code' in data:
        new_code = data['code'].strip()
        existing = Coupon.query.filter(
            db.func.lower(Coupon.code) == new_code.lower(),
            Coupon.id != coupon_id
        ).first()
        if existing:
            return jsonify({'error': f'Ya existe otro cupón con el código "{new_code}"'}), 409
        coupon.code = new_code
    
    if 'description' in data:
        coupon.description = data['description']
    if 'discount_type' in data:
        coupon.discount_type = data['discount_type']
    if 'discount_value' in data:
        coupon.discount_value = float(data['discount_value'])
    if 'min_order_amount' in data:
        coupon.min_order_amount = float(data['min_order_amount'])
    if 'max_uses' in data:
        coupon.max_uses = data['max_uses']
    if 'max_uses_per_customer' in data:
        coupon.max_uses_per_customer = data['max_uses_per_customer']
    if 'active' in data:
        coupon.active = data['active']
    if 'expires_at' in data:
        if data['expires_at']:
            try:
                coupon.expires_at = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                try:
                    coupon.expires_at = datetime.strptime(data['expires_at'], '%Y-%m-%d')
                except:
                    pass
        else:
            coupon.expires_at = None
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Cupón "{coupon.code}" actualizado',
        'coupon': coupon.to_dict()
    })


@admin_panel_bp.route('/coupons/<int:coupon_id>/toggle', methods=['POST'])
@admin_required
def toggle_coupon(coupon_id):
    """Activar/desactivar un cupón"""
    from src.models.coupon import Coupon
    
    coupon = Coupon.query.get(coupon_id)
    if not coupon:
        return jsonify({'error': 'Cupón no encontrado'}), 404
    
    coupon.active = not coupon.active
    db.session.commit()
    
    status = 'activado' if coupon.active else 'desactivado'
    return jsonify({
        'success': True,
        'message': f'Cupón "{coupon.code}" {status}',
        'coupon': coupon.to_dict()
    })


@admin_panel_bp.route('/coupons/<int:coupon_id>', methods=['DELETE'])
@admin_required
def delete_coupon(coupon_id):
    """Eliminar un cupón"""
    from src.models.coupon import Coupon
    
    coupon = Coupon.query.get(coupon_id)
    if not coupon:
        return jsonify({'error': 'Cupón no encontrado'}), 404
    
    code = coupon.code
    db.session.delete(coupon)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Cupón "{code}" eliminado'
    })


@admin_panel_bp.route('/coupons/validate', methods=['POST'])
@admin_required
def admin_validate_coupon():
    """Validar un cupón (para testing desde el admin)"""
    from src.models.coupon import Coupon
    
    data = request.json
    code = data.get('code', '').strip()
    order_amount = float(data.get('order_amount', 0))
    email = data.get('email')
    
    is_valid, result = Coupon.validate_coupon(code, email)
    
    if is_valid:
        coupon = result
        discount = coupon.calculate_discount(order_amount)
        return jsonify({
            'valid': True,
            'coupon': coupon.to_dict(),
            'discount_amount': discount,
            'final_amount': order_amount - discount
        })
    else:
        return jsonify({
            'valid': False,
            'error': result
        })


# ============================================================
# UTILIDADES INTERNAS
# ============================================================

# Composición de packs: SKU del pack → lista de componentes
# Cada componente tiene: sku (o id para manuales), name, quantity
# Si 'manual': True → el coste se guarda en pack_component_costs.json
# Si tiene 'sku' válido → el coste se obtiene de Holded automáticamente
PACK_COMPONENTS = {
    'MIKPACK01': [  # Pack Degustación Premium (9€)
        {'sku': 'MIKPARJ250', 'name': 'Mermelada de Paraguayo 250g', 'quantity': 1},
        {'id': 'botellas_degustacion', 'name': '4x Botellas aceite 14ml (muestras)', 'quantity': 1, 'manual': True},
    ],
    'MIKEST01': [  # Estuche de Regalo Premium (5€)
        {'id': 'estuche_cilindrico', 'name': 'Estuche cilíndrico cartón', 'quantity': 1, 'manual': True},
    ],
    'MIKPACKFR': [  # Pack Fruta Premium (35€)
        {'sku': 'MIKPARA450', 'name': 'Paraguayo en Almíbar 720g', 'quantity': 1},
        {'sku': 'MIKNECT450', 'name': 'Nectarina en Almíbar 720g', 'quantity': 1},
        {'sku': 'MIKPARJ250', 'name': 'Mermelada de Paraguayo 250g', 'quantity': 1},
        {'id': 'estuche_madera', 'name': 'Estuche de madera premium', 'quantity': 1, 'manual': True},
    ],
    'MIKPACKTP': [  # Pack Temprano Premium (19€)
        {'sku': 'MIKVET500', 'name': 'Aceite Temprano 500ml sin filtrar', 'quantity': 1},
        {'sku': 'MIKEST01', 'name': 'Estuche premium temprano', 'quantity': 1},
    ],
    'MIKPACKCO': [  # Pack Completo Mikel's Earth (81.90€)
        {'sku': 'MIKVE5LP', 'name': 'Aceite de Oliva Virgen Extra 5L', 'quantity': 1},
        {'sku': 'MIKVET500', 'name': 'Aceite Temprano 500ml sin filtrar', 'quantity': 1},
        {'sku': 'MIKPARA450', 'name': 'Paraguayo en Almíbar 720g', 'quantity': 1},
        {'sku': 'MIKNECT450', 'name': 'Nectarina en Almíbar 720g', 'quantity': 1},
        {'sku': 'MIKPARJ250', 'name': 'Mermelada de Paraguayo 250g', 'quantity': 1},
        {'id': 'botellas_degustacion', 'name': '4x Botellas aceite 14ml (muestras)', 'quantity': 1, 'manual': True},
        {'id': 'estuche_kraft', 'name': 'Estuche kraft premium', 'quantity': 1, 'manual': True},
    ],
}


def _get_pack_component_costs():
    """Lee los costes manuales de componentes de pack desde JSON."""
    costs_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'pack_component_costs.json')
    try:
        if os.path.exists(costs_file):
            with open(costs_file, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_pack_component_costs(costs):
    """Guarda los costes manuales de componentes de pack en JSON."""
    costs_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'pack_component_costs.json')
    with open(costs_file, 'w') as f:
        json.dump(costs, f, indent=2)


def _calculate_pack_costs(holded_products):
    """
    Calcula el coste de cada pack sumando:
    - Costes de componentes con SKU en Holded (automático)
    - Costes manuales de componentes sin SKU (desde pack_component_costs.json)
    Devuelve un dict {pack_sku: coste_calculado}
    """
    # Crear mapa de costes por SKU desde Holded
    cost_by_sku = {}
    for p in holded_products:
        sku = p.get('sku', '')
        if sku:
            cost_by_sku[sku] = p.get('cost', 0) or 0

    # Leer costes manuales de componentes
    manual_costs = _get_pack_component_costs()

    pack_costs = {}
    for pack_sku, components in PACK_COMPONENTS.items():
        total_cost = 0
        for comp in components:
            if comp.get('manual'):
                # Componente manual: buscar en pack_component_costs.json
                comp_id = comp.get('id', '')
                comp_cost = manual_costs.get(comp_id, 0)
            else:
                # Componente con SKU: buscar en Holded, si es 0 buscar manual
                comp_sku = comp.get('sku', '')
                holded_cost = cost_by_sku.get(comp_sku, 0)
                if holded_cost > 0:
                    comp_cost = holded_cost
                else:
                    comp_cost = manual_costs.get(comp_sku, 0)
            total_cost += comp_cost * comp.get('quantity', 1)
        pack_costs[pack_sku] = round(total_cost, 2)

    return pack_costs


def _get_web_prices():
    """
    Lee los precios actuales de la web desde la base de datos.
    Devuelve un dict con SKU como clave, incluyendo el id de la DB.
    """
    try:
        from src.models.web_product import WebProduct
        products = WebProduct.query.all()
        if products:
            result = {}
            for p in products:
                if p.sku:
                    result[p.sku] = {
                        'id': p.id,
                        'name': p.name,
                        'price': p.price,
                        'sku': p.sku,
                        'category': p.category,
                        'stock': p.stock,
                        'active': p.active,
                        'shipping_cost': p.shipping_cost or 0,
                        'preparation_cost': p.preparation_cost or 0
                    }
            if result:
                return result
    except Exception as e:
        print(f"[Admin] Error leyendo productos de DB: {e}")

    # Fallback: catálogo hardcodeado (solo se usa si la DB está vacía)
    return {
        'MIKVE5LP': {'name': 'Aceite de Oliva Virgen Extra 5L', 'price': 33.00, 'sku': 'MIKVE5LP', 'category': 'Aceites'},
        'MIKVET500': {'name': 'Aceite de Oliva Virgen Extra Temprano 500ml sin filtrar', 'price': 14.90, 'sku': 'MIKVET500', 'category': 'Aceites'},
        'MIKVE500': {'name': 'Aceite de Oliva Virgen Extra Mikel\'s Fruit (Equilibrado)', 'price': 10.00, 'sku': 'MIKVE500', 'category': 'Aceites'},
        'MIKBIO19': {'name': 'Aceite de Oliva Virgen Extra Ecológico Mikel\'s Fruit', 'price': 13.50, 'sku': 'MIKBIO19', 'category': 'Aceites'},
        'MIKPARA450': {'name': 'Paraguayo en Almíbar', 'price': 14.90, 'sku': 'MIKPARA450', 'category': 'Conservas'},
        'MIKNECT450': {'name': 'Nectarina en Almíbar', 'price': 14.90, 'sku': 'MIKNECT450', 'category': 'Conservas'},
        'MIKPARJ250': {'name': 'Mermelada de Paraguayo Artesanal', 'price': 6.50, 'sku': 'MIKPARJ250', 'category': 'Conservas'},
        'MIKPACK01': {'name': 'Pack Degustación Premium', 'price': 9.00, 'sku': 'MIKPACK01', 'category': 'Packs'},
        'MIKEST01': {'name': 'Estuche de Regalo Premium', 'price': 5.00, 'sku': 'MIKEST01', 'category': 'Packs'},
        'MIKPACKFR': {'name': 'Pack Fruta Premium', 'price': 35.00, 'sku': 'MIKPACKFR', 'category': 'Packs'},
        'MIKPACKTP': {'name': 'Pack Temprano Premium', 'price': 19.00, 'sku': 'MIKPACKTP', 'category': 'Packs'},
        'MIKPACKCO': {'name': 'Pack Completo Mikel\'s Earth', 'price': 81.90, 'sku': 'MIKPACKCO', 'category': 'Packs'},
    }


def _is_price_synced(holded_product, web_prices):
    """Verifica si el precio de Holded coincide con el de la web"""
    sku = holded_product.get('sku', '')
    if sku not in web_prices:
        return None  # No está en la web
    web_price = web_prices[sku].get('price', 0)
    holded_price = holded_product.get('price', 0)
    return abs(holded_price - web_price) < 0.01


def _get_stock_status(stock):
    """Devuelve el estado del stock de forma visual"""
    if stock <= 0:
        return 'agotado'
    elif stock < 50:
        return 'bajo'
    elif stock < 200:
        return 'normal'
    else:
        return 'alto'
