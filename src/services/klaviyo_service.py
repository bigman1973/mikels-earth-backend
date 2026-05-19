"""
Servicio de Klaviyo para Mikel's Earth
Envía eventos transaccionales y gestiona contactos via Klaviyo API
"""
import os
import requests
from datetime import datetime

KLAVIYO_API_URL = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-10-15"


def _get_api_key():
    """Obtiene y limpia la API key de Klaviyo"""
    api_key = os.getenv('KLAVIYO_API_KEY', '')
    return api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')


def _get_headers():
    """Headers comunes para las peticiones a Klaviyo"""
    api_key = _get_api_key()
    return {
        'Authorization': f'Klaviyo-API-Key {api_key}',
        'accept': 'application/vnd.api+json',
        'content-type': 'application/vnd.api+json',
        'revision': KLAVIYO_REVISION
    }


def send_klaviyo_event(metric_name, profile_email, properties, value=None, unique_id=None, profile_attrs=None):
    """
    Envía un evento a Klaviyo via la Events API.
    
    Args:
        metric_name: Nombre del evento/métrica (ej: "Placed Order", "Newsletter Subscription")
        profile_email: Email del perfil asociado al evento
        properties: Dict con las propiedades del evento (datos del pedido, etc.)
        value: Valor monetario del evento (opcional)
        unique_id: ID único para deduplicación (opcional)
        profile_attrs: Dict con atributos adicionales del perfil (first_name, etc.)
    
    Returns:
        True si el evento se envió correctamente, False en caso contrario
    """
    api_key = _get_api_key()
    if not api_key:
        print("ERROR: KLAVIYO_API_KEY no configurada")
        return False
    
    # Construir el perfil
    profile_data = {
        "type": "profile",
        "attributes": {
            "email": profile_email
        }
    }
    
    # Añadir atributos adicionales del perfil si los hay
    if profile_attrs:
        for key, val in profile_attrs.items():
            profile_data["attributes"][key] = val
    
    # Construir el payload del evento
    event_attributes = {
        "properties": properties,
        "metric": {
            "data": {
                "type": "metric",
                "attributes": {
                    "name": metric_name
                }
            }
        },
        "profile": {
            "data": profile_data
        },
        "time": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    }
    
    if value is not None:
        event_attributes["value"] = value
    
    if unique_id:
        event_attributes["unique_id"] = unique_id
    
    payload = {
        "data": {
            "type": "event",
            "attributes": event_attributes
        }
    }
    
    try:
        response = requests.post(
            f"{KLAVIYO_API_URL}/events",
            headers=_get_headers(),
            json=payload
        )
        
        if response.status_code == 202:
            print(f"✅ [KLAVIYO] Evento '{metric_name}' enviado para {profile_email}")
            return True
        else:
            print(f"❌ [KLAVIYO] Error enviando evento '{metric_name}': {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ [KLAVIYO] Excepción enviando evento '{metric_name}': {str(e)}")
        return False


def add_contact_to_klaviyo(email, first_name=None, last_name=None, phone=None, source=None):
    """
    Añade o actualiza un perfil en Klaviyo, lo suscribe a email marketing
    y lo añade a la lista 'Newsletter Mikel's Earth'.
    
    Args:
        email: Email del contacto
        first_name: Nombre (opcional)
        last_name: Apellido (opcional)
        phone: Teléfono (opcional)
        source: Fuente de la suscripción (opcional)
    
    Returns:
        Dict con resultado de la operación
    """
    api_key = _get_api_key()
    if not api_key:
        print("ERROR: KLAVIYO_API_KEY no configurada")
        return {"success": False, "error": "API key not configured"}
    
    # ID de la lista "Newsletter Mikel's Earth" en Klaviyo
    NEWSLETTER_LIST_ID = os.getenv('KLAVIYO_NEWSLETTER_LIST_ID', 'WWPsb2')
    
    # Primero crear/actualizar el perfil
    profile_attrs = {"email": email}
    if first_name:
        profile_attrs["first_name"] = first_name
    if last_name:
        profile_attrs["last_name"] = last_name
    if phone:
        profile_attrs["phone_number"] = phone
    
    profile_properties = {}
    if source:
        profile_properties["Source"] = source
    
    profile_payload = {
        "data": {
            "type": "profile",
            "attributes": {
                **profile_attrs,
                "properties": profile_properties
            }
        }
    }
    
    try:
        # Crear o actualizar perfil
        response = requests.post(
            f"{KLAVIYO_API_URL}/profiles",
            headers=_get_headers(),
            json=profile_payload
        )
        
        if response.status_code in [200, 201, 202, 204, 409]:
            print(f"✅ [KLAVIYO] Perfil creado/actualizado para {email}")
            
            # Suscribir al email marketing Y añadir a la lista Newsletter
            subscribe_payload = {
                "data": {
                    "type": "profile-subscription-bulk-create-job",
                    "attributes": {
                        "custom_source": "Newsletter Website",
                        "profiles": {
                            "data": [
                                {
                                    "type": "profile",
                                    "attributes": {
                                        "email": email,
                                        "subscriptions": {
                                            "email": {
                                                "marketing": {
                                                    "consent": "SUBSCRIBED"
                                                }
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    },
                    "relationships": {
                        "list": {
                            "data": {
                                "type": "list",
                                "id": NEWSLETTER_LIST_ID
                            }
                        }
                    }
                }
            }
            
            sub_response = requests.post(
                f"{KLAVIYO_API_URL}/profile-subscription-bulk-create-jobs",
                headers=_get_headers(),
                json=subscribe_payload
            )
            
            if sub_response.status_code in [200, 201, 202, 204]:
                print(f"✅ [KLAVIYO] Suscripción activada y añadido a lista Newsletter para {email}")
            else:
                print(f"⚠️ [KLAVIYO] Error suscribiendo {email}: {sub_response.status_code} - {sub_response.text}")
            
            return {"success": True, "id": None}
        else:
            print(f"❌ [KLAVIYO] Error creando perfil: {response.status_code} - {response.text}")
            return {"success": False, "error": response.text}
            
    except Exception as e:
        print(f"❌ [KLAVIYO] Excepción: {str(e)}")
        return {"success": False, "error": str(e)}


# ============================================================
# Funciones de alto nivel para cada tipo de email/evento
# ============================================================

def _build_items_html(items):
    """
    Construye HTML con la tabla de productos del pedido para usar en plantillas de email.
    """
    if not items:
        return '<p>No hay productos</p>'
    
    html_parts = []
    for item in items:
        name = item.get('name', 'Producto')
        qty = item.get('quantity', 1)
        price = item.get('price', 0)
        html_parts.append(f'{name} x{qty} — {price:.2f}€')
    
    return '<br/>'.join(html_parts)


def klaviyo_notify_new_order(order_data):
    """
    Envía evento 'New Order' a Klaviyo (notificación interna)
    Trigger para Flow que envía email a info@mikels.es
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    items = order_data.get('items', [])
    subtotal = order_data.get('subtotal', order_data.get('total', 0))
    total = order_data.get('total', 0)
    discount_code = order_data.get('discount_code', '')
    discount_amount = order_data.get('discount_amount', 0)
    
    order_number = order_data.get('order_number', 'N/A')
    items_html = _build_items_html(items)
    properties = {
        "OrderNumber": order_number,
        "order_id": order_number,  # Alias para compatibilidad con subjects
        "CustomerName": order_data.get('customer_name', 'N/A'),
        "CustomerEmail": order_data.get('customer_email', 'N/A'),
        "CustomerPhone": order_data.get('customer_phone', 'N/A'),
        "Items": items,
        "ItemsHtml": items_html,
        "Subtotal": f"{subtotal:.2f}\u20ac",
        "Total": f"{total:.2f}\u20ac",
        "ShippingAddress": order_data.get('shipping_address', 'N/A'),
        "DiscountCode": discount_code,
        "DiscountAmount": f"{discount_amount:.2f}\u20ac" if discount_amount else '',
        "DiscountText": f"Descuento ({discount_code})" if discount_code else '',
        "NeedsInvoice": order_data.get('needs_invoice', False),
        "InvoiceData": order_data.get('invoice_data', {}),
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "customer_name": order_data.get('customer_name', 'N/A'),
        "customer_email": order_data.get('customer_email', 'N/A'),
        "customer_phone": order_data.get('customer_phone', 'N/A'),
        "phone": order_data.get('customer_phone', 'N/A'),
        "items_html": items_html,
        "total": f"{total:.2f}€",
        "subtotal": f"{subtotal:.2f}€",
        "shipping_address": order_data.get('shipping_address', 'N/A'),
        "date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "discount_code": discount_code,
        "discount_amount": f"{discount_amount:.2f}€" if discount_amount else '',
        "stripe_url": order_data.get('stripe_url', '')
    }
    
    return send_klaviyo_event(
        metric_name="Mikels New Order Internal",
        profile_email=owner_email,
        properties=properties,
        value=order_data.get('total', 0),
        unique_id=f"order-internal-{order_data.get('order_number', '')}"
    )


def klaviyo_send_order_confirmation(order_data):
    """
    Envía evento 'Placed Order' a Klaviyo (confirmación al cliente)
    Trigger para Flow que envía email de confirmación al cliente
    """
    customer_email = order_data.get('customer_email')
    if not customer_email or customer_email == 'N/A':
        print("⚠️ [KLAVIYO] No se puede enviar confirmación: email no disponible")
        return False
    
    items = order_data.get('items', [])
    subtotal = order_data.get('subtotal', order_data.get('total', 0))
    total = order_data.get('total', 0)
    discount_code = order_data.get('discount_code', '')
    discount_amount = order_data.get('discount_amount', 0)
    invoice_data = order_data.get('invoice_data', {})
    
    order_number = order_data.get('order_number', 'N/A')
    items_html = _build_items_html(items)
    shipping_text = "GRATIS" if total >= 40 else "4.95\u20ac"
    properties = {
        "OrderNumber": order_number,
        "order_id": order_number,  # Alias para compatibilidad con subjects
        "CustomerName": order_data.get('customer_name', 'N/A'),
        "Items": items,
        "ItemsHtml": items_html,
        "Subtotal": f"{subtotal:.2f}\u20ac",
        "Total": f"{total:.2f}\u20ac",
        "ShippingAddress": order_data.get('shipping_address', 'N/A'),
        "ShippingText": shipping_text,
        "DiscountCode": discount_code,
        "DiscountAmount": f"{discount_amount:.2f}\u20ac" if discount_amount else '',
        "DiscountText": f"Descuento ({discount_code})" if discount_code else '',
        "NeedsInvoice": order_data.get('needs_invoice', False),
        "BillingName": invoice_data.get('name', '') if invoice_data else '',
        "BillingAddress": invoice_data.get('address', '') if invoice_data else '',
        "BillingNif": invoice_data.get('nif', '') if invoice_data else '',
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "customer_name": order_data.get('customer_name', 'N/A'),
        "customer_email": order_data.get('customer_email', 'N/A'),
        "customer_phone": order_data.get('customer_phone', 'N/A'),
        "phone": order_data.get('customer_phone', 'N/A'),
        "items_html": items_html,
        "total": f"{total:.2f}€",
        "subtotal": f"{subtotal:.2f}€",
        "shipping": shipping_text,
        "shipping_address": order_data.get('shipping_address', 'N/A'),
        "date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "discount_code": discount_code,
        "discount_amount": f"{discount_amount:.2f}€" if discount_amount else ''
    }
    
    profile_attrs = {}
    customer_name = order_data.get('customer_name', '')
    if customer_name:
        parts = customer_name.split(' ', 1)
        profile_attrs["first_name"] = parts[0]
        if len(parts) > 1:
            profile_attrs["last_name"] = parts[1]
    
    return send_klaviyo_event(
        metric_name="Mikels Placed Order",
        profile_email=customer_email,
        properties=properties,
        value=order_data.get('total', 0),
        unique_id=f"order-{order_data.get('order_number', '')}",
        profile_attrs=profile_attrs
    )


def klaviyo_notify_new_subscription(subscription_data):
    """
    Envía evento de nueva suscripción a Klaviyo
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    price = subscription_data.get('price', 0)
    frequency_map = {
        'weekly': 'Semanal',
        'biweekly': 'Quincenal',
        'monthly': 'Mensual',
        'quarterly': 'Trimestral',
        'semiannual': 'Semestral'
    }
    frequency_raw = subscription_data.get('frequency', 'N/A')
    frequency_text = frequency_map.get(frequency_raw, frequency_raw)
    
    sub_number = subscription_data.get('subscription_number', 'N/A')
    properties = {
        "SubscriptionNumber": sub_number,
        "order_id": sub_number,  # Alias para compatibilidad con subjects
        "CustomerName": subscription_data.get('customer_name', 'N/A'),
        "CustomerEmail": subscription_data.get('customer_email', 'N/A'),
        "ProductName": subscription_data.get('product_name', 'N/A'),
        "Frequency": frequency_text,
        "Price": f"{price:.2f}\u20ac" if price else 'N/A',
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "customer_name": subscription_data.get('customer_name', 'N/A'),
        "customer_email": subscription_data.get('customer_email', 'N/A'),
        "product_name": subscription_data.get('product_name', 'N/A'),
        "frequency": frequency_text,
        "amount": f"{price:.2f}\u20ac" if price else 'N/A'
    }
    
    return send_klaviyo_event(
        metric_name="Mikels New Subscription Internal",
        profile_email=owner_email,
        properties=properties,
        value=subscription_data.get('price', 0),
        unique_id=f"sub-{subscription_data.get('subscription_number', '')}"
    )


def klaviyo_notify_newsletter_subscription(email, coupon_code=None, first_name=None, last_name=None, phone=None):
    """
    Envía evento de suscripción al newsletter a Klaviyo
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    properties = {
        "SubscriberEmail": email,
        "SubscriberName": f"{first_name or ''} {last_name or ''}".strip() or "No proporcionado",
        "SubscriberFirstName": first_name or "No proporcionado",
        "SubscriberLastName": last_name or "No proporcionado",
        "SubscriberPhone": phone or "No proporcionado",
        "SubscriptionDate": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "CouponCode": coupon_code or "No generado",
        "Source": "mikels-earth-website"
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Newsletter Subscription Internal",
        profile_email=owner_email,
        properties=properties
    )


def klaviyo_send_newsletter_welcome(email, coupon_code="BIENVENIDA10"):
    """
    Envía evento de bienvenida newsletter a Klaviyo
    Trigger para Flow que envía email de bienvenida con cupón
    """
    properties = {
        "CouponCode": coupon_code,
        "Source": "mikels-earth-website"
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Newsletter Welcome",
        profile_email=email,
        properties=properties
    )


def klaviyo_notify_contact_message(name, email, phone, message):
    """
    Envía evento de mensaje de contacto a Klaviyo (notificación interna)
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    properties = {
        "ContactName": name,
        "ContactEmail": email,
        "ContactPhone": phone or 'No proporcionado',
        "Message": message,
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-website",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "name": name,
        "email": email,
        "phone": phone or 'No proporcionado',
        "message": message
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Contact Message Internal",
        profile_email=owner_email,
        properties=properties
    )


def klaviyo_send_contact_confirmation(name, email, message=''):
    """
    Envía evento de confirmación de contacto al cliente
    """
    properties = {
        "ContactName": name,
        "Message": message,
        "Source": "mikels-earth-website",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "name": name,
        "message": message
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Contact Confirmation",
        profile_email=email,
        properties=properties,
        profile_attrs={"first_name": name.split(' ')[0] if name else ''}
    )


def klaviyo_notify_workshop_visit(nombre, email, telefono, interes):
    """
    Envía evento de solicitud de visita al obrador (notificación interna)
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    interes_text = {
        'visita': 'Visita al obrador',
        'taller': 'Taller de elaboración',
        'degustacion': 'Degustación de productos'
    }.get(interes, interes)
    
    properties = {
        "VisitorName": nombre,
        "VisitorEmail": email,
        "VisitorPhone": telefono or 'No proporcionado',
        "Interest": interes_text,
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-website",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "name": nombre,
        "email": email,
        "phone": telefono or 'No proporcionado',
        "preferred_date": interes_text,
        "guests": 'No especificado',
        "comments": ''
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Workshop Visit Request Internal",
        profile_email=owner_email,
        properties=properties
    )


def klaviyo_send_workshop_visit_confirmation(nombre, email, interes='visita'):
    """
    Envía evento de confirmación de visita al obrador al visitante
    """
    interes_text = {
        'visita': 'Visita al obrador',
        'taller': 'Taller de elaboración',
        'degustacion': 'Degustación de productos'
    }.get(interes, interes)
    
    properties = {
        "VisitorName": nombre,
        "Interest": interes_text,
        "Source": "mikels-earth-website",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "name": nombre,
        "guests": 'No especificado',
        "preferred_date": interes_text
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Workshop Visit Confirmation",
        profile_email=email,
        properties=properties,
        profile_attrs={"first_name": nombre.split(' ')[0] if nombre else ''}
    )


def klaviyo_notify_product_request(product_name, customer_name, customer_email, customer_phone=''):
    """
    Envía evento de solicitud de notificación de producto (notificación interna)
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    properties = {
        "ProductName": product_name,
        "CustomerName": customer_name,
        "CustomerEmail": customer_email,
        "CustomerPhone": customer_phone or 'No proporcionado',
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-website",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "name": customer_name,
        "email": customer_email,
        "product_name": product_name,
        "date": datetime.now().strftime('%d/%m/%Y %H:%M')
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Product Notification Request Internal",
        profile_email=owner_email,
        properties=properties
    )


def klaviyo_track_started_checkout(checkout_data):
    """
    Envía evento 'Mikels Started Checkout' a Klaviyo.
    Se dispara cuando el cliente inicia el checkout (crea sesión de Stripe).
    Si el cliente NO completa la compra, Klaviyo disparará el Flow de carrito abandonado.
    """
    customer_email = checkout_data.get('customer_email')
    if not customer_email:
        print("⚠️ [KLAVIYO] No se puede trackear Started Checkout: email no disponible")
        return False
    
    items = checkout_data.get('items', [])
    subtotal = checkout_data.get('subtotal', 0)
    total = checkout_data.get('total', 0)
    discount_code = checkout_data.get('discount_code', '')
    discount_amount = checkout_data.get('discount_amount', 0)
    
    items_html = _build_items_html(items)
    
    # URL para volver al checkout
    checkout_url = checkout_data.get('checkout_url', 'https://mikels.es/checkout')
    
    properties = {
        "Items": items,
        "ItemsHtml": items_html,
        "Subtotal": f"{subtotal:.2f}\u20ac",
        "Total": f"{total:.2f}\u20ac",
        "CustomerName": checkout_data.get('customer_name', ''),
        "CustomerEmail": customer_email,
        "CustomerPhone": checkout_data.get('customer_phone', ''),
        "DiscountCode": discount_code,
        "DiscountAmount": f"{discount_amount:.2f}\u20ac" if discount_amount else '',
        "CheckoutURL": checkout_url,
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend",
        # Aliases en snake_case para compatibilidad
        "items_html": items_html,
        "total": f"{total:.2f}€",
        "subtotal": f"{subtotal:.2f}€",
        "customer_name": checkout_data.get('customer_name', ''),
        "checkout_url": checkout_url
    }
    
    profile_attrs = {}
    customer_name = checkout_data.get('customer_name', '')
    if customer_name:
        parts = customer_name.split(' ', 1)
        profile_attrs["first_name"] = parts[0]
        if len(parts) > 1:
            profile_attrs["last_name"] = parts[1]
    
    return send_klaviyo_event(
        metric_name="Mikels Started Checkout",
        profile_email=customer_email,
        properties=properties,
        value=total,
        unique_id=f"checkout-{checkout_data.get('order_number', '')}",
        profile_attrs=profile_attrs
    )


def klaviyo_send_review_request(customer_email, customer_name, order_number, items):
    """
    Envía evento 'Mikels Review Request' a Klaviyo.
    Se dispara 7 días después de la compra para pedir reseña.
    """
    items_html = _build_items_html(items)
    
    properties = {
        "CustomerName": customer_name,
        "OrderNumber": order_number,
        "Items": items,
        "ItemsHtml": items_html,
        "ReviewURL": "https://www.mikels.es/opiniones",
        "Source": "mikels-earth-backend",
        # Aliases
        "customer_name": customer_name,
        "order_number": order_number,
        "items_html": items_html,
        "review_url": "https://www.mikels.es/opiniones"
    }
    
    profile_attrs = {}
    if customer_name:
        parts = customer_name.split(' ', 1)
        profile_attrs["first_name"] = parts[0]
        if len(parts) > 1:
            profile_attrs["last_name"] = parts[1]
    
    return send_klaviyo_event(
        metric_name="Mikels Review Request",
        profile_email=customer_email,
        properties=properties,
        unique_id=f"review-request-{order_number}",
        profile_attrs=profile_attrs
    )


def klaviyo_send_product_notification_confirmation(product_name, customer_name, customer_email):
    """
    Envía evento de confirmación de solicitud de notificación de producto al cliente
    """
    properties = {
        "ProductName": product_name,
        "CustomerName": customer_name,
        "Source": "mikels-earth-website",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "name": customer_name,
        "product_name": product_name
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Product Notification Confirmation",
        profile_email=customer_email,
        properties=properties,
        profile_attrs={"first_name": customer_name.split(' ')[0] if customer_name else ''}
    )


def klaviyo_send_post_purchase_event(order_data):
    """
    Envía evento 'Mikels Post Purchase' a Klaviyo.
    Genera un cupón de 10% para la próxima compra (un solo uso, vinculado al email)
    e incluye CustomerName, Items y CouponCode en el evento.
    El flow en Klaviyo se encarga de enviar el email al cliente.
    """
    customer_email = order_data.get('customer_email')
    if not customer_email or customer_email == 'N/A':
        print("⚠️ [KLAVIYO] No se puede enviar post-purchase: email no disponible")
        return False
    
    customer_name = order_data.get('customer_name', 'N/A')
    items = order_data.get('items', [])
    order_number = order_data.get('order_number', '')
    
    # Generar cupón de 10% para próxima compra
    coupon_code = None
    try:
        from src.models.coupon import Coupon, db
        import secrets as sec
        
        # Generar código único tipo VUELVE10-XXXXXXXX
        code = f"VUELVE10-{sec.token_hex(4).upper()}"
        
        # Verificar que no existe ya un cupón post-compra para este email
        existing = Coupon.query.filter_by(email=customer_email, discount_percent=10, used=False).filter(
            Coupon.code.like('VUELVE10-%')
        ).first()
        
        if existing:
            # Ya tiene un cupón de próxima compra sin usar, reutilizar
            coupon_code = existing.code
            print(f"♻️ Reutilizando cupón post-compra existente: {coupon_code} para {customer_email}")
        else:
            # Crear nuevo cupón
            new_coupon = Coupon(
                code=code,
                email=customer_email,
                discount_percent=10,
                used=False
            )
            db.session.add(new_coupon)
            db.session.commit()
            coupon_code = code
            print(f"✅ Cupón post-compra generado: {coupon_code} para {customer_email}")
    except Exception as coupon_err:
        print(f"⚠️ Error generando cupón post-compra: {coupon_err}")
        # Continuar sin cupón — el evento se envía igual
    
    # Construir array de Items con ProductName
    items_for_event = []
    for item in items:
        items_for_event.append({
            "ProductName": item.get('name', 'Producto'),
            "Quantity": item.get('quantity', 1),
            "Price": item.get('price', 0)
        })
    
    properties = {
        "CustomerName": customer_name,
        "Items": items_for_event,
        "CouponCode": coupon_code or '',
        "OrderNumber": order_number,
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend",
        # Aliases en snake_case
        "customer_name": customer_name,
        "items": items_for_event,
        "coupon_code": coupon_code or '',
        "order_number": order_number
    }
    
    profile_attrs = {}
    if customer_name and customer_name != 'N/A':
        parts = customer_name.split(' ', 1)
        profile_attrs["first_name"] = parts[0]
        if len(parts) > 1:
            profile_attrs["last_name"] = parts[1]
    
    return send_klaviyo_event(
        metric_name="Mikels Post Purchase",
        profile_email=customer_email,
        properties=properties,
        unique_id=f"post-purchase-{order_number}",
        profile_attrs=profile_attrs
    )


def klaviyo_track_started_checkout(email, customer_name, items, total, checkout_url, items_html, cart_token):
    """
    Envía evento 'Started Checkout' a Klaviyo para el flow de carrito abandonado.
    
    Propiedades del evento:
    - CheckoutURL: URL persistente para recuperar el carrito
    - ItemsHtml: HTML con los productos (nombre, imagen, precio) para el email
    - Items: Array con los productos
    - Total: Importe total del carrito
    - CustomerName: Nombre del cliente
    - CartToken: Token único del carrito
    """
    # Preparar items para el evento
    items_for_event = []
    for item in items:
        items_for_event.append({
            "ProductName": item.get('name', 'Producto'),
            "ProductImage": item.get('image', ''),
            "Price": item.get('price', 0),
            "Quantity": item.get('quantity', 1),
            "ProductURL": f"https://www.mikels.es/producto/{item.get('slug', '')}"
        })
    
    properties = {
        "CheckoutURL": checkout_url,
        "ItemsHtml": items_html,
        "Items": items_for_event,
        "Total": f"{total:.2f}",
        "TotalNumeric": total,
        "CustomerName": customer_name or '',
        "CartToken": cart_token,
        "ItemCount": sum(item.get('quantity', 1) for item in items),
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-frontend",
        # Aliases en snake_case para compatibilidad
        "checkout_url": checkout_url,
        "items_html": items_html,
        "items": items_for_event,
        "total": f"{total:.2f}",
        "customer_name": customer_name or ''
    }
    
    profile_attrs = {}
    if customer_name:
        parts = customer_name.split(' ', 1)
        profile_attrs["first_name"] = parts[0]
        if len(parts) > 1:
            profile_attrs["last_name"] = parts[1]
    
    return send_klaviyo_event(
        metric_name="Started Checkout",
        profile_email=email,
        properties=properties,
        unique_id=f"started-checkout-{cart_token}",
        profile_attrs=profile_attrs
    )


def klaviyo_track_product_notify_subscribe(email, name, product_name, product_id):
    """
    Envía evento 'Mikels Product Notification' a Klaviyo cuando un cliente
    se apunta a la lista de espera de un producto agotado.
    Dispara el flow de confirmación ("Te avisaremos cuando esté disponible").
    """
    properties = {
        "product_name": product_name,
        "product_id": product_id,
        "ProductName": product_name,
        "ProductId": product_id,
        "CustomerName": name or '',
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-website"
    }

    profile_attrs = {}
    if name:
        parts = name.split(' ', 1)
        profile_attrs["first_name"] = parts[0]
        if len(parts) > 1:
            profile_attrs["last_name"] = parts[1]

    return send_klaviyo_event(
        metric_name="Mikels Product Notification",
        profile_email=email,
        properties=properties,
        unique_id=f"product-notify-{product_id}-{email}",
        profile_attrs=profile_attrs
    )


def klaviyo_track_product_back_in_stock(email, name, product_name, product_id):
    """
    Envía evento 'Product Back In Stock' a Klaviyo cuando un producto
    vuelve a estar disponible. Se envía a cada suscriptor que lo esperaba.
    Dispara el flow de "Ya está disponible".
    """
    product_url = f"https://www.mikels.es/producto/{product_id}"

    properties = {
        "product_name": product_name,
        "product_id": product_id,
        "product_url": product_url,
        "ProductName": product_name,
        "ProductId": product_id,
        "ProductURL": product_url,
        "CustomerName": name or '',
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend"
    }

    profile_attrs = {}
    if name:
        parts = name.split(' ', 1)
        profile_attrs["first_name"] = parts[0]
        if len(parts) > 1:
            profile_attrs["last_name"] = parts[1]

    return send_klaviyo_event(
        metric_name="Product Back In Stock",
        profile_email=email,
        properties=properties,
        unique_id=f"back-in-stock-{product_id}-{email}-{datetime.now().strftime('%Y%m%d')}",
        profile_attrs=profile_attrs
    )
