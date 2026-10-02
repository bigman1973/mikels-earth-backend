"""
Rutas para gestión de carritos abandonados.
- POST /api/abandoned-cart: Guardar carrito y enviar evento Started Checkout a Klaviyo
- GET /api/abandoned-cart/<token>: Recuperar carrito por token (para URL persistente)
"""
from copy import deepcopy
from decimal import Decimal

from flask import Blueprint, request, jsonify
from src.models.abandoned_cart import AbandonedCart
from src.services.email_dispatcher import dispatch_started_checkout_event
from src.services.checkout_pricing import calculate_checkout_line_price
from src.services.order_receipt import format_eur

abandoned_cart_bp = Blueprint('abandoned_cart', __name__)


class CartPricingError(ValueError):
    """Raised when a cart event cannot be tied to the live web catalogue."""


def _canonical_cart_items(items):
    """Build the cart-event lines from ``WebProduct``, never browser prices.

    The Flow receives an explicit Spanish line display, including the exact
    reservation-box offer where it applies. That prevents an email from
    presenting a quantity alongside a one-bottle price as though it were the
    line total.
    """
    from src.models.web_product import WebProduct

    canonical_items = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            raise CartPricingError('El carrito contiene una línea no válida.')
        try:
            quantity = int(raw_item.get('quantity', 0))
        except (TypeError, ValueError) as exc:
            raise CartPricingError('La cantidad del carrito no es válida.') from exc
        if quantity < 1:
            raise CartPricingError('La cantidad del carrito no es válida.')

        product = None
        if raw_item.get('id'):
            product = WebProduct.query.get(raw_item['id'])
        if not product and raw_item.get('slug'):
            product = WebProduct.query.filter_by(slug=raw_item['slug']).first()
        if not product:
            raise CartPricingError('Uno de los productos ya no está disponible.')

        pricing = calculate_checkout_line_price(product, quantity)
        item = deepcopy(raw_item)
        item.update({
            'id': product.id,
            'name': product.name,
            'slug': product.slug,
            'image': product.image or raw_item.get('image', ''),
            # Keep the catalogue unit price for recovery. The CartContext
            # repeats the configured box offer after the recovery URL loads.
            'price': float(pricing.base_unit_price),
            'quantity': quantity,
            'unit_price_display': format_eur(pricing.base_unit_price),
            'line_total': float(pricing.expected_line_total),
            'line_total_display': format_eur(pricing.expected_line_total),
            'base_line_total': float(pricing.base_line_total),
            'base_line_total_display': format_eur(pricing.base_line_total),
        })

        if pricing.bundle_quantity and pricing.paid_quantity:
            bundle_count, remainder = divmod(quantity, pricing.bundle_quantity)
            payable_units = bundle_count * pricing.paid_quantity + remainder
            offer = (
                f"{pricing.tier_label}: pagas {payable_units} y recibes {quantity}"
                if bundle_count else ''
            )
            item['pricing_note'] = offer
            item['line_display'] = (
                f"{quantity} × {item['unit_price_display']} · {offer}"
                f" = {item['line_total_display']}"
            )
        else:
            item['pricing_note'] = ''
            item['line_display'] = (
                f"{quantity} × {item['unit_price_display']}"
                f" = {item['line_total_display']}"
            )
        canonical_items.append(item)

    return canonical_items


@abandoned_cart_bp.route('/', methods=['POST'])
def save_abandoned_cart():
    """
    Guardar carrito abandonado y enviar evento Started Checkout a Klaviyo.
    
    Body: {
        "email": "cliente@email.com",
        "customer_name": "Nombre del cliente",
        "items": [
            {
                "id": "product-id",
                "name": "AOVE Temprano 500ml",
                "image": "https://...",
                "price": 14.90,
                "quantity": 2,
                "slug": "aceite-temprano-sin-filtrar"
            }
        ],
        "total": 29.80,
        "discount_code": "MIKELS10-XXXXXXXX" (opcional)
    }
    """
    try:
        data = request.get_json()
        
        email = data.get('email')
        items = data.get('items', [])
        customer_name = data.get('customer_name', '')
        discount_code = data.get('discount_code')
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        if not items:
            return jsonify({'error': 'Cart items are required'}), 400
        
        try:
            items = _canonical_cart_items(items)
        except CartPricingError as pricing_error:
            return jsonify({'error': str(pricing_error)}), 409

        # The event must record the total from live catalogue pricing, not an
        # arbitrary browser number. This is especially important for the
        # reservation case offer, whose unit and line prices differ.
        total = sum(
            (Decimal(str(item['line_total'])) for item in items),
            Decimal('0.00'),
        )

        # Crear o actualizar carrito abandonado. An update retains the same
        # token and must not re-enter the Klaviyo Flow a second time.
        cart, was_created = AbandonedCart.create_or_update(
            email=email,
            items=items,
            total=float(total),
            customer_name=customer_name,
            discount_code=discount_code
        )
        
        # This is the *only* Started Checkout dispatch path. The Stripe
        # checkout-session route deliberately does not emit this event.
        if was_created:
            try:
                dispatch_started_checkout_event(
                    email=email,
                    customer_name=customer_name,
                    items=items,
                    total=float(total),
                    checkout_url=cart.get_checkout_url(),
                    items_html=cart.get_items_html(),
                    cart_token=cart.cart_token
                )
                print(f"✅ Started Checkout event sent to Klaviyo for {email}")
            except Exception as klaviyo_err:
                print(f"⚠️ Error sending Started Checkout to Klaviyo: {klaviyo_err}")
                # No fallar — el carrito se guardó igualmente
        
        return jsonify({
            'success': True,
            'cart_token': cart.cart_token,
            'checkout_url': cart.get_checkout_url(),
            'message': 'Cart saved and event sent'
        }), 200
        
    except Exception as e:
        print(f"Error saving abandoned cart: {str(e)}")
        return jsonify({'error': str(e)}), 500


@abandoned_cart_bp.route('/<token>', methods=['GET'])
def recover_cart(token):
    """
    Recuperar un carrito abandonado por su token.
    Devuelve los items del carrito para que el frontend lo cargue.
    """
    try:
        cart = AbandonedCart.query.filter_by(cart_token=token).first()
        
        if not cart:
            return jsonify({
                'success': False,
                'error': 'Cart not found or expired'
            }), 404
        
        # Marcar como recuperado
        if not cart.recovered:
            cart.recovered = True
            cart.recovered_at = __import__('datetime').datetime.utcnow()
            from src.models.user import db
            db.session.commit()
        
        return jsonify({
            'success': True,
            'cart': cart.to_dict()
        }), 200
        
    except Exception as e:
        print(f"Error recovering cart: {str(e)}")
        return jsonify({'error': str(e)}), 500
