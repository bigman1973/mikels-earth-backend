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
    holded_find_contact_by_email,
    holded_create_sales_order,
    holded_create_invoice,
    holded_get_invoice_pdf,
    holded_get_warehouses,
    holded_get_or_create_contact
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
    Permite comparar precios Holded vs Web.
    """
    holded_products = holded_get_products()

    # Leer precios web desde el archivo de productos del frontend
    # (En futuro se migrará a base de datos)
    web_prices = _get_web_prices()

    result = []
    for p in holded_products:
        product_data = {
            'holded_id': p.get('id'),
            'name': p.get('name', ''),
            'sku': p.get('sku', ''),
            'barcode': p.get('barcode', ''),
            'holded_price': p.get('price', 0),
            'holded_total': p.get('total', 0),  # Precio con IVA
            'stock': p.get('stock', 0),
            'has_stock': p.get('hasStock', False),
            'cost': p.get('cost', 0),
            'tax': p.get('taxes', []),
            'web_price': web_prices.get(p.get('sku', ''), {}).get('price'),
            'web_name': web_prices.get(p.get('sku', ''), {}).get('name'),
            'synced': _is_price_synced(p, web_prices)
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
    orders = Order.query.order_by(Order.created_at.desc()).limit(50).all()

    return jsonify({
        'orders': [o.to_dict() for o in orders] if orders else [],
        'total': len(orders) if orders else 0
    })


@admin_panel_bp.route('/orders/<int:order_id>/create-in-holded', methods=['POST'])
@admin_required
@role_required('admin', 'sales')
def create_order_in_holded(order_id):
    """Crea un pedido de venta en Holded a partir de un pedido web"""
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
    items = []
    if order.items:
        order_items = json.loads(order.items) if isinstance(order.items, str) else order.items
        for item in order_items:
            items.append({
                'name': item.get('name', ''),
                'description': item.get('description', ''),
                'units': item.get('quantity', 1),
                'subtotal': item.get('price', 0),
                'tax': 's_iva_4',
                'sku': item.get('sku', '')
            })

    success, result = holded_create_sales_order(
        contact_id=contact_id,
        items=items,
        notes=f'Pedido web #{order.id} - Stripe: {order.stripe_checkout_session_id or "N/A"}'
    )

    if success:
        return jsonify({'success': True, 'holded_order': result})
    else:
        return jsonify({'error': f'Error creando pedido en Holded: {result}'}), 500


@admin_panel_bp.route('/orders/<int:order_id>/invoice', methods=['POST'])
@admin_required
@role_required('admin', 'sales')
def create_invoice_in_holded(order_id):
    """Genera una factura en Holded para un pedido entregado"""
    from src.models.order import Order
    order = Order.query.get(order_id)

    if not order:
        return jsonify({'error': 'Pedido no encontrado'}), 404

    # Buscar contacto en Holded
    contact_id = holded_get_or_create_contact(
        email=order.customer_email,
        name=order.customer_name
    )

    if not contact_id:
        return jsonify({'error': 'No se pudo encontrar el contacto en Holded'}), 500

    # Preparar items
    items = []
    if order.items:
        order_items = json.loads(order.items) if isinstance(order.items, str) else order.items
        for item in order_items:
            items.append({
                'name': item.get('name', ''),
                'units': item.get('quantity', 1),
                'subtotal': item.get('price', 0),
                'tax': 's_iva_4',
                'sku': item.get('sku', '')
            })

    success, result = holded_create_invoice(
        contact_id=contact_id,
        items=items,
        notes=f'Factura pedido web #{order.id}'
    )

    if success:
        return jsonify({'success': True, 'holded_invoice': result})
    else:
        return jsonify({'error': f'Error creando factura en Holded: {result}'}), 500


# ============================================================
# CONTACTOS / CLIENTES
# ============================================================

@admin_panel_bp.route('/clients', methods=['GET'])
@admin_required
def get_clients():
    """Devuelve los contactos/clientes de Holded"""
    contacts = holded_get_contacts()

    # Filtrar solo clientes (type=client)
    clients = [c for c in contacts if c.get('type') == 'client']

    return jsonify({
        'clients': [{
            'id': c.get('id'),
            'name': c.get('name', ''),
            'email': c.get('email', ''),
            'phone': c.get('phone', ''),
            'mobile': c.get('mobile', ''),
            'total_invoiced': c.get('socialInvoiced', 0)
        } for c in clients],
        'total': len(clients)
    })


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
# UTILIDADES INTERNAS
# ============================================================

def _get_web_prices():
    """
    Lee los precios actuales de la web desde el archivo de productos.
    Devuelve un dict con SKU como clave.
    """
    # Intentar leer del archivo de productos del frontend
    # Esto se mejorará cuando los precios estén en base de datos
    try:
        products_file = os.environ.get(
            'WEB_PRODUCTS_FILE',
            '/app/web_products.json'
        )
        if os.path.exists(products_file):
            with open(products_file, 'r') as f:
                products = json.load(f)
                return {p.get('sku', ''): p for p in products if p.get('sku')}
    except Exception as e:
        print(f"[Admin] Error leyendo precios web: {e}")

    # Fallback: precios hardcodeados del catálogo actual de mikels.es
    return {
        'MIKBIO19': {'name': 'Aceite Ecológico 500ml', 'price': 14.90, 'sku': 'MIKBIO19'},
        'MIKVE500': {'name': 'Aceite Virgen Extra 500ml', 'price': 12.90, 'sku': 'MIKVE500'},
        'MIKVET500': {'name': 'Aceite Temprano 500ml', 'price': 16.90, 'sku': 'MIKVET500'},
        'MIKVE5LP': {'name': 'Aceite Virgen Extra 5L', 'price': 54.90, 'sku': 'MIKVE5LP'},
        'MIKVE1000': {'name': 'Aceite Virgen Extra 1L', 'price': 19.90, 'sku': 'MIKVE1000'},
        'MIKPARJ250': {'name': 'Mermelada Paraguayo 250g', 'price': 6.90, 'sku': 'MIKPARJ250'},
        'MIKPARA450': {'name': 'Fruta Paraguayo 450g', 'price': 14.90, 'sku': 'MIKPARA450'},
        'MIKNECT450': {'name': 'Fruta Nectarina 450g', 'price': 14.90, 'sku': 'MIKNECT450'},
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
