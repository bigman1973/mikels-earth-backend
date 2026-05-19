# PostgreSQL coupons table ready - 2025-12-01 22:45
from flask import Blueprint, request, jsonify
from src.services.email_dispatcher import dispatch_newsletter_subscription_notification, dispatch_newsletter_welcome, dispatch_add_contact
from src.models.coupon import Coupon

newsletter_bp = Blueprint('newsletter', __name__)

@newsletter_bp.route('/subscribe', methods=['POST'])
def subscribe_newsletter():
    """
    Endpoint para suscribirse al newsletter.
    Acepta: email (obligatorio), first_name, last_name (obligatorios desde frontend), phone (opcional)
    """
    try:
        data = request.get_json()
        email = data.get('email')
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()
        phone = data.get('phone', '').strip()
        coupon_code = data.get('coupon_code')  # Cupón generado por el microservicio
        source = data.get('source', 'website')
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        # Si no viene cupón del frontend, generar uno usando PostgreSQL
        if not coupon_code:
            try:
                # Crear cupón único usando el modelo Coupon (PostgreSQL)
                coupon = Coupon.create_coupon(email, discount_percent=10)
                coupon_code = coupon.code
                print(f"Coupon created successfully: {coupon_code} for {email}")
            except Exception as e:
                print(f"Error creating coupon: {str(e)}")
                # Si falla, usar código genérico como fallback
                coupon_code = "BIENVENIDA10"
        
        # Añadir contacto a Klaviyo con nombre, apellidos y teléfono
        contact_result = dispatch_add_contact(
            email, 
            first_name=first_name, 
            last_name=last_name, 
            phone=phone,
            source=source
        )
        
        # Enviar notificación a info@mikels.es (Klaviyo + Brevo fallback)
        dispatch_newsletter_subscription_notification(email, coupon_code)
        
        # Enviar email de bienvenida al suscriptor con código de descuento único (Klaviyo + Brevo fallback)
        dispatch_newsletter_welcome(email, coupon_code)
        
        return jsonify({
            'success': True,
            'message': 'Subscription successful',
            'coupon_code': coupon_code,
            'contact_id': contact_result.get('id') if contact_result else None
        }), 200
        
    except Exception as e:
        print(f"Error in newsletter subscription: {str(e)}")
        return jsonify({'error': str(e)}), 500
