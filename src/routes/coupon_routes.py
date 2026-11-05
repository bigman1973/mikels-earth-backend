"""
Rutas para gestión de cupones de descuento
"""
from flask import Blueprint, request, jsonify
from src.models.coupon import Coupon
from src.services.money import as_eur, MoneyValueError
from src.services.protected_catalogue import is_promotion_protected
from src.services.cart_promotions import (
    cart_contains_in_promotion, cart_item_references, resolve_product_references,
)
from src.services.coupon_policy import cart_blocks_coupon, PROMOTION_COUPON_MESSAGE

coupon_bp = Blueprint('coupon', __name__)


def _has_protected_cart_item(data):
    """Optional preview context; checkout always checks its own persisted cart."""
    if cart_blocks_coupon(data.get('items', []), preserve_legacy_sku=True):
        return True
    # Preserve this branch's selected nested-addon protection as well as the
    # published independent ID/slug/SKU policy; browser flags stay irrelevant.
    for item in cart_item_references(data.get('items', [])):
        if is_promotion_protected(item):
            return True
        if any(is_promotion_protected(product) for product in resolve_product_references(item)):
            return True
    return False


@coupon_bp.route('/validate', methods=['POST'])
def validate_coupon():
    """
    Validar si un cupón es válido (universal - funciona para todos los tipos)
    Body: { "code": "CODIGO", "email": "user@example.com", "items": [...], "order_amount": 49 }
    Email, items e importe son opcionales; sin importe solo se valida el código.
    El pago vuelve a comprobar el mínimo con el subtotal calculado en servidor.
    """
    try:
        data = request.get_json(silent=True) or {}
        code = data.get('code')
        email = data.get('email')  # Opcional
        
        if not code:
            return jsonify({'error': 'Coupon code is required'}), 400

        if cart_contains_in_promotion(data.get('items', [])):
            return jsonify({
                'valid': False,
                'error': 'PROMOTION_NOT_AVAILABLE',
                'message': PROMOTION_COUPON_MESSAGE,
            }), 200
        if _has_protected_cart_item(data):
            return jsonify({
                'valid': False,
                'error': 'PROMOTION_NOT_AVAILABLE',
                'message': PROMOTION_COUPON_MESSAGE,
            }), 200
        
        amount_context = {}
        if 'order_amount' in data:
            try:
                amount = as_eur(data['order_amount'], field='subtotal')
                if amount < 0:
                    raise MoneyValueError('El subtotal no puede ser negativo.')
            except (MoneyValueError, ArithmeticError):
                return jsonify({'error': 'INVALID_ORDER_AMOUNT'}), 400
            amount_context['order_amount'] = amount

        # Validar cupón (busca por código case-insensitive)
        is_valid, result = Coupon.validate_coupon(code, email, **amount_context)
        
        if not is_valid:
            return jsonify({
                'valid': False,
                'message': result  # Mensaje de error
            }), 200
        
        # Si es válido, result es el objeto Coupon
        coupon = result
        return jsonify({
            'valid': True,
            'coupon': {
                'code': coupon.code,
                'discount_percentage': coupon.discount_value,  # Siempre devolver el valor
                'discount_type': coupon.discount_type,
                'min_order_amount': coupon.min_order_amount,
            }
        }), 200
        
    except Exception as e:
        print(f"Error validating coupon: {str(e)}")
        return jsonify({'error': str(e)}), 500


@coupon_bp.route('/use', methods=['POST'])
def use_coupon():
    """
    Marcar un cupón como usado
    Body: { "code": "MIKELS10-XXXXXXXX", "email": "user@example.com" }
    """
    try:
        data = request.get_json(silent=True) or {}
        code = data.get('code')
        email = data.get('email')
        
        if not code or not email:
            return jsonify({'error': 'Code and email are required'}), 400

        if cart_contains_in_promotion(data.get('items', [])):
            return jsonify({
                'success': False,
                'error': 'PROMOTION_NOT_AVAILABLE',
                'message': PROMOTION_COUPON_MESSAGE,
            }), 422
        if _has_protected_cart_item(data):
            return jsonify({
                'success': False,
                'error': 'PROMOTION_NOT_AVAILABLE',
                'message': PROMOTION_COUPON_MESSAGE,
            }), 422
        
        # Validar cupón primero
        is_valid, result = Coupon.validate_coupon(code, email)
        
        if not is_valid:
            return jsonify({
                'success': False,
                'message': result
            }), 400
        
        # Marcar como usado
        coupon = result
        coupon.mark_as_used()
        
        return jsonify({
            'success': True,
            'message': 'Coupon marked as used',
            'coupon': coupon.to_dict()
        }), 200
        
    except Exception as e:
        print(f"Error using coupon: {str(e)}")
        return jsonify({'error': str(e)}), 500


@coupon_bp.route('/check/<email>', methods=['GET'])
def check_user_coupon(email):
    """
    Verificar si un email tiene un cupón asociado
    """
    try:
        coupon = Coupon.query.filter_by(email=email).first()
        
        if not coupon:
            return jsonify({
                'has_coupon': False,
                'message': 'No coupon found for this email'
            }), 200
        
        return jsonify({
            'has_coupon': True,
            'coupon': coupon.to_dict()
        }), 200
        
    except Exception as e:
        print(f"Error checking coupon: {str(e)}")
        return jsonify({'error': str(e)}), 500
