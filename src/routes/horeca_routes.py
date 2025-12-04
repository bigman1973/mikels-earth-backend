"""
Rutas para pedidos HORECA (Hostelería, Restauración y Catering)
"""
from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
import os
import requests
from datetime import datetime

horeca_bp = Blueprint('horeca', __name__)


@horeca_bp.route('/order', methods=['POST', 'OPTIONS'])
@cross_origin(origins=['https://www.mikels.es', 'https://mikels.es', 'http://localhost:5173'], supports_credentials=True)
def create_horeca_order():
    """
    Procesa una solicitud de pedido HORECA
    """
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = [
            'establishmentName', 'establishmentType', 'contactName',
            'phone', 'email', 'address', 'city', 'postalCode', 'province'
        ]
        
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'El campo {field} es obligatorio'}), 400
        
        # Validar que al menos un producto tenga cantidad > 0
        aceite_5l = int(data.get('quantity5L', 0))
        aceite_temprano = int(data.get('quantityTemprano', 0))
        subscribe_newsletter = data.get('subscribeNewsletter', False)
        
        if aceite_5l == 0 and aceite_temprano == 0:
            return jsonify({'error': 'Debes seleccionar al menos un producto'}), 400
        
        # Si el usuario quiere suscribirse al newsletter, procesarlo
        if subscribe_newsletter:
            try:
                # Suscribir al newsletter (reutilizando la lógica existente)
                newsletter_response = requests.post(
                    'https://mikels-earth-backend-production.up.railway.app/api/newsletter/subscribe',
                    json={'email': data['email'], 'source': 'horeca'},
                    headers={'Content-Type': 'application/json'}
                )
                if newsletter_response.status_code == 200:
                    print(f"✅ Cliente HORECA suscrito al newsletter: {data['email']}")
                    
                    # Añadir atributo personalizado ORIGEN: HORECA en Brevo
                    api_key = os.getenv('BREVO_API_KEY')
                    if api_key:
                        api_key = api_key.strip().replace('\\n', '').replace('\\r', '').replace(' ', '')
                        try:
                            brevo_response = requests.post(
                                "https://api.brevo.com/v3/contacts",
                                headers={
                                    "accept": "application/json",
                                    "api-key": api_key,
                                    "content-type": "application/json"
                                },
                                json={
                                    "email": data['email'],
                                    "attributes": {
                                        "ORIGEN": "HORECA"
                                    },
                                    "updateEnabled": True
                                }
                            )
                            if brevo_response.status_code in [201, 204]:
                                print(f"✅ Contacto HORECA añadido con atributo ORIGEN: {data['email']}")
                            else:
                                print(f"Advertencia: No se pudo añadir atributo ORIGEN: {brevo_response.status_code}")
                        except Exception as brevo_error:
                            print(f"Error añadiendo atributo ORIGEN en Brevo: {str(brevo_error)}")
            except Exception as e:
                print(f"Error suscribiendo al newsletter: {str(e)}")
        
        # Preparar email para Mikel's Earth
        email_content = f"""
        <h2>🏨 Nuevo Pedido HORECA</h2>
        
        <h3>DATOS DEL ESTABLECIMIENTO</h3>
        <ul>
            <li><strong>Nombre:</strong> {data['establishmentName']}</li>
            <li><strong>Tipo:</strong> {data['establishmentType']}</li>
        </ul>
        
        <h3>DATOS DE CONTACTO</h3>
        <ul>
            <li><strong>Persona de contacto:</strong> {data['contactName']}</li>
            <li><strong>Teléfono:</strong> {data['phone']}</li>
            <li><strong>Email:</strong> {data['email']}</li>
        </ul>
        
        <h3>PRODUCTOS SOLICITADOS</h3>
        <ul>
            <li><strong>Aceite 5L (Caja 3 uds):</strong> {aceite_5l} cajas</li>
            <li><strong>Aceite Temprano 500ml:</strong> {aceite_temprano} unidades</li>
        </ul>
        
        <h3>DIRECCIÓN DE ENTREGA</h3>
        <p>
            {data['address']}<br>
            {data['city']}, {data['postalCode']}<br>
            {data['province']}, España
        </p>
        
        <h3>COMENTARIOS</h3>
        <p>{data.get('comments', 'Sin comentarios adicionales')}</p>
        
        <h3>NEWSLETTER</h3>
        <p>{'✅ Suscrito al newsletter (recibirá cupón 10% descuento)' if subscribe_newsletter else '❌ No suscrito al newsletter'}</p>
        
        <hr>
        <p><small>Fecha de solicitud: {datetime.now().strftime('%d/%m/%Y %H:%M')}</small></p>
        """
        
        # Enviar email a Mikel's Earth
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return jsonify({'error': 'Error de configuración del servidor'}), 500
        
        api_key = api_key.strip().replace('\\n', '').replace('\\r', '').replace(' ', '')
        
        # Email a Mikel's Earth
        response_admin = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Mikel's Earth HORECA", "email": "noreply@mikels.es"},
                "to": [{"email": "info@mikels.es"}],
                "subject": f"🏨 Nuevo Pedido HORECA - {data['establishmentName']}",
                "htmlContent": email_content
            }
        )
        
        if response_admin.status_code != 201:
            print(f"Error enviando email a admin: {response_admin.status_code} - {response_admin.text}")
            return jsonify({'error': 'Error enviando notificación'}), 500
        
        # Email de confirmación al cliente
        client_email_content = f"""
        <h2>Gracias por tu solicitud, {data['contactName']}</h2>
        
        <p>Hemos recibido tu solicitud de pedido HORECA para <strong>{data['establishmentName']}</strong>.</p>
        
        <h3>Resumen de tu solicitud:</h3>
        <ul>
            <li><strong>Aceite 5L (Caja 3 uds):</strong> {aceite_5l} cajas</li>
            <li><strong>Aceite Temprano 500ml:</strong> {aceite_temprano} unidades</li>
        </ul>
        
        <p><strong>Nuestro equipo comercial te enviará una propuesta personalizada en las próximas 24 horas.</strong></p>
        
        <p>Si tienes alguna pregunta urgente, no dudes en contactarnos:</p>
        <ul>
            <li>📧 Email: <a href="mailto:info@mikels.es">info@mikels.es</a></li>
            <li>📱 WhatsApp: <a href="https://wa.me/436789070062172">+43 6789 0700 62172</a></li>
        </ul>
        
        <p>Gracias por confiar en Mikel's Earth.</p>
        
        <p>Un saludo,<br>
        <strong>Equipo Mikel's Earth</strong><br>
        <em>Del campo a tu mesa desde 1819</em></p>
        """
        
        response_client = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Mikel's Earth", "email": "noreply@mikels.es"},
                "to": [{"email": data['email']}],
                "subject": "Solicitud de Pedido HORECA Recibida - Mikel's Earth",
                "htmlContent": client_email_content
            }
        )
        
        if response_client.status_code != 201:
            print(f"Error enviando email a cliente: {response_client.status_code} - {response_client.text}")
        
        print(f"✅ Pedido HORECA procesado: {data['establishmentName']}")
        
        return jsonify({
            'success': True,
            'message': 'Solicitud enviada correctamente. Recibirás una propuesta en las próximas 24 horas.'
        }), 200
        
    except Exception as e:
        print(f"Error procesando pedido HORECA: {str(e)}")
        return jsonify({'error': 'Error procesando la solicitud'}), 500
