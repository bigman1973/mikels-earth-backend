"""
Servicio de Klaviyo para Mikel's Earth
Envía eventos transaccionales y gestiona contactos via Klaviyo API
"""
import json
import os
import requests
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KLAVIYO_API_URL = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-10-15"


def _json_safe_payload(payload):
    """Convert non-JSON scalar values before handing a payload to requests.

    Stripe checkout and the canonical order receipt use ``Decimal`` until the
    final presentation boundary. Klaviyo's HTTP client serializes JSON itself,
    so an unconverted Decimal caused the order event to fail locally before it
    ever reached Klaviyo. Monetary display values remain in ``Receipt``; this
    conversion only makes numeric event values valid JSON numbers.
    """
    def encode(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f'Unsupported Klaviyo payload value: {type(value).__name__}')

    return json.loads(json.dumps(payload, default=encode, ensure_ascii=False))


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


def send_klaviyo_event(metric_name, profile_email, properties, value=None, unique_id=None, profile_attrs=None, return_result=False, redact_logs=False):
    """
    Envía un evento a Klaviyo via la Events API.
    
    Args:
        metric_name: Nombre del evento/métrica (ej: "Placed Order", "Newsletter Subscription")
        profile_email: Email del perfil asociado al evento
        properties: Dict con las propiedades del evento (datos del pedido, etc.)
        value: Valor monetario del evento (opcional)
        unique_id: ID único para deduplicación (opcional)
        profile_attrs: Dict con atributos adicionales del perfil (first_name, etc.)
        redact_logs: Use sanitized VIP error codes, logs and a 15-second timeout.
    
    Returns:
        True si el evento se envió correctamente, False en caso contrario
    """
    def result(success, error=None):
        return (success, error) if return_result else success

    api_key = _get_api_key()
    if not api_key:
        error = 'VIP_API_KEY_NOT_CONFIGURED' if redact_logs else 'KLAVIYO_API_KEY no configurada'
        print(f"ERROR: {error}")
        return result(False, error)
    
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
        payload = _json_safe_payload(payload)
        request_options = {'timeout': 15} if redact_logs else {}
        response = requests.post(
            f"{KLAVIYO_API_URL}/events",
            headers=_get_headers(),
            json=payload,
            **request_options,
        )
        
        if response.status_code == 202:
            if redact_logs:
                print('[KLAVIYO] VIP_EVENT_ACCEPTED HTTP 202')
            else:
                print(f"✅ [KLAVIYO] Evento '{metric_name}' enviado para {profile_email}")
            return result(True)
        else:
            if redact_logs:
                return result(False, _vip_http_error('VIP_EVENT_HTTP_ERROR', response))
            error = f"Klaviyo HTTP {response.status_code}: {response.text[:500]}"
            print(f"❌ [KLAVIYO] Error enviando evento '{metric_name}': {error}")
            return result(False, error)
            
    except Exception as e:
        if redact_logs:
            print('[KLAVIYO] VIP_EVENT_REQUEST_FAILED')
            return result(False, 'VIP_EVENT_REQUEST_FAILED')
        error = f"Klaviyo excepción: {str(e)}"
        print(f"❌ [KLAVIYO] Excepción enviando evento '{metric_name}': {str(e)}")
        return result(False, error)


def _find_klaviyo_profile_id(email):
    """Return the Klaviyo profile ID for an email without exposing profile data."""
    response = requests.get(
        f"{KLAVIYO_API_URL}/profiles/",
        headers=_get_headers(),
        params={"filter": f"equals(email,'{email}')", "page[size]": 1},
        timeout=15,
    )
    if response.status_code != 200:
        print(f"⚠️ [KLAVIYO] Error buscando perfil: {response.status_code} - {response.text}")
        return None

    profiles = response.json().get('data', [])
    return profiles[0].get('id') if profiles else None


def add_contact_to_klaviyo(
    email,
    first_name=None,
    last_name=None,
    phone=None,
    source=None,
    whatsapp_marketing_accepted=False,
    subscribe_email=True,
):
    """
    Añade o actualiza un perfil en Klaviyo y, si procede, lo suscribe al
    newsletter por email. El teléfono se borra al retirar consentimiento de
    WhatsApp para que no permanezca disponible para acciones comerciales.
    
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
    
    # ID de la lista newsletter de Mikel's Fruit en Klaviyo.
    NEWSLETTER_LIST_ID = os.getenv('KLAVIYO_NEWSLETTER_LIST_ID', 'WWPsb2')
    
    # Primero crear/actualizar el perfil
    profile_attrs = {"email": email}
    if first_name:
        profile_attrs["first_name"] = first_name
    if last_name:
        profile_attrs["last_name"] = last_name
    if phone and whatsapp_marketing_accepted:
        profile_attrs["phone_number"] = phone
    
    profile_properties = {}
    if source:
        profile_properties["Source"] = source
    profile_properties["NewsletterWhatsAppMarketingConsent"] = bool(whatsapp_marketing_accepted)
    profile_properties["NewsletterWhatsAppMarketingRecordedAt"] = datetime.utcnow().isoformat()
    
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
        profile_id = _find_klaviyo_profile_id(email)
        if profile_id:
            # Klaviyo documents that an explicit null clears a profile field.
            # This is required when a subscriber withdraws WhatsApp permission.
            profile_payload['data']['id'] = profile_id
            if not whatsapp_marketing_accepted:
                profile_payload['data']['attributes']['phone_number'] = None
            response = requests.patch(
                f"{KLAVIYO_API_URL}/profiles/{profile_id}",
                headers=_get_headers(),
                json=profile_payload,
                timeout=15,
            )
        else:
            response = requests.post(
                f"{KLAVIYO_API_URL}/profiles",
                headers=_get_headers(),
                json=profile_payload,
                timeout=15,
            )

        if response.status_code in [200, 201, 202, 204]:
            print(f"✅ [KLAVIYO] Perfil creado/actualizado para {email}")

            if not subscribe_email:
                return {"success": True, "id": profile_id}

            # Suscribir al email marketing y añadir a la lista Newsletter.
            subscribe_payload = {
                "data": {
                    "type": "profile-subscription-bulk-create-job",
                    "attributes": {
                        "custom_source": "Mikel's Fruit Website",
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
                json=subscribe_payload,
                timeout=15,
            )
            
            if sub_response.status_code in [200, 201, 202, 204]:
                print(f"✅ [KLAVIYO] Suscripción activada y añadido a lista Newsletter para {email}")
            else:
                print(f"⚠️ [KLAVIYO] Error suscribiendo {email}: {sub_response.status_code} - {sub_response.text}")
            
            return {"success": True, "id": profile_id}
        else:
            print(f"❌ [KLAVIYO] Error creando perfil: {response.status_code} - {response.text}")
            return {"success": False, "error": response.text}
            
    except Exception as e:
        print(f"❌ [KLAVIYO] Excepción: {str(e)}")
        return {"success": False, "error": str(e)}


VIP_LIST_NAME = 'Black Friday VIP 2026'
_vip_list_id_cache = None


def _vip_http_error(code, response):
    """Never expose a remote body, request URL, email or exception in VIP errors."""
    status = response.status_code
    if type(status) is int and 100 <= status <= 599:
        return f'{code}_HTTP_{status}'
    return code


def _vip_resource_id(value):
    return isinstance(value, str) and bool(value) and all(
        char.isascii() and (char.isalnum() or char in '-_') for char in value
    )


def _vip_collection(document, code):
    """An empty collection is authoritative only without a next page or errors."""
    if not isinstance(document, dict) or 'errors' in document:
        return None, f'{code}_MALFORMED'
    data = document.get('data')
    links = document.get('links', {})
    if not isinstance(data, list) or not isinstance(links, dict):
        return None, f'{code}_MALFORMED'
    next_page = links.get('next')
    if next_page is not None and (not isinstance(next_page, str) or not next_page):
        return None, f'{code}_MALFORMED'
    if next_page is not None or len(data) > 1:
        return None, f'{code}_AMBIGUOUS'
    return data, None


def _get_vip_list_id():
    """Resolve the existing dedicated VIP list; never create or alter a list."""
    global _vip_list_id_cache

    if _vip_list_id_cache:
        return _vip_list_id_cache, None

    api_key = _get_api_key()
    if not api_key:
        return None, 'VIP_API_KEY_NOT_CONFIGURED'

    configured_list_id = str(os.getenv('KLAVIYO_VIP_LIST_ID') or '').strip()
    if configured_list_id:
        if not _vip_resource_id(configured_list_id):
            return None, 'VIP_LIST_CONFIGURATION_INVALID'
        _vip_list_id_cache = configured_list_id
        return _vip_list_id_cache, None

    try:
        response = requests.get(
            f'{KLAVIYO_API_URL}/lists',
            headers=_get_headers(),
            params={
                'filter': f'equals(name,{json.dumps(VIP_LIST_NAME)})',
                'page[size]': 2,
            },
            timeout=15,
        )
        if response.status_code != 200:
            return None, _vip_http_error('VIP_LIST_LOOKUP_FAILED', response)
        existing_lists, error = _vip_collection(response.json(), 'VIP_LIST_LOOKUP')
        if error:
            return None, error
        if not existing_lists:
            return None, 'VIP_LIST_NOT_FOUND'
        vip_list = existing_lists[0]
        if (not isinstance(vip_list, dict) or vip_list.get('type') != 'list'
                or not _vip_resource_id(vip_list.get('id'))):
            return None, 'VIP_LIST_LOOKUP_MALFORMED'
        _vip_list_id_cache = vip_list['id']
        return _vip_list_id_cache, None
    except (ValueError, TypeError):
        return None, 'VIP_LIST_LOOKUP_MALFORMED'
    except Exception:
        return None, 'VIP_LIST_LOOKUP_FAILED'


def _lookup_vip_profile(email):
    """Read current email consent, distinguishing confirmed absence from failure."""
    try:
        response = requests.get(
            f'{KLAVIYO_API_URL}/profiles',
            headers=_get_headers(),
            params={
                'filter': f'equals(email,{json.dumps(email)})',
                'additional-fields[profile]': 'subscriptions',
                'page[size]': 2,
            },
            timeout=15,
        )
        if response.status_code != 200:
            return None, _vip_http_error('VIP_PROFILE_LOOKUP_FAILED', response)
        profiles, error = _vip_collection(response.json(), 'VIP_PROFILE_LOOKUP')
        if error:
            return None, error
        if not profiles:
            return None, None
        profile = profiles[0]
        if (not isinstance(profile, dict) or profile.get('type') != 'profile'
                or not _vip_resource_id(profile.get('id'))):
            return None, 'VIP_PROFILE_LOOKUP_MALFORMED'
        return profile, None
    except (ValueError, TypeError):
        return None, 'VIP_PROFILE_LOOKUP_MALFORMED'
    except Exception:
        return None, 'VIP_PROFILE_LOOKUP_FAILED'


def _vip_existing_consent_error(profile):
    """Require explicit email subscription and verified absence of suppressions."""
    marketing = profile
    for field in ('attributes', 'subscriptions', 'email', 'marketing'):
        if not isinstance(marketing, dict):
            return 'VIP_EXISTING_CONSENT_UNVERIFIED'
        marketing = marketing.get(field)
    if not isinstance(marketing, dict):
        return 'VIP_EXISTING_CONSENT_UNVERIFIED'
    consent = marketing.get('consent')
    can_receive = marketing.get('can_receive_email_marketing')
    suppression = marketing.get('suppression')
    list_suppressions = marketing.get('list_suppressions')
    if (consent not in ('SUBSCRIBED', 'NEVER_SUBSCRIBED', 'UNSUBSCRIBED')
            or type(can_receive) is not bool
            or not isinstance(suppression, list)
            or not isinstance(list_suppressions, list)):
        return 'VIP_EXISTING_CONSENT_UNVERIFIED'
    for entries, fields in (
        (suppression, ('reason', 'timestamp')),
        (list_suppressions, ('list_id', 'reason', 'timestamp')),
    ):
        if any(not isinstance(entry, dict) or any(
            not isinstance(entry.get(field), str) or not entry[field]
            for field in fields
        ) for entry in entries):
            return 'VIP_EXISTING_CONSENT_UNVERIFIED'
    if consent != 'SUBSCRIBED':
        return 'VIP_EXISTING_NO_EMAIL_CONSENT'
    if suppression or not can_receive:
        return 'VIP_EXISTING_EMAIL_SUPPRESSED'
    # Respect withdrawals from ANY list, not only the VIP list.
    if list_suppressions:
        return 'VIP_EXISTING_LIST_SUPPRESSED'
    return None


def _add_existing_vip_profile(profile, list_id):
    """Only list membership may change on an eligible existing profile."""
    error = _vip_existing_consent_error(profile)
    if error:
        return False, error
    try:
        response = requests.post(
            f'{KLAVIYO_API_URL}/lists/{list_id}/relationships/profiles',
            headers=_get_headers(),
            json={'data': [{'type': 'profile', 'id': profile['id']}]},
            timeout=15,
        )
        # 2024-10-15 Add Profiles to List documents only 204 (also idempotent).
        if response.status_code == 204:
            return True, None
        return False, _vip_http_error('VIP_LIST_MEMBERSHIP_FAILED', response)
    except Exception:
        return False, 'VIP_LIST_MEMBERSHIP_FAILED'


def klaviyo_subscribe_vip_contact(*, email, first_name='', consented_at=None, source_ip=''):
    """Add eligible existing profiles, or create and subscribe a new opt-in only.

    Existing profiles are never patched or sent to a subscription job. Lookup
    failures and uncertain creation outcomes cannot trigger creation or bulk
    subscription; retries must re-read current consent.
    """
    if not _get_api_key():
        return False, 'VIP_API_KEY_NOT_CONFIGURED'
    profile, error = _lookup_vip_profile(email)
    if error:
        return False, error
    if profile is not None:
        error = _vip_existing_consent_error(profile)
        if error:
            return False, error
    elif not isinstance(consented_at, datetime):
        return False, 'VIP_NEW_CONSENT_REQUIRED'

    list_id, error = _get_vip_list_id()
    if not list_id:
        return False, error
    if profile is not None:
        return _add_existing_vip_profile(profile, list_id)

    attributes = {
        'email': email,
        'properties': {
            'MikelsVIPConsentAt': consented_at.isoformat(),
            'MikelsVIPConsentIP': source_ip,
            'MikelsVIPConsentSource': 'mikels-vip-black-friday-2026',
        },
    }
    if first_name:
        attributes['first_name'] = first_name
    try:
        response = requests.post(
            f'{KLAVIYO_API_URL}/profiles',
            headers=_get_headers(),
            json={'data': {'type': 'profile', 'attributes': attributes}},
            timeout=15,
        )
        if response.status_code == 409:
            # A concurrent creation is EXISTING, never a reason to resubscribe.
            profile, error = _lookup_vip_profile(email)
            if error:
                return False, error
            if profile is None:
                return False, 'VIP_PROFILE_LOOKUP_CONFLICT_UNRESOLVED'
            return _add_existing_vip_profile(profile, list_id)
        if response.status_code != 201:
            return False, _vip_http_error('VIP_PROFILE_CREATE_FAILED', response)
        created = response.json()
        data = created.get('data') if isinstance(created, dict) else None
        if (not isinstance(data, dict) or 'errors' in created
                or data.get('type') != 'profile' or not _vip_resource_id(data.get('id'))):
            return False, 'VIP_PROFILE_CREATE_MALFORMED'
        profile_id = data['id']
    except (ValueError, TypeError):
        return False, 'VIP_PROFILE_CREATE_MALFORMED'
    except Exception:
        return False, 'VIP_PROFILE_CREATE_FAILED'

    attributes = {
        'email': email,
        'subscriptions': {
            'email': {
                'marketing': {
                    'consent': 'SUBSCRIBED',
                },
            },
        },
    }

    payload = {
        'data': {
            'type': 'profile-subscription-bulk-create-job',
            'attributes': {
                'custom_source': 'Mikels VIP Black Friday 2026',
                'profiles': {
                    'data': [{
                        'type': 'profile',
                        # Revision 2024-10-15 supports ID-first lookup for NEW profiles.
                        'id': profile_id,
                        'attributes': attributes,
                    }],
                },
            },
            'relationships': {
                'list': {
                    'data': {
                        'type': 'list',
                        'id': list_id,
                    },
                },
            },
        },
    }

    try:
        response = requests.post(
            f'{KLAVIYO_API_URL}/profile-subscription-bulk-create-jobs',
            headers=_get_headers(),
            json=payload,
            timeout=15,
        )
        if response.status_code == 202:
            return True, None
        return False, _vip_http_error('VIP_NEW_SUBSCRIPTION_FAILED', response)
    except Exception:
        return False, 'VIP_NEW_SUBSCRIPTION_FAILED'


def klaviyo_track_vip_signup(*, email, first_name='', consented_at, source_ip, signup_id):
    """Send the event that records an accepted website VIP signup."""
    return send_klaviyo_event(
        metric_name='Mikels VIP Signup',
        profile_email=email,
        properties={
            'ConsentDate': consented_at.isoformat(),
            'ConsentIP': source_ip,
            'Source': 'mikels-vip-black-friday-2026',
            'VipSignupId': signup_id,
        },
        unique_id=f'vip-signup-{signup_id}',
        return_result=True,
        redact_logs=True,
    )


# ============================================================
# Funciones de alto nivel para cada tipo de email/evento
# ============================================================


def _format_eur(value):
    """Return a customer-facing euro amount in Spanish notation."""
    try:
        amount = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal('0.00')

    formatted = f'{amount:,.2f}'
    return formatted.replace(',', 'X').replace('.', ',').replace('X', '.') + ' €'

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
        line_display = item.get('line_display')
        if line_display:
            html_parts.append(f'{name}<br/>{line_display}')
        else:
            price = item.get('price', 0)
            html_parts.append(f'{name} x{qty} — {_format_eur(price)}')
    
    return '<br/>'.join(html_parts)


def klaviyo_notify_new_order(order_data, return_result=False):
    """
    Envía evento 'New Order' a Klaviyo (notificación interna)
    Trigger para Flow que envía email a info@mikels.es
    """
    owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es')
    
    items = order_data.get('items', [])
    receipt = order_data.get('receipt') or {}
    receipt_totals = receipt.get('totals') or {}
    subtotal = receipt_totals.get('subtotal_display') or _format_eur(order_data.get('subtotal', order_data.get('total', 0)))
    shipping = receipt_totals.get('shipping_display') or 'GRATIS'
    tax = receipt_totals.get('tax_display') or _format_eur(0)
    total = receipt_totals.get('total_display') or _format_eur(order_data.get('total', 0))
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
        "Receipt": receipt,
        "Subtotal": subtotal,
        "ShippingText": shipping,
        "Tax": tax,
        "Total": total,
        "ShippingAddress": order_data.get('shipping_address', 'N/A'),
        "DiscountCode": discount_code,
        "DiscountAmount": _format_eur(discount_amount) if discount_amount else '',
        "DiscountText": f"Descuento ({discount_code})" if discount_code else '',
        "NeedsInvoice": order_data.get('needs_invoice', False),
        "InvoiceData": order_data.get('invoice_data', {}),
        "Date": receipt.get('paid_at_display') or datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "customer_name": order_data.get('customer_name', 'N/A'),
        "customer_email": order_data.get('customer_email', 'N/A'),
        "customer_phone": order_data.get('customer_phone', 'N/A'),
        "phone": order_data.get('customer_phone', 'N/A'),
        "items_html": items_html,
        "total": total,
        "subtotal": subtotal,
        "shipping": shipping,
        "tax": tax,
        "shipping_address": order_data.get('shipping_address', 'N/A'),
        "date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "discount_code": discount_code,
        "discount_amount": _format_eur(discount_amount) if discount_amount else '',
        "stripe_url": order_data.get('stripe_url', '')
    }
    
    return send_klaviyo_event(
        metric_name="Mikels New Order Internal",
        profile_email=owner_email,
        properties=properties,
        value=order_data.get('total', 0),
        unique_id=f"order-internal-{order_data.get('order_number', '')}",
        return_result=return_result,
    )


def klaviyo_send_order_confirmation(order_data, return_result=False):
    """
    Envía evento 'Placed Order' a Klaviyo (confirmación al cliente)
    Trigger para Flow que envía email de confirmación al cliente
    """
    customer_email = order_data.get('customer_email')
    if not customer_email or customer_email == 'N/A':
        error = "No se puede enviar confirmación: email del cliente no disponible"
        print(f"⚠️ [KLAVIYO] {error}")
        return (False, error) if return_result else False
    
    items = order_data.get('items', [])
    receipt = order_data.get('receipt') or {}
    receipt_totals = receipt.get('totals') or {}
    subtotal = receipt_totals.get('subtotal_display') or _format_eur(order_data.get('subtotal', order_data.get('total', 0)))
    shipping_text = receipt_totals.get('shipping_display') or 'GRATIS'
    tax = receipt_totals.get('tax_display') or _format_eur(0)
    total = receipt_totals.get('total_display') or _format_eur(order_data.get('total', 0))
    discount_code = order_data.get('discount_code', '')
    discount_amount = order_data.get('discount_amount', 0)
    invoice_data = order_data.get('invoice_data', {})
    
    order_number = order_data.get('order_number', 'N/A')
    items_html = _build_items_html(items)
    properties = {
        "OrderNumber": order_number,
        "order_id": order_number,  # Alias para compatibilidad con subjects
        "CustomerName": order_data.get('customer_name', 'N/A'),
        "Items": items,
        "ItemsHtml": items_html,
        "Receipt": receipt,
        "Subtotal": subtotal,
        "ShippingText": shipping_text,
        "Tax": tax,
        "Total": total,
        "ShippingAddress": order_data.get('shipping_address', 'N/A'),
        "ShippingText": shipping_text,
        "DiscountCode": discount_code,
        "DiscountAmount": _format_eur(discount_amount) if discount_amount else '',
        "DiscountText": f"Descuento ({discount_code})" if discount_code else '',
        "NeedsInvoice": order_data.get('needs_invoice', False),
        "BillingName": invoice_data.get('name', '') if invoice_data else '',
        "BillingAddress": invoice_data.get('address', '') if invoice_data else '',
        "BillingNif": invoice_data.get('nif', '') if invoice_data else '',
        "Date": receipt.get('paid_at_display') or datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "customer_name": order_data.get('customer_name', 'N/A'),
        "customer_email": order_data.get('customer_email', 'N/A'),
        "customer_phone": order_data.get('customer_phone', 'N/A'),
        "phone": order_data.get('customer_phone', 'N/A'),
        "items_html": items_html,
        "total": total,
        "subtotal": subtotal,
        "shipping": shipping_text,
        "tax": tax,
        "shipping_address": order_data.get('shipping_address', 'N/A'),
        "date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "discount_code": discount_code,
        "discount_amount": _format_eur(discount_amount) if discount_amount else ''
    }
    
    profile_attrs = {}
    customer_name = order_data.get('customer_name', '')
    if customer_name:
        parts = customer_name.split(' ', 1)
        profile_attrs["first_name"] = parts[0]
        if len(parts) > 1:
            profile_attrs["last_name"] = parts[1]

    # Klaviyo exposes this request's ``value`` as the event's $value for Flow
    # revenue attribution. Prefer the immutable Receipt total, saved after
    # Stripe confirmation, over a missing caller default.
    revenue_value = (
        receipt_totals.get('total')
        if receipt_totals.get('total') is not None
        else order_data.get('total', 0)
    )
    properties['OrderValue'] = revenue_value
    
    return send_klaviyo_event(
        metric_name="Mikels Placed Order",
        profile_email=customer_email,
        properties=properties,
        value=revenue_value,
        unique_id=f"order-{order_data.get('order_number', '')}",
        profile_attrs=profile_attrs,
        return_result=return_result,
    )


def klaviyo_send_order_cancellation(order_data, return_result=False):
    """Send the transactional full-refund cancellation event to Klaviyo.

    ``Receipt`` is built from the persisted paid order.  The Flow template
    only renders its display values and never performs monetary arithmetic.
    ``unique_id`` makes a Stripe webhook retry safe.
    """
    customer_email = order_data.get('customer_email')
    if not customer_email or customer_email == 'N/A':
        error = "No se puede enviar aviso de anulación: email del cliente no disponible"
        print(f"⚠️ [KLAVIYO] {error}")
        return (False, error) if return_result else False

    receipt = order_data.get('receipt') or {}
    cancellation = receipt.get('cancellation') or {}
    order_number = order_data.get('order_number', 'N/A')
    customer_name = order_data.get('customer_name', '')
    profile_attrs = {}
    if customer_name:
        parts = customer_name.split(' ', 1)
        profile_attrs['first_name'] = parts[0]
        if len(parts) > 1:
            profile_attrs['last_name'] = parts[1]

    revenue_value = (
        cancellation.get('refunded_amount')
        if cancellation.get('refunded_amount') is not None
        else order_data.get('refund_amount', 0)
    )
    properties = {
        'OrderNumber': order_number,
        'order_id': order_number,
        'CustomerName': customer_name or 'Cliente',
        'CustomerEmail': customer_email,
        'Receipt': receipt,
        'RefundAmount': cancellation.get('refunded_amount_display') or _format_eur(order_data.get('refund_amount', 0)),
        'RefundValue': revenue_value,
        'CancellationMessage': cancellation.get('message') or '',
        'CancellationSupportMessage': cancellation.get('support_message') or '',
        'Source': 'mikels-earth-backend',
    }
    return send_klaviyo_event(
        metric_name='Mikels Order Cancelled',
        profile_email=customer_email,
        properties=properties,
        value=revenue_value,
        unique_id=f"order-cancelled-{order_number}",
        profile_attrs=profile_attrs,
        return_result=return_result,
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
        "Price": _format_eur(price) if price else 'N/A',
        "Date": datetime.now().strftime('%d/%m/%Y %H:%M'),
        "Source": "mikels-earth-backend",
        # Aliases en snake_case para compatibilidad con plantillas existentes
        "customer_name": subscription_data.get('customer_name', 'N/A'),
        "customer_email": subscription_data.get('customer_email', 'N/A'),
        "product_name": subscription_data.get('product_name', 'N/A'),
        "frequency": frequency_text,
        "amount": _format_eur(price) if price else 'N/A'
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


def klaviyo_track_started_checkout(
    checkout_data=None,
    *,
    email=None,
    customer_name='',
    items=None,
    total=0,
    checkout_url=None,
    items_html=None,
    cart_token=None,
):
    """Send the one canonical ``Mikels Started Checkout`` event to Klaviyo.

    The persistent abandoned-cart route is the single public dispatch path.
    ``checkout_data`` remains accepted for test and compatibility callers, but
    creating a Stripe Checkout Session must not call this function: it would
    re-enter the Flow seconds after the cart endpoint did.
    """
    if checkout_data is not None:
        if not isinstance(checkout_data, dict):
            raise ValueError('checkout_data must be a dictionary')
        email = checkout_data.get('customer_email') or checkout_data.get('email') or email
        customer_name = checkout_data.get('customer_name', customer_name)
        items = checkout_data.get('items', items)
        total = checkout_data.get('total', total)
        checkout_url = checkout_data.get('checkout_url', checkout_url)
        items_html = checkout_data.get('items_html', items_html)
        cart_token = (
            checkout_data.get('cart_token')
            or checkout_data.get('order_number')
            or cart_token
        )

    if not email:
        print("[KLAVIYO] Started Checkout omitted: email unavailable")
        return False

    items = items or []
    checkout_url = checkout_url or 'https://www.mikels.es/tienda'
    cart_token = cart_token or f'email-{email.lower()}'

    items_for_event = []
    for item in items:
        quantity = int(item.get('quantity', 1) or 1)
        unit_price = item.get('price', 0)
        line_total = item.get('line_total')
        line_total = line_total if line_total is not None else unit_price * quantity
        items_for_event.append({
            'ProductName': item.get('name', 'Producto'),
            'ProductImage': item.get('image', ''),
            'Price': unit_price,
            'PriceFormatted': item.get('unit_price_display') or _format_eur(unit_price),
            'UnitPriceDisplay': item.get('unit_price_display') or _format_eur(unit_price),
            'LineTotal': line_total,
            'LineTotalDisplay': item.get('line_total_display') or _format_eur(line_total),
            'LineDisplay': item.get('line_display') or (
                f"{quantity} × {_format_eur(unit_price)} = {_format_eur(line_total)}"
            ),
            'PricingNote': item.get('pricing_note') or '',
            'Quantity': quantity,
            'ProductURL': f"https://www.mikels.es/producto/{item.get('slug', '')}",
        })

    rendered_items_html = items_html if items_html is not None else _build_items_html(items)
    properties = {
        'CheckoutURL': checkout_url,
        'ItemsHtml': rendered_items_html,
        'Items': items_for_event,
        'Total': f'{float(total):.2f}',
        'TotalFormatted': _format_eur(total),
        'TotalNumeric': float(total),
        'CustomerName': customer_name or '',
        'CartToken': cart_token,
        'ItemCount': sum(item.get('quantity', 1) for item in items),
        'Date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'Source': 'mikels-earth-backend',
        # Lowercase aliases preserve existing template compatibility.
        'checkout_url': checkout_url,
        'items_html': rendered_items_html,
        'items': items_for_event,
        'total': f'{float(total):.2f}',
        'total_formatted': _format_eur(total),
        'customer_name': customer_name or '',
    }

    profile_attrs = {}
    if customer_name:
        parts = customer_name.split(' ', 1)
        profile_attrs['first_name'] = parts[0]
        if len(parts) > 1:
            profile_attrs['last_name'] = parts[1]

    return send_klaviyo_event(
        metric_name='Mikels Started Checkout',
        profile_email=email,
        properties=properties,
        value=float(total),
        unique_id=f'started-checkout-{cart_token}',
        profile_attrs=profile_attrs,
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
