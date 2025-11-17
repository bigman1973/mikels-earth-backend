from flask import Blueprint, request, jsonify
from src.services.email_service import send_contact_notification, send_contact_confirmation

contact_bp = Blueprint('contact', __name__)

@contact_bp.route('/send-message', methods=['POST'])
def send_message():
    """
    Endpoint para enviar mensaje de contacto
    """
    try:
        data = request.get_json()
        name = data.get('name')
        email = data.get('email')
        phone = data.get('phone', '')
        message = data.get('message')
        
        if not name or not email or not message:
            return jsonify({'error': 'Nombre, email y mensaje son requeridos'}), 400
        
        # Enviar notificación a info@mikels.es
        send_contact_notification(name, email, phone, message)
        
        # Enviar confirmación al cliente
        send_contact_confirmation(name, email)
        
        return jsonify({
            'success': True,
            'message': 'Mensaje enviado correctamente'
        }), 200
        
    except Exception as e:
        print(f"Error in contact form: {str(e)}")
        return jsonify({'error': str(e)}), 500

