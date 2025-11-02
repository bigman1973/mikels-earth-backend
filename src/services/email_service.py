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
    
    # Limpiar la API key (eliminar saltos de línea y espacios)
    api_key = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
    
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
                    <table style="margin-top: 15px;">
                        <tr>
                            <td style="text-align: right; padding: 5px; color: #666;">Subtotal:</td>
                            <td style="text-align: right; padding: 5px; width: 100px;">{order_data.get('subtotal', order_data.get('total', 0)):.2f}€</td>
                        </tr>
                        {f'''<tr>
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">Descuento {order_data.get('discount_code', '')}:</td>
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">-{order_data.get('discount_amount', 0):.2f}€</td>
                        </tr>''' if order_data.get('discount_code') else ''}
                        <tr>
                            <td style="text-align: right; padding: 5px; color: #666;">Envío:</td>
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">GRATIS</td>
                        </tr>
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


def format_customer_order_confirmation(order_data):
    """
    Formatea el email de confirmación para el cliente
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
            .header {{ background-color: #2d5016; color: white; padding: 30px 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .content {{ background-color: #f9f9f9; padding: 20px; }}
            .section {{ background-color: white; padding: 20px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .section h3 {{ color: #2d5016; margin-top: 0; }}
            table {{ width: 100%; border-collapse: collapse; }}
            .total {{ font-size: 1.3em; font-weight: bold; color: #2d5016; text-align: right; padding: 15px 0; border-top: 2px solid #2d5016; }}
            .highlight-box {{ background-color: #f0f7e9; padding: 15px; border-left: 4px solid #2d5016; margin: 15px 0; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 0.9em; }}
            .contact-info {{ background-color: #f0f7e9; padding: 15px; border-radius: 5px; margin-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>✅ ¡Pedido Confirmado!</h1>
                <p style="margin: 10px 0 0 0; font-size: 16px;">Gracias por tu compra</p>
            </div>
            
            <div class="content">
                <div class="section">
                    <p style="font-size: 16px; margin-top: 0;">Hola <strong>{order_data.get('customer_name', 'Cliente')}</strong>,</p>
                    <p>¡Gracias por confiar en Mikel's Earth! Hemos recibido tu pedido y lo estamos preparando con mucho cariño.</p>
                    
                    <div class="highlight-box">
                        <p style="margin: 0;"><strong>📋 Número de Pedido:</strong> {order_data.get('order_number', 'N/A')}</p>
                        <p style="margin: 5px 0 0 0;"><strong>📅 Fecha:</strong> {datetime.now().strftime('%d/%m/%Y')}</p>
                    </div>
                </div>
                
                <div class="section">
                    <h3>📦 Resumen de tu Pedido</h3>
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
                    <table style="margin-top: 15px;">
                        <tr>
                            <td style="text-align: right; padding: 5px; color: #666;">Subtotal:</td>
                            <td style="text-align: right; padding: 5px; width: 100px;">{order_data.get('subtotal', order_data.get('total', 0)):.2f}€</td>
                        </tr>
                        {f'''<tr>
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">Descuento {order_data.get('discount_code', '')}:</td>
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">-{order_data.get('discount_amount', 0):.2f}€</td>
                        </tr>''' if order_data.get('discount_code') else ''}
                        <tr>
                            <td style="text-align: right; padding: 5px; color: #666;">Envío:</td>
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">GRATIS</td>
                        </tr>
                    </table>
                    <div class="total">
                        Total: {order_data.get('total', 0):.2f}€
                    </div>
                </div>
                
                <div class="section">
                    <h3>📍 Dirección de Envío</h3>
                    <p>{order_data.get('shipping_address', 'N/A')}</p>
                </div>
                
                <div class="section">
                    <h3>🚚 ¿Qué sigue?</h3>
                    <p>1. <strong>Preparación:</strong> Estamos preparando tu pedido con productos frescos del campo</p>
                    <p>2. <strong>Envío:</strong> Te enviaremos un email cuando tu pedido esté en camino</p>
                    <p>3. <strong>Entrega:</strong> Recibirás tu pedido en 3-5 días laborables</p>
                </div>
                
                <div class="contact-info">
                    <h4 style="margin-top: 0; color: #2d5016;">¿Necesitas ayuda?</h4>
                    <p style="margin: 5px 0;">📧 Email: info@mikels.es</p>
                    <p style="margin: 5px 0;">📱 WhatsApp: +43 6789 0700 62172</p>
                    <p style="margin: 5px 0;">🌐 Web: mikels-earth-frontend.vercel.app</p>
                </div>
            </div>
            
            <div class="footer">
                <p><strong>Mikel's Earth</strong></p>
                <p>Productos del campo directo a tu mesa</p>
                <p style="font-size: 0.8em; color: #999; margin-top: 15px;">Este email se envió a {order_data.get('customer_email', '')} porque realizaste una compra en nuestra tienda.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html


def send_customer_order_confirmation(order_data):
    """
    Envía email de confirmación al cliente
    """
    customer_email = order_data.get('customer_email')
    
    if not customer_email or customer_email == 'N/A':
        print("⚠️ No se puede enviar email al cliente: email no disponible")
        return False
    
    subject = f"✅ Pedido Confirmado #{order_data.get('order_number', 'N/A')} - Mikel's Earth"
    html_content = format_customer_order_confirmation(order_data)
    
    return send_email(customer_email, subject, html_content)




def send_product_notification_request(product_name, customer_name, customer_email, customer_phone=''):
    """
    Envía notificación al propietario cuando un cliente solicita ser avisado de un producto sold out
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    subject = f"🔔 Solicitud de Notificación - {product_name}"
    
    html_content = f"""
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
            .highlight {{ background-color: #fff3cd; padding: 10px; border-left: 4px solid #ffc107; margin: 10px 0; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 0.9em; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔔 Solicitud de Notificación</h1>
            </div>
            
            <div class="content">
                <div class="highlight">
                    <strong>⚠️ Un cliente quiere ser notificado cuando este producto esté disponible</strong>
                </div>
                
                <div class="section">
                    <h3>📦 Producto Solicitado</h3>
                    <p style="font-size: 1.2em; font-weight: bold; color: #2d5016;">{product_name}</p>
                </div>
                
                <div class="section">
                    <h3>👤 Datos del Cliente</h3>
                    <p><strong>Nombre:</strong> {customer_name}</p>
                    <p><strong>Email:</strong> {customer_email}</p>
                    {f'<p><strong>Teléfono:</strong> {customer_phone}</p>' if customer_phone else ''}
                </div>
                
                <div class="section">
                    <h3>📝 Acción Requerida</h3>
                    <p>Cuando el producto esté disponible, contacta al cliente para informarle:</p>
                    <ul>
                        <li>Email: {customer_email}</li>
                        {f'<li>Teléfono: {customer_phone}</li>' if customer_phone else ''}
                    </ul>
                </div>
            </div>
            
            <div class="footer">
                <p>Este es un email automático de solicitud de notificación.</p>
                <p>Mikel's Earth - Productos del campo directo a tu mesa</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return send_email(owner_email, subject, html_content)




def send_customer_notification_confirmation(product_name, customer_name, customer_email):
    """
    Envía email de confirmación al cliente cuando solicita ser notificado de un producto sold out
    """
    subject = f"✅ Te avisaremos cuando {product_name} esté disponible - Mikel's Earth"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 30px 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .content {{ background-color: #f9f9f9; padding: 20px; }}
            .section {{ background-color: white; padding: 20px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .section h3 {{ color: #2d5016; margin-top: 0; }}
            .highlight-box {{ background-color: #f0f7e9; padding: 20px; border-left: 4px solid #2d5016; margin: 15px 0; text-align: center; }}
            .product-name {{ font-size: 1.3em; font-weight: bold; color: #2d5016; margin: 10px 0; }}
            .footer {{ text-align: center; padding: 20px; color: #666; font-size: 0.9em; }}
            .contact-info {{ background-color: #f0f7e9; padding: 15px; border-radius: 5px; margin-top: 15px; }}
            .icon {{ font-size: 48px; margin-bottom: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="icon">🔔</div>
                <h1>¡Solicitud Recibida!</h1>
            </div>
            
            <div class="content">
                <div class="section">
                    <h3>Hola {customer_name},</h3>
                    <p>Gracias por tu interés en nuestros productos. Hemos recibido tu solicitud y te avisaremos por email cuando el siguiente producto esté disponible:</p>
                    
                    <div class="highlight-box">
                        <div class="product-name">🌾 {product_name}</div>
                        <p style="color: #666; margin: 5px 0;">En cosecha - Disponible pronto</p>
                    </div>
                </div>
                
                <div class="section">
                    <h3>📧 ¿Qué sigue?</h3>
                    <p><strong>1. Cosecha:</strong> Estamos en plena cosecha de nuestros productos frescos</p>
                    <p><strong>2. Preparación:</strong> Una vez listos, prepararemos el producto con el máximo cuidado</p>
                    <p><strong>3. Te avisamos:</strong> Recibirás un email en cuanto esté disponible para comprar</p>
                </div>
                
                <div class="section">
                    <h3>🌿 Mientras tanto...</h3>
                    <p>Explora otros productos disponibles en nuestra tienda:</p>
                    <p style="text-align: center; margin-top: 15px;">
                        <a href="https://www.mikels.es/productos" style="display: inline-block; padding: 12px 24px; background-color: #2d5016; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">Ver Todos los Productos</a>
                    </p>
                </div>
                
                <div class="contact-info">
                    <h4 style="margin-top: 0; color: #2d5016;">¿Tienes alguna pregunta?</h4>
                    <p style="margin: 5px 0;">📧 Email: info@mikels.es</p>
                    <p style="margin: 5px 0;">📱 WhatsApp: +43 6789 0700 62172</p>
                    <p style="margin: 5px 0;">🌐 Web: www.mikels.es</p>
                </div>
            </div>
            
            <div class="footer">
                <p><strong>Mikel's Earth</strong></p>
                <p>Productos del campo directo a tu mesa desde 1819</p>
                <p style="font-size: 0.8em; color: #999; margin-top: 15px;">Este email se envió a {customer_email} porque solicitaste ser notificado sobre la disponibilidad de un producto.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return send_email(customer_email, subject, html_content)

