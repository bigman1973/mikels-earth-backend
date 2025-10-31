import requests
import os
from datetime import datetime

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

def send_email(to_email, subject, html_content):
    """
    Envía un email usando la API de Brevo
    
    Args:
        to_email: Email del destinatario
        subject: Asunto del email
        html_content: Contenido HTML del email
    """
    api_key = os.getenv('BREVO_API_KEY')
    
    if not api_key:
        print("ERROR: BREVO_API_KEY no configurada")
        return False
    
    headers = {
        'accept': 'application/json',
        'api-key': api_key,
        'content-type': 'application/json'
    }
    
    payload = {
        'sender': {
            'name': "Mikel's Earth",
            'email': 'info@mikels.es'
        },
        'to': [
            {
                'email': to_email,
                'name': 'Administrador'
            }
        ],
        'subject': subject,
        'htmlContent': html_content
    }
    
    try:
        response = requests.post(BREVO_API_URL, json=payload, headers=headers)
        
        if response.status_code == 201:
            print(f"✅ Email enviado correctamente a {to_email}")
            return True
        else:
            print(f"❌ Error al enviar email: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Excepción al enviar email: {str(e)}")
        return False


def format_order_email(order_data):
    """
    Formatea los datos del pedido en HTML para email
    """
    items_html = ""
    for item in order_data.get('items', []):
        items_html += f"""
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">{item['name']}</td>
            <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: center;">{item['quantity']}</td>
            <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">{item['price']:.2f}€</td>
        </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ background-color: #f9f9f9; padding: 20px; }}
            .section {{ background-color: white; padding: 15px; margin-bottom: 15px; border-radius: 5px; }}
            .section h3 {{ color: #2d5016; margin-top: 0; }}
            table {{ width: 100%; border-collapse: collapse; }}
            .total {{ font-size: 1.2em; font-weight: bold; color: #2d5016; text-align: right; padding: 15px 0; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 0.9em; }}
            .button {{ display: inline-block; padding: 12px 24px; background-color: #2d5016; color: white; text-decoration: none; border-radius: 5px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🛒 Nuevo Pedido Recibido</h1>
            </div>
            
            <div class="content">
                <div class="section">
                    <h3>📋 Información del Pedido</h3>
                    <p><strong>Número de Pedido:</strong> {order_data.get('order_number', 'N/A')}</p>
                    <p><strong>Fecha:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                    <p><strong>Estado:</strong> ✅ Pago confirmado</p>
                </div>
                
                <div class="section">
                    <h3>👤 Datos del Cliente</h3>
                    <p><strong>Nombre:</strong> {order_data.get('customer_name', 'N/A')}</p>
                    <p><strong>Email:</strong> {order_data.get('customer_email', 'N/A')}</p>
                    <p><strong>Teléfono:</strong> {order_data.get('customer_phone', 'N/A')}</p>
                </div>
                
                <div class="section">
                    <h3>📦 Productos</h3>
                    <table>
                        <thead>
                            <tr style="background-color: #f0f0f0;">
                                <th style="padding: 10px; text-align: left;">Producto</th>
                                <th style="padding: 10px; text-align: center;">Cantidad</th>
                                <th style="padding: 10px; text-align: right;">Precio</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items_html}
                        </tbody>
                    </table>
                    <div class="total">
                        Total: {order_data.get('total', 0):.2f}€
                    </div>
                </div>
                
                <div class="section">
                    <h3>📍 Dirección de Envío</h3>
                    <p>{order_data.get('shipping_address', 'N/A')}</p>
                </div>
                
                <div style="text-align: center;">
                    <a href="https://dashboard.stripe.com/payments" class="button">Ver en Stripe Dashboard</a>
                </div>
            </div>
            
            <div class="footer">
                <p>Este es un email automático de notificación de pedidos.</p>
                <p>Mikel's Earth - Productos del campo directo a tu mesa</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html


def format_subscription_email(subscription_data):
    """
    Formatea los datos de la suscripción en HTML para email
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ background-color: #f9f9f9; padding: 20px; }}
            .section {{ background-color: white; padding: 15px; margin-bottom: 15px; border-radius: 5px; }}
            .section h3 {{ color: #2d5016; margin-top: 0; }}
            .highlight {{ font-size: 1.2em; font-weight: bold; color: #2d5016; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 0.9em; }}
            .button {{ display: inline-block; padding: 12px 24px; background-color: #2d5016; color: white; text-decoration: none; border-radius: 5px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔄 Nueva Suscripción Activada</h1>
            </div>
            
            <div class="content">
                <div class="section">
                    <h3>📋 Información de la Suscripción</h3>
                    <p><strong>Número:</strong> {subscription_data.get('subscription_number', 'N/A')}</p>
                    <p><strong>Fecha de activación:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                    <p><strong>Estado:</strong> ✅ Activa</p>
                </div>
                
                <div class="section">
                    <h3>👤 Datos del Cliente</h3>
                    <p><strong>Nombre:</strong> {subscription_data.get('customer_name', 'N/A')}</p>
                    <p><strong>Email:</strong> {subscription_data.get('customer_email', 'N/A')}</p>
                </div>
                
                <div class="section">
                    <h3>📦 Producto Suscrito</h3>
                    <p class="highlight">{subscription_data.get('product_name', 'N/A')}</p>
                    <p><strong>Frecuencia:</strong> {subscription_data.get('frequency', 'N/A')}</p>
                    <p><strong>Precio por envío:</strong> {subscription_data.get('price', 0):.2f}€</p>
                </div>
                
                <div style="text-align: center;">
                    <a href="https://dashboard.stripe.com/subscriptions" class="button">Ver en Stripe Dashboard</a>
                </div>
            </div>
            
            <div class="footer">
                <p>Este es un email automático de notificación de suscripciones.</p>
                <p>Mikel's Earth - Productos del campo directo a tu mesa</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html


def notify_new_order_email(order_data):
    """
    Envía notificación de nuevo pedido por email
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    subject = f"🛒 Nuevo Pedido #{order_data.get('order_number', 'N/A')}"
    html_content = format_order_email(order_data)
    
    return send_email(owner_email, subject, html_content)


def notify_new_subscription_email(subscription_data):
    """
    Envía notificación de nueva suscripción por email
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    subject = f"🔄 Nueva Suscripción #{subscription_data.get('subscription_number', 'N/A')}"
    html_content = format_subscription_email(subscription_data)
    
    return send_email(owner_email, subject, html_content)

