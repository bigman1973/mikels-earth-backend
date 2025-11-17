from flask import Blueprint, request, jsonify
from src.services.email_service import send_newsletter_subscription_notification, send_newsletter_subscription_confirmation, add_contact_to_brevo

newsletter_bp = Blueprint('newsletter', __name__)

@newsletter_bp.route('/subscribe', methods=['POST'])
def subscribe_newsletter():
    """
    Endpoint para suscribirse al newsletter
    """
    try:
        data = request.get_json()
        email = data.get('email')
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        # Añadir contacto a Brevo
        brevo_result = add_contact_to_brevo(email)
        
        # Enviar notificación a info@mikels.es
        send_newsletter_subscription_notification(email)
        
        # Enviar email de confirmación al suscriptor
        send_newsletter_subscription_confirmation(email)
        
        return jsonify({
            'success': True,
            'message': 'Subscription successful',
            'brevo_contact_id': brevo_result.get('id') if brevo_result else None
        }), 200
        
    except Exception as e:
        print(f"Error in newsletter subscription: {str(e)}")
        return jsonify({'error': str(e)}), 500

