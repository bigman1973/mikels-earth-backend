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


def add_contact_to_klaviyo(email, first_name=None, last_name=None, source=None):
    """
    Añade o actualiza un perfil en Klaviyo, lo suscribe a email marketing
    y lo añade a la lista 'Newsletter Mikel's Earth'.
    
    Args:
        email: Email del contacto
        first_name: Nombre (opcional)
        last_name: Apellido (opcional)
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

def klaviyo_notify_new_order(order_data):
    """
    Envía evento 'New Order' a Klaviyo (notificación interna)
    Trigger para Flow que envía email a info@mikels.es
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    properties = {
        "OrderNumber": order_data.get('order_number', 'N/A'),
        "CustomerName": order_data.get('customer_name', 'N/A'),
        "CustomerEmail": order_data.get('customer_email', 'N/A'),
        "CustomerPhone": order_data.get('customer_phone', 'N/A'),
        "Items": order_data.get('items', []),
        "Subtotal": order_data.get('subtotal', order_data.get('total', 0)),
        "Total": order_data.get('total', 0),
        "ShippingAddress": order_data.get('shipping_address', 'N/A'),
        "DiscountCode": order_data.get('discount_code', ''),
        "DiscountAmount": order_data.get('discount_amount', 0),
        "NeedsInvoice": order_data.get('needs_invoice', False),
        "InvoiceData": order_data.get('invoice_data', {}),
        "Source": "mikels-earth-backend"
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
    
    properties = {
        "OrderNumber": order_data.get('order_number', 'N/A'),
        "Items": order_data.get('items', []),
        "Subtotal": order_data.get('subtotal', order_data.get('total', 0)),
        "Total": order_data.get('total', 0),
        "ShippingAddress": order_data.get('shipping_address', 'N/A'),
        "DiscountCode": order_data.get('discount_code', ''),
        "DiscountAmount": order_data.get('discount_amount', 0),
        "NeedsInvoice": order_data.get('needs_invoice', False),
        "InvoiceData": order_data.get('invoice_data', {}),
        "Source": "mikels-earth-backend"
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
    
    properties = {
        "SubscriptionNumber": subscription_data.get('subscription_number', 'N/A'),
        "CustomerName": subscription_data.get('customer_name', 'N/A'),
        "CustomerEmail": subscription_data.get('customer_email', 'N/A'),
        "ProductName": subscription_data.get('product_name', 'N/A'),
        "Frequency": subscription_data.get('frequency', 'N/A'),
        "Price": subscription_data.get('price', 0),
        "Source": "mikels-earth-backend"
    }
    
    return send_klaviyo_event(
        metric_name="Mikels New Subscription Internal",
        profile_email=owner_email,
        properties=properties,
        value=subscription_data.get('price', 0),
        unique_id=f"sub-{subscription_data.get('subscription_number', '')}"
    )


def klaviyo_notify_newsletter_subscription(email, coupon_code=None):
    """
    Envía evento de suscripción al newsletter a Klaviyo
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    properties = {
        "SubscriberEmail": email,
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
        "Source": "mikels-earth-website"
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Contact Message Internal",
        profile_email=owner_email,
        properties=properties
    )


def klaviyo_send_contact_confirmation(name, email):
    """
    Envía evento de confirmación de contacto al cliente
    """
    properties = {
        "ContactName": name,
        "Source": "mikels-earth-website"
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
        "Source": "mikels-earth-website"
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Workshop Visit Request Internal",
        profile_email=owner_email,
        properties=properties
    )


def klaviyo_send_workshop_visit_confirmation(nombre, email):
    """
    Envía evento de confirmación de visita al obrador al visitante
    """
    properties = {
        "VisitorName": nombre,
        "Source": "mikels-earth-website"
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
        "Source": "mikels-earth-website"
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Product Notification Request Internal",
        profile_email=owner_email,
        properties=properties
    )


def klaviyo_send_product_notification_confirmation(product_name, customer_name, customer_email):
    """
    Envía evento de confirmación de solicitud de notificación de producto al cliente
    """
    properties = {
        "ProductName": product_name,
        "Source": "mikels-earth-website"
    }
    
    return send_klaviyo_event(
        metric_name="Mikels Product Notification Confirmation",
        profile_email=customer_email,
        properties=properties,
        profile_attrs={"first_name": customer_name.split(' ')[0] if customer_name else ''}
    )
