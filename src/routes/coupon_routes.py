"""
Rutas para gestión de cupones de descuento
"""
from flask import Blueprint, request, jsonify
from src.models.coupon import Coupon

coupon_bp = Blueprint('coupon', __name__)


@coupon_bp.route('/validate', methods=['POST'])
def validate_coupon():
    """
    Validar si un cupón es válido
    Body: { "code": "MIKELS10-XXXXXXXX", "email": "user@example.com" (opcional) }
    """
    try:
        data = request.get_json()
        code = data.get('code')
        email = data.get('email')  # Opcional
        
        if not code:
            return jsonify({'error': 'Coupon code is required'}), 400
        
        # Validar cupón
        is_valid, result = Coupon.validate_coupon(code, email)
        
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
                'discount_percentage': coupon.discount_percent,
                'email': coupon.email
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
        data = request.get_json()
        code = data.get('code')
        email = data.get('email')
        
        if not code or not email:
            return jsonify({'error': 'Code and email are required'}), 400
        
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
