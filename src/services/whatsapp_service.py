import requests
import os
from datetime import datetime

def send_whatsapp_notification(phone_number, message):
    """
    Envía un mensaje de WhatsApp usando la API de WhatsApp Business Cloud
    
    Args:
        phone_number: Número de teléfono en formato internacional (ej: 436789070062172)
        message: Mensaje a enviar
    """
    # Por ahora, usaremos la API de WhatsApp Web (método simple)
    # Para producción, deberías usar WhatsApp Business API oficial
    
    # Como alternativa temporal, vamos a usar un servicio de terceros
    # o simplemente logear el mensaje para que lo envíes manualmente
    
    print(f"\n{'='*60}")
    print(f"NOTIFICACIÓN WHATSAPP")
    print(f"{'='*60}")
    print(f"Para: +{phone_number}")
    print(f"Mensaje:\n{message}")
    print(f"{'='*60}\n")
    
    # TODO: Integrar con WhatsApp Business API oficial
    # Requiere configuración en Meta Developer Console
    
    return True


def format_order_notification(order_data):
    """
    Formatea los datos del pedido para WhatsApp
    
    Args:
        order_data: Diccionario con información del pedido
    """
    items_text = ""
    for item in order_data.get('items', []):
        items_text += f"• {item['name']} x{item['quantity']}\n"
    
    message = f"""🛒 *NUEVO PEDIDO* 🛒

📋 *Número de Pedido:* {order_data.get('order_number', 'N/A')}
📅 *Fecha:* {datetime.now().strftime('%d/%m/%Y %H:%M')}

👤 *CLIENTE*
Nombre: {order_data.get('customer_name', 'N/A')}
Email: {order_data.get('customer_email', 'N/A')}
Teléfono: {order_data.get('customer_phone', 'N/A')}

📦 *PRODUCTOS*
{items_text}
💰 *Total:* {order_data.get('total', 0)}€

📍 *ENVÍO*
{order_data.get('shipping_address', 'N/A')}

✅ *Estado:* Pago confirmado
🔗 *Ver en Stripe:* https://dashboard.stripe.com/payments"""

    return message


def format_subscription_notification(subscription_data):
    """
    Formatea los datos de la suscripción para WhatsApp
    
    Args:
        subscription_data: Diccionario con información de la suscripción
    """
    message = f"""🔄 *NUEVA SUSCRIPCIÓN* 🔄

📋 *Número:* {subscription_data.get('subscription_number', 'N/A')}
📅 *Fecha:* {datetime.now().strftime('%d/%m/%Y %H:%M')}

👤 *CLIENTE*
Nombre: {subscription_data.get('customer_name', 'N/A')}
Email: {subscription_data.get('customer_email', 'N/A')}

📦 *PRODUCTO*
{subscription_data.get('product_name', 'N/A')}

🔁 *Frecuencia:* {subscription_data.get('frequency', 'N/A')}
💰 *Precio:* {subscription_data.get('price', 0)}€

✅ *Estado:* Activa
🔗 *Ver en Stripe:* https://dashboard.stripe.com/subscriptions"""

    return message


def notify_new_order(order_data):
    """
    Envía notificación de nuevo pedido por WhatsApp
    """
    phone = os.getenv('OWNER_WHATSAPP', '436789070062172')
    message = format_order_notification(order_data)
    return send_whatsapp_notification(phone, message)


def notify_new_subscription(subscription_data):
    """
    Envía notificación de nueva suscripción por WhatsApp
    """
    phone = os.getenv('OWNER_WHATSAPP', '436789070062172')
    message = format_subscription_notification(subscription_data)
    return send_whatsapp_notification(phone, message)

