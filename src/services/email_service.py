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
                
                {f'''<div class="section">
                    <h3>📝 Datos de Factura</h3>
                    <p><strong>Nombre Fiscal:</strong> {order_data.get('invoice_data', {}).get('fiscalName', 'N/A')}</p>
                    <p><strong>NIF/CIF:</strong> {order_data.get('invoice_data', {}).get('nif', 'N/A')}</p>
                    {f"<p><strong>Dirección Fiscal:</strong> {order_data.get('invoice_data', {}).get('fiscalAddress', 'N/A')}</p>" if order_data.get('invoice_data', {}).get('fiscalAddress') else ''}
                    {f"<p><strong>Ciudad:</strong> {order_data.get('invoice_data', {}).get('fiscalCity', 'N/A')}</p>" if order_data.get('invoice_data', {}).get('fiscalCity') else ''}
                    {f"<p><strong>Código Postal:</strong> {order_data.get('invoice_data', {}).get('fiscalPostalCode', 'N/A')}</p>" if order_data.get('invoice_data', {}).get('fiscalPostalCode') else ''}
                </div>''' if order_data.get('needs_invoice') else ''}
                
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
                
                {f'''<div class="section">
                    <h3>📝 Datos de Factura</h3>
                    <div class="highlight-box">
                        <p style="margin: 5px 0;"><strong>Nombre Fiscal:</strong> {order_data.get('invoice_data', {}).get('fiscalName', 'N/A')}</p>
                        <p style="margin: 5px 0;"><strong>NIF/CIF:</strong> {order_data.get('invoice_data', {}).get('nif', 'N/A')}</p>
                        {f"<p style='margin: 5px 0;'><strong>Dirección Fiscal:</strong> {order_data.get('invoice_data', {}).get('fiscalAddress', 'N/A')}</p>" if order_data.get('invoice_data', {}).get('fiscalAddress') else ''}
                        {f"<p style='margin: 5px 0;'><strong>Ciudad:</strong> {order_data.get('invoice_data', {}).get('fiscalCity', 'N/A')} - {order_data.get('invoice_data', {}).get('fiscalPostalCode', 'N/A')}</p>" if order_data.get('invoice_data', {}).get('fiscalCity') else ''}
                    </div>
                    <p style="font-size: 0.9em; color: #666; margin-top: 10px;">Recibirás tu factura por email en las próximas 24-48 horas.</p>
                </div>''' if order_data.get('needs_invoice') else ''}
                
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
                    <p style="margin: 5px 0;">🌐 Web: www.mikels.es</p>
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
    Envía email de confirmación al cliente (bilingüe ES/EN)
    """
    customer_email = order_data.get('customer_email')
    locale = order_data.get('locale', 'es')
    
    if not customer_email or customer_email == 'N/A':
        print("⚠️ No se puede enviar email al cliente: email no disponible")
        return False
    
    if locale == 'en':
        subject = f"✅ Order Confirmed #{order_data.get('order_number', 'N/A')} - Mikel's Earth"
        html_content = format_customer_order_confirmation_en(order_data)
    else:
        subject = f"✅ Pedido Confirmado #{order_data.get('order_number', 'N/A')} - Mikel's Earth"
        html_content = format_customer_order_confirmation(order_data)
    
    return send_email(customer_email, subject, html_content)




def format_customer_order_confirmation_en(order_data):
    """
    English version of the order confirmation email for the customer
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
                <h1>✅ Order Confirmed!</h1>
                <p style="margin: 10px 0 0 0; font-size: 16px;">Thank you for your purchase</p>
            </div>
            
            <div class="content">
                <div class="section">
                    <p style="font-size: 16px; margin-top: 0;">Hello <strong>{order_data.get('customer_name', 'Customer')}</strong>,</p>
                    <p>Thank you for choosing Mikel's Earth! We have received your order and are preparing it with great care.</p>
                    
                    <div class="highlight-box">
                        <p style="margin: 0;"><strong>📋 Order Number:</strong> {order_data.get('order_number', 'N/A')}</p>
                        <p style="margin: 5px 0 0 0;"><strong>📅 Date:</strong> {datetime.now().strftime('%d/%m/%Y')}</p>
                    </div>
                </div>
                
                <div class="section">
                    <h3>📦 Order Summary</h3>
                    <table>
                        <thead>
                            <tr style="background-color: #f0f0f0;">
                                <th style="padding: 10px; text-align: left;">Product</th>
                                <th style="padding: 10px; text-align: center;">Qty</th>
                                <th style="padding: 10px; text-align: right;">Price</th>
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
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">Discount {order_data.get('discount_code', '')}:</td>
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">-{order_data.get('discount_amount', 0):.2f}€</td>
                        </tr>''' if order_data.get('discount_code') else ''}
                        <tr>
                            <td style="text-align: right; padding: 5px; color: #666;">Shipping:</td>
                            <td style="text-align: right; padding: 5px; color: #16a34a; font-weight: bold;">FREE</td>
                        </tr>
                    </table>
                    <div class="total">
                        Total: {order_data.get('total', 0):.2f}€
                    </div>
                </div>
                
                <div class="section">
                    <h3>📍 Shipping Address</h3>
                    <p>{order_data.get('shipping_address', 'N/A')}</p>
                </div>
                
                <div class="section">
                    <h3>🚚 What's Next?</h3>
                    <p>1. <strong>Preparation:</strong> We are preparing your order with fresh products from the field</p>
                    <p>2. <strong>Shipping:</strong> We will send you an email when your order is on its way</p>
                    <p>3. <strong>Delivery:</strong> You will receive your order in 3-5 business days</p>
                </div>
                
                <div class="contact-info">
                    <h4 style="margin-top: 0; color: #2d5016;">Need help?</h4>
                    <p style="margin: 5px 0;">📧 Email: info@mikels.es</p>
                    <p style="margin: 5px 0;">📱 WhatsApp: +43 6789 0700 62172</p>
                    <p style="margin: 5px 0;">🌐 Web: www.mikels.es</p>
                </div>
            </div>
            
            <div class="footer">
                <p><strong>Mikel's Earth</strong></p>
                <p>From the field, straight to your table</p>
                <p style="font-size: 0.8em; color: #999; margin-top: 15px;">This email was sent to {order_data.get('customer_email', '')} because you made a purchase in our shop.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html


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




def send_newsletter_subscription_notification(email):
    """
    Envía notificación a info@mikels.es cuando alguien se suscribe al newsletter
    """
    subject = f"🔔 Nueva suscripción al Newsletter - {email}"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ background-color: #f9f9f9; padding: 20px; border-radius: 0 0 5px 5px; }}
            .highlight {{ background-color: #f0f7e9; padding: 15px; border-left: 4px solid #2d5016; margin: 15px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📧 Nueva Suscripción al Newsletter</h1>
            </div>
            <div class="content">
                <p>Se ha registrado una nueva suscripción al newsletter de Mikel's Earth.</p>
                
                <div class="highlight">
                    <p style="margin: 0;"><strong>Email:</strong> {email}</p>
                    <p style="margin: 5px 0 0 0;"><strong>Fecha:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                </div>
                
                <p>El contacto ha sido añadido automáticamente a Brevo.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return False
        
        # Limpiar la API key
        api_key = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Mikel's Earth", "email": "noreply@mikels.es"},
                "to": [{"email": "info@mikels.es"}],
                "subject": subject,
                "htmlContent": html_content
            }
        )
        return response.status_code == 201
    except Exception as e:
        print(f"Error sending newsletter notification email: {str(e)}")
        return False


def add_contact_to_brevo(email):
    """
    Añade un contacto a la lista de newsletter en Brevo
    """
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return {"success": False, "error": "API key not configured"}
        
        # Limpiar la API key
        api_key = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        response = requests.post(
            "https://api.brevo.com/v3/contacts",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "email": email,
                "listIds": [2],  # ID de la lista de newsletter en Brevo
                "updateEnabled": True  # Actualizar si ya existe
            }
        )
        
        print(f"Brevo API response: {response.status_code}")
        
        if response.status_code in [201, 204]:
            print(f"✅ Contacto añadido a Brevo exitosamente: {email}")
            return {"success": True, "id": response.json().get('id') if response.status_code == 201 else None}
        else:
            print(f"❌ Brevo API error: {response.status_code} - {response.text}")
            return {"success": False, "error": response.text}
            
    except Exception as e:
        print(f"Error adding contact to Brevo: {str(e)}")
        return {"success": False, "error": str(e)}




def send_workshop_visit_notification(nombre, email, telefono, interes):
    """
    Envía notificación a info@mikels.es cuando alguien solicita visita al obrador
    """
    subject = f"🏭 Nueva solicitud de visita al obrador - {nombre}"
    
    interes_text = {
        'visita': 'Visita al obrador',
        'taller': 'Taller de elaboración',
        'degustacion': 'Degustación de productos'
    }.get(interes, interes)
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ background-color: #f9f9f9; padding: 20px; border-radius: 0 0 5px 5px; }}
            .highlight {{ background-color: #f0f7e9; padding: 15px; border-left: 4px solid #2d5016; margin: 15px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🏭 Nueva Solicitud de Visita al Obrador</h1>
            </div>
            <div class="content">
                <p>Se ha recibido una nueva solicitud de visita al obrador.</p>
                
                <div class="highlight">
                    <p style="margin: 5px 0;"><strong>Nombre:</strong> {nombre}</p>
                    <p style="margin: 5px 0;"><strong>Email:</strong> {email}</p>
                    <p style="margin: 5px 0;"><strong>Teléfono:</strong> {telefono if telefono else 'No proporcionado'}</p>
                    <p style="margin: 5px 0;"><strong>Interés:</strong> {interes_text}</p>
                    <p style="margin: 5px 0;"><strong>Fecha:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                </div>
                
                <p>Por favor, contacta con el interesado para coordinar la visita.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return False
        
        # Limpiar la API key
        api_key = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Mikel's Earth", "email": "noreply@mikels.es"},
                "to": [{"email": "info@mikels.es"}],
                "subject": subject,
                "htmlContent": html_content
            }
        )
        return response.status_code == 201
    except Exception as e:
        print(f"Error sending workshop visit notification: {str(e)}")
        return False


def send_workshop_visit_confirmation(nombre, email):
    """
    Envía email de confirmación al interesado en visitar el obrador
    """
    subject = "✅ Solicitud de visita recibida - Mikel's Earth"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 30px 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ background-color: #ffffff; padding: 30px; border: 1px solid #e0e0e0; border-top: none; }}
            .highlight {{ background-color: #f0f7e9; padding: 20px; border-radius: 8px; margin: 20px 0; text-align: center; }}
            .footer {{ background-color: #f9f9f9; padding: 20px; text-align: center; border-radius: 0 0 5px 5px; font-size: 0.9em; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0;">🏭 ¡Gracias por tu interés!</h1>
            </div>
            <div class="content">
                <p>Hola {nombre},</p>
                
                <p>Hemos recibido tu solicitud para visitar nuestro obrador. ¡Nos encanta que quieras conocer de cerca cómo elaboramos nuestros productos artesanales!</p>
                
                <div class="highlight">
                    <p style="font-size: 1.2em; margin: 0; color: #2d5016;">
                        <strong>Nos pondremos en contacto contigo muy pronto</strong>
                    </p>
                    <p style="margin: 10px 0 0 0; color: #666;">
                        para coordinar la fecha y hora de tu visita
                    </p>
                </div>
                
                <p><strong>¿Qué podrás ver en el obrador?</strong></p>
                <ul style="color: #555;">
                    <li>El proceso completo de elaboración artesanal</li>
                    <li>Nuestras instalaciones y maquinaria tradicional</li>
                    <li>La historia familiar detrás de Mikel's Earth</li>
                    <li>Degustación de nuestros productos (según disponibilidad)</li>
                </ul>
                
                <p>Si tienes alguna pregunta mientras tanto, no dudes en contactarnos:</p>
                <p>
                    📧 Email: <a href="mailto:info@mikels.es" style="color: #2d5016;">info@mikels.es</a><br>
                    📱 WhatsApp: <a href="https://wa.me/436789070062172" style="color: #2d5016;">+43 6789 0700 62172</a>
                </p>
                
                <p style="margin-top: 30px;">¡Hasta pronto!</p>
                <p style="color: #2d5016; font-weight: bold;">El equipo de Mikel's Earth</p>
            </div>
            <div class="footer">
                <p>Mikel's Earth - Del campo a tu mesa</p>
                <p style="font-size: 0.85em; color: #999;">Carrer Cardenal Cisneros, 10 - Lérida, España</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return False
        
        # Limpiar la API key
        api_key = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Mikel's Earth", "email": "noreply@mikels.es"},
                "to": [{"email": email, "name": nombre}],
                "subject": subject,
                "htmlContent": html_content
            }
        )
        return response.status_code == 201
    except Exception as e:
        print(f"Error sending workshop visit confirmation: {str(e)}")
        return False




def send_contact_notification(name, email, phone, message):
    """
    Envía notificación a info@mikels.es cuando alguien envía mensaje de contacto
    """
    subject = f"📬 Nuevo mensaje de contacto - {name}"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ background-color: #f9f9f9; padding: 20px; border-radius: 0 0 5px 5px; }}
            .highlight {{ background-color: #f0f7e9; padding: 15px; border-left: 4px solid #2d5016; margin: 15px 0; }}
            .message-box {{ background-color: white; padding: 15px; border-radius: 5px; margin: 15px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📬 Nuevo Mensaje de Contacto</h1>
            </div>
            <div class="content">
                <p>Has recibido un nuevo mensaje desde el formulario de contacto de la web.</p>
                
                <div class="highlight">
                    <p style="margin: 5px 0;"><strong>Nombre:</strong> {name}</p>
                    <p style="margin: 5px 0;"><strong>Email:</strong> <a href="mailto:{email}">{email}</a></p>
                    <p style="margin: 5px 0;"><strong>Teléfono:</strong> {phone if phone else 'No proporcionado'}</p>
                    <p style="margin: 5px 0;"><strong>Fecha:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                </div>
                
                <div class="message-box">
                    <p style="margin: 0 0 10px 0;"><strong>Mensaje:</strong></p>
                    <p style="margin: 0; white-space: pre-wrap;">{message}</p>
                </div>
                
                <p>Por favor, responde al cliente lo antes posible.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return False
        
        # Limpiar la API key
        api_key = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Mikel's Earth", "email": "noreply@mikels.es"},
                "to": [{"email": "info@mikels.es"}],
                "replyTo": {"email": email, "name": name},
                "subject": subject,
                "htmlContent": html_content
            }
        )
        return response.status_code == 201
    except Exception as e:
        print(f"Error sending contact notification: {str(e)}")
        return False


def send_contact_confirmation(name, email):
    """
    Envía email de confirmación al cliente que envió mensaje de contacto
    """
    subject = "✅ Mensaje recibido - Mikel's Earth"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 30px 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ background-color: #ffffff; padding: 30px; border: 1px solid #e0e0e0; border-top: none; }}
            .highlight {{ background-color: #f0f7e9; padding: 20px; border-radius: 8px; margin: 20px 0; text-align: center; }}
            .footer {{ background-color: #f9f9f9; padding: 20px; text-align: center; border-radius: 0 0 5px 5px; font-size: 0.9em; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0;">✅ ¡Mensaje recibido!</h1>
            </div>
            <div class="content">
                <p>Hola {name},</p>
                
                <p>Gracias por ponerte en contacto con nosotros. Hemos recibido tu mensaje correctamente.</p>
                
                <div class="highlight">
                    <p style="font-size: 1.2em; margin: 0; color: #2d5016;">
                        <strong>Te responderemos en menos de 24 horas</strong>
                    </p>
                </div>
                
                <p>Mientras tanto, si tu consulta es urgente, puedes contactarnos directamente por:</p>
                <p style="background-color: #f9f9f9; padding: 15px; border-radius: 5px;">
                    📧 Email: <a href="mailto:info@mikels.es" style="color: #2d5016;">info@mikels.es</a><br>
                    📱 WhatsApp: <a href="https://wa.me/436789070062172" style="color: #2d5016;">+43 6789 0700 62172</a>
                </p>
                
                <p>También te invitamos a:</p>
                <ul style="color: #555;">
                    <li><a href="https://www.mikels.es/productos" style="color: #2d5016;">Explorar nuestros productos artesanales</a></li>
                    <li><a href="https://www.mikels.es/el-obrador" style="color: #2d5016;">Conocer nuestro obrador</a></li>
                    <li><a href="https://www.mikels.es/la-familia" style="color: #2d5016;">Descubrir nuestra historia familiar</a></li>
                </ul>
                
                <p style="margin-top: 30px;">¡Gracias por tu interés en Mikel's Earth!</p>
                <p style="color: #2d5016; font-weight: bold;">El equipo de Mikel's Earth</p>
            </div>
            <div class="footer">
                <p>Mikel's Earth - Del campo a tu mesa</p>
                <p style="font-size: 0.85em; color: #999;">Carrer Cardenal Cisneros, 10 - Lérida, España</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return False
        
        # Limpiar la API key
        api_key = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Mikel's Earth", "email": "noreply@mikels.es"},
                "to": [{"email": email, "name": name}],
                "subject": subject,
                "htmlContent": html_content
            }
        )
        return response.status_code == 201
    except Exception as e:
        print(f"Error sending contact confirmation: {str(e)}")
        return False




def send_newsletter_subscription_confirmation(email):
    """
    Envía email de confirmación al usuario que se suscribe al newsletter
    """
    subject = "✅ ¡Bienvenido a la familia Mikel's Earth!"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #2d5016; color: white; padding: 30px 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ background-color: #ffffff; padding: 30px; border: 1px solid #e0e0e0; border-top: none; }}
            .highlight {{ background-color: #f0f7e9; padding: 20px; border-radius: 8px; margin: 20px 0; text-align: center; }}
            .footer {{ background-color: #f9f9f9; padding: 20px; text-align: center; border-radius: 0 0 5px 5px; font-size: 0.9em; color: #666; }}
            .btn {{ display: inline-block; padding: 12px 30px; background-color: #2d5016; color: white; text-decoration: none; border-radius: 5px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0;">🌿 ¡Bienvenido a Mikel's Earth!</h1>
            </div>
            <div class="content">
                <p>¡Gracias por suscribirte a nuestro newsletter!</p>
                
                <div class="highlight">
                    <p style="font-size: 1.2em; margin: 0; color: #2d5016;">
                        <strong>Ahora formas parte de nuestra familia</strong>
                    </p>
                    <p style="margin: 10px 0 0 0; color: #666;">
                        Más de 200 años cultivando tradición
                    </p>
                </div>
                
                <p><strong>¿Qué recibirás en tu bandeja de entrada?</strong></p>
                <ul style="color: #555;">
                    <li>🍑 Recetas exclusivas con nuestros productos</li>
                    <li>📖 Historias de nuestra tierra y tradición familiar</li>
                    <li>🎁 Ofertas especiales y promociones únicas</li>
                    <li>🌱 Consejos sobre productos naturales y artesanales</li>
                    <li>📅 Novedades y lanzamientos antes que nadie</li>
                </ul>
                
                <p style="text-align: center; margin: 30px 0;">
                    <a href="https://www.mikels.es/tienda" class="btn" style="color: white;">Descubre nuestros productos</a>
                </p>
                
                <p>Si tienes alguna pregunta, no dudes en contactarnos:</p>
                <p>
                    📧 Email: <a href="mailto:info@mikels.es" style="color: #2d5016;">info@mikels.es</a><br>
                    📱 WhatsApp: <a href="https://wa.me/436789070062172" style="color: #2d5016;">+43 6789 0700 62172</a><br>
                    🌐 Web: <a href="https://www.mikels.es" style="color: #2d5016;">www.mikels.es</a>
                </p>
                
                <p style="margin-top: 30px; font-size: 0.9em; color: #666;">
                    <em>No spam. Solo contenido de calidad. Puedes darte de baja en cualquier momento.</em>
                </p>
                
                <p style="margin-top: 30px;">¡Gracias por confiar en nosotros!</p>
                <p style="color: #2d5016; font-weight: bold;">El equipo de Mikel's Earth</p>
            </div>
            <div class="footer">
                <p>Mikel's Earth - Del campo a tu mesa desde 1819</p>
                <p style="font-size: 0.85em; color: #999;">Carrer Cardenal Cisneros, 10 - Lérida, España</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key:
            print("ERROR: BREVO_API_KEY no configurada")
            return False
        
        # Limpiar la API key
        api_key = api_key.strip().replace('\\n', '').replace('\\r', '').replace(' ', '')
        
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json={
                "sender": {"name": "Mikel's Earth", "email": "noreply@mikels.es"},
                "to": [{"email": email}],
                "subject": subject,
                "htmlContent": html_content
            }
        )
        return response.status_code == 201
    except Exception as e:
        print(f"Error sending newsletter confirmation email: {str(e)}")
        return False

