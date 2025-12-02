# PostgreSQL coupons table ready - 2025-12-01 22:45
import os
import requests
from flask import Blueprint, request, jsonify
from src.services.email_service import send_newsletter_subscription_notification, add_contact_to_brevo
from src.services.email_newsletter_welcome import send_newsletter_welcome_email

newsletter_bp = Blueprint('newsletter', __name__)

@newsletter_bp.route('/subscribe', methods=['POST'])
def subscribe_newsletter():
    """
    Endpoint para suscribirse al newsletter
    """
    try:
        data = request.get_json()
        email = data.get('email')
        coupon_code = data.get('coupon_code')  # Cupón generado por el microservicio
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        # Si no viene cupón del frontend, llamar al servicio de cupones para generar uno
        if not coupon_code:
            try:
                # Llamar al servicio de cupones para crear un cupón único
                coupon_service_url = os.getenv('COUPON_SERVICE_URL', 'https://mikels-coupons-service-production.up.railway.app')
                response = requests.post(
                    f"{coupon_service_url}/api/coupon/create",
                    json={
                        "email": email,
                        "discount_percentage": 10
                    },
                    timeout=5
                )
                
                if response.status_code == 201:
                    coupon_data = response.json()
                    coupon_code = coupon_data.get('coupon', {}).get('code', 'BIENVENIDA10')
                    print(f"Coupon created successfully: {coupon_code}")
                else:
                    print(f"Error creating coupon: {response.status_code} - {response.text}")
                    coupon_code = "BIENVENIDA10"
            except Exception as e:
                print(f"Error calling coupon service: {str(e)}")
                # Si falla, usar código genérico como fallback
                coupon_code = "BIENVENIDA10"
        
        # Añadir contacto a Brevo
        brevo_result = add_contact_to_brevo(email)
        
        # Enviar notificación a info@mikels.es
        send_newsletter_subscription_notification(email)
        
        # Enviar email de bienvenida al suscriptor con código de descuento único
        send_newsletter_welcome_email(email, coupon_code)
        
        return jsonify({
            'success': True,
            'message': 'Subscription successful',
            'coupon_code': coupon_code,
            'brevo_contact_id': brevo_result.get('id') if brevo_result else None
        }), 200
        
    except Exception as e:
        print(f"Error in newsletter subscription: {str(e)}")
        return jsonify({'error': str(e)}), 500

