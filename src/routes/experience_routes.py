from flask import Blueprint, request, jsonify
from src.services.email_dispatcher import dispatch_workshop_visit_notification, dispatch_workshop_visit_confirmation

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
        
        # Enviar notificación a info@mikels.es (Klaviyo + Brevo fallback)
        dispatch_workshop_visit_notification(nombre, email, telefono, interes)
        
        # Enviar confirmación al interesado (Klaviyo + Brevo fallback)
        dispatch_workshop_visit_confirmation(nombre, email)
        
        return jsonify({
            'success': True,
            'message': 'Solicitud de visita enviada correctamente'
        }), 200
        
    except Exception as e:
        print(f"Error in workshop visit request: {str(e)}")
        return jsonify({'error': str(e)}), 500
