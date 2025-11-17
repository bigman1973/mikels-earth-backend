from flask import Blueprint, request, jsonify
from src.services.email_service import send_workshop_visit_notification, send_workshop_visit_confirmation

experience_bp = Blueprint('experience', __name__)

@experience_bp.route('/workshop-visit', methods=['POST'])
def workshop_visit():
    """
    Endpoint para solicitar visita al obrador
    """
    try:
        data = request.get_json()
        nombre = data.get('nombre')
        email = data.get('email')
        telefono = data.get('telefono', '')
        interes = data.get('interes', 'visita')
        
        if not nombre or not email:
            return jsonify({'error': 'Nombre y email son requeridos'}), 400
        
        # Enviar notificación a info@mikels.es
        send_workshop_visit_notification(nombre, email, telefono, interes)
        
        # Enviar confirmación al interesado
        send_workshop_visit_confirmation(nombre, email)
        
        return jsonify({
            'success': True,
            'message': 'Solicitud de visita enviada correctamente'
        }), 200
        
    except Exception as e:
        print(f"Error in workshop visit request: {str(e)}")
        return jsonify({'error': str(e)}), 500

