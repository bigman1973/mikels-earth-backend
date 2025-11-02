from flask import Blueprint, request, jsonify
from src.services.email_service import send_product_notification_request, send_customer_notification_confirmation

notification_bp = Blueprint('notification', __name__, url_prefix='/api/notification')

@notification_bp.route('/notify-me', methods=['POST'])
def notify_me():
    """Handle product availability notification requests"""
    try:
        data = request.json
        
        # Validate required fields
        required_fields = ['product_name', 'customer_name', 'customer_email']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        product_name = data['product_name']
        customer_name = data['customer_name']
        customer_email = data['customer_email']
        customer_phone = data.get('customer_phone', '')
        
        # Send notification email to owner
        owner_email_sent = send_product_notification_request(
            product_name=product_name,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=customer_phone
        )
        
        # Send confirmation email to customer
        customer_email_sent = send_customer_notification_confirmation(
            product_name=product_name,
            customer_name=customer_name,
            customer_email=customer_email
        )
        
        if not owner_email_sent:
            print("⚠️ Email al propietario no se pudo enviar")
        if not customer_email_sent:
            print("⚠️ Email al cliente no se pudo enviar")
        
        return jsonify({
            'success': True,
            'message': 'Te avisaremos cuando el producto esté disponible'
        }), 200
        
    except Exception as e:
        print(f"Error processing notification request: {str(e)}")
        return jsonify({'error': 'Error al procesar la solicitud'}), 500

