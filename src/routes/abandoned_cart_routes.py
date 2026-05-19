"""
Rutas para gestión de carritos abandonados.
- POST /api/abandoned-cart: Guardar carrito y enviar evento Started Checkout a Klaviyo
- GET /api/abandoned-cart/<token>: Recuperar carrito por token (para URL persistente)
"""
from flask import Blueprint, request, jsonify
from src.models.abandoned_cart import AbandonedCart
from src.services.email_dispatcher import dispatch_started_checkout_event

abandoned_cart_bp = Blueprint('abandoned_cart', __name__)


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
        total = data.get('total', 0)
        customer_name = data.get('customer_name', '')
        discount_code = data.get('discount_code')
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        if not items:
            return jsonify({'error': 'Cart items are required'}), 400
        
        # Crear o actualizar carrito abandonado
        cart = AbandonedCart.create_or_update(
            email=email,
            items=items,
            total=total,
            customer_name=customer_name,
            discount_code=discount_code
        )
        
        # Enviar evento Started Checkout a Klaviyo
        try:
            dispatch_started_checkout_event(
                email=email,
                customer_name=customer_name,
                items=items,
                total=total,
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
