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
        product_data = {
            'holded_id': p.get('id'),
            'name': p.get('name', ''),
            'sku': sku,
            'barcode': p.get('barcode', ''),
            'holded_price': p.get('price', 0),
            'holded_total': p.get('total', 0),  # Precio con IVA
            'stock': p.get('stock', 0),
            'has_stock': p.get('hasStock', False),
            'cost': p.get('cost', 0),
            'tax': taxes,
            'iva_rate': iva_rate,
            'web_price': web_match.get('price'),
            'web_name': web_match.get('name'),
            'web_category': web_match.get('category'),
            'synced': _is_price_synced(p, web_prices),
            'source': 'holded',
            'shipping_cost': sku_costs.get('shipping_cost', 0),
            'preparation_cost': sku_costs.get('preparation_cost', 0)
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
            product_data = {
                'holded_id': None,
                'name': wp.get('name', ''),
                'sku': sku,
                'barcode': '',
                'holded_price': None,
                'holded_total': None,
                'stock': wp.get('stock', 0),
                'has_stock': True,
                'cost': 0,
                'tax': [],
                'iva_rate': iva_rate,
                'web_price': wp.get('price'),
                'web_name': wp.get('name'),
                'web_category': wp.get('category'),
                'synced': None,
                'source': 'web_only',
                'shipping_cost': sku_costs.get('shipping_cost', 0),
                'preparation_cost': sku_costs.get('preparation_cost', 0)
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
    """Lee los costes de portes y preparación por SKU."""
    costs_file = os.environ.get('PRODUCT_COSTS_FILE', '/app/product_costs.json')
    try:
        if os.path.exists(costs_file):
            with open(costs_file, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"[Admin] Error leyendo costes: {e}")
    return {}


def _save_product_costs(costs):
    """Guarda los costes de portes y preparación por SKU."""
    costs_file = os.environ.get('PRODUCT_COSTS_FILE', '/app/product_costs.json')
    try:
        with open(costs_file, 'w') as f:
            json.dump(costs, f, indent=2)
        return True
    except Exception as e:
        print(f"[Admin] Error guardando costes: {e}")
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


@admin_panel_bp.route('/products/<sku>/web-price', methods=['PUT'])
@admin_required
@role_required('admin')
def update_web_price(sku):
    """
    Actualiza el precio web de un producto.
    Modifica el archivo web_products.json y el fallback del catálogo.
    Body: { price: float }
    """
    try:
        data = request.get_json()
        new_price = float(data.get('price', 0))

        if new_price <= 0:
            return jsonify({'error': 'El precio debe ser mayor que 0'}), 400

        # Actualizar en web_products.json si existe
        products_file = os.environ.get('WEB_PRODUCTS_FILE', '/app/web_products.json')
        updated = False

        if os.path.exists(products_file):
            try:
                with open(products_file, 'r') as f:
                    products = json.load(f)
                for p in products:
                    if p.get('sku') == sku:
                        p['price'] = new_price
                        updated = True
                        break
                if updated:
                    with open(products_file, 'w') as f:
                        json.dump(products, f, indent=2)
            except Exception as e:
                print(f"[Admin] Error actualizando web_products.json: {e}")

        return jsonify({
            'success': True,
            'sku': sku,
            'new_price': new_price,
            'file_updated': updated
        })
    except Exception as e:
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
        items = []
        if order.items:
            order_items = json.loads(order.items) if isinstance(order.items, str) else order.items
            for item in order_items:
                price_with_iva = item.get('price', 0)
                # Quitar IVA (4% para AOVE/conservas)
                price_without_iva = round(price_with_iva / 1.04, 2)
                items.append({
                    'name': item.get('name', ''),
                    'units': item.get('quantity', 1),
                    'subtotal': price_without_iva,
                    'tax': 's_iva_4',
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
            doc_number = result.get('docNumber', '') or result.get('num', '')
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

    # Fallback: catálogo completo de mikels.es (precios con IVA incluido)
    # SKU → datos del producto web
    return {
        # Aceites (IVA 4%)
        'MIKVE5LP': {'name': 'Aceite de Oliva Virgen Extra 5L', 'price': 33.00, 'sku': 'MIKVE5LP', 'category': 'Aceites'},
        'MIKVET500': {'name': 'Aceite de Oliva Virgen Extra Temprano 500ml sin filtrar', 'price': 14.90, 'sku': 'MIKVET500', 'category': 'Aceites'},
        'MIKVE500': {'name': 'Aceite de Oliva Virgen Extra Mikel\'s Fruit (Equilibrado)', 'price': 10.00, 'sku': 'MIKVE500', 'category': 'Aceites'},
        'MIKBIO19': {'name': 'Aceite de Oliva Virgen Extra Ecológico Mikel\'s Fruit', 'price': 13.50, 'sku': 'MIKBIO19', 'category': 'Aceites'},
        # Conservas (IVA 10%)
        'MIKPARA450': {'name': 'Paraguayo en Almíbar', 'price': 14.90, 'sku': 'MIKPARA450', 'category': 'Conservas'},
        'MIKNECT450': {'name': 'Nectarina en Almíbar', 'price': 14.90, 'sku': 'MIKNECT450', 'category': 'Conservas'},
        'MIKPARJ250': {'name': 'Mermelada de Paraguayo Artesanal', 'price': 6.50, 'sku': 'MIKPARJ250', 'category': 'Conservas'},
        # Packs (IVA 4% - mayoría aceite)
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
