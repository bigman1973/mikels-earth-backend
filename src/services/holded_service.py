"""
Servicio de integración con Holded ERP (API v1)
Gestiona productos, contactos, pedidos de venta y facturación.
"""
import os
import requests
from datetime import datetime

HOLDED_API_KEY = os.environ.get('HOLDED_API_KEY', '').strip()
HOLDED_BASE_URL = 'https://api.holded.com/api/invoicing/v1'
HOLDED_V2_BASE_URL = 'https://api.holded.com/api/v2'

HEADERS = {
    'key': HOLDED_API_KEY,
    'Content-Type': 'application/json'
}


def _holded_v2_headers():
    """Return the v2 headers only when its scoped token is configured."""
    token = os.environ.get('HOLDED_V2_API_TOKEN', '').strip()
    if not token:
        return None
    return {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }


def _holded_error(stage, response=None, exception=None):
    """Preserve the actionable Holded response for the admin panel and logs."""
    if exception is not None:
        return {
            'stage': stage,
            'http_status': None,
            'literal_response': str(exception)
        }
    return {
        'stage': stage,
        'http_status': response.status_code,
        'literal_response': response.text
    }


def format_holded_error(error):
    """Format a safe, literal API failure for a user-facing admin response."""
    status = error.get('http_status')
    status_label = f'HTTP {status}' if status is not None else 'sin respuesta HTTP'
    literal = error.get('literal_response') or 'sin detalle devuelto por Holded'
    return f'Holded — {error.get("stage", "operación")}: {status_label}. Respuesta: {literal}'


# ============================================================
# PRODUCTOS
# ============================================================

def holded_get_products():
    """Obtiene todos los productos de Holded"""
    try:
        response = requests.get(f'{HOLDED_BASE_URL}/products', headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        print(f"[Holded] Error obteniendo productos: {e}")
        return []


def holded_get_product(product_id):
    """Obtiene un producto específico de Holded por ID"""
    try:
        response = requests.get(f'{HOLDED_BASE_URL}/products/{product_id}', headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"[Holded] Error obteniendo producto {product_id}: {e}")
        return None


def holded_update_product(product_id, data):
    """Actualiza un producto en Holded (precio, nombre, etc.)"""
    try:
        response = requests.put(
            f'{HOLDED_BASE_URL}/products/{product_id}',
            headers=HEADERS,
            json=data,
            timeout=10
        )
        return response.status_code == 200, response.json() if response.status_code == 200 else response.text
    except Exception as e:
        print(f"[Holded] Error actualizando producto {product_id}: {e}")
        return False, str(e)


# ============================================================
# CONTACTOS
# ============================================================

def holded_get_contacts():
    """Obtiene todos los contactos de Holded"""
    try:
        response = requests.get(f'{HOLDED_BASE_URL}/contacts', headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        print(f"[Holded] Error obteniendo contactos: {e}")
        return []


def holded_find_contact_by_email(email):
    """Busca un contacto en Holded por email"""
    if not email:
        return None
    contacts = holded_get_contacts()
    for contact in contacts:
        contact_email = contact.get('email') or ''
        if contact_email and contact_email.lower() == email.lower():
            return contact
    return None


def holded_find_contact_by_name(name):
    """Busca un contacto en Holded por nombre (comparación flexible)"""
    if not name:
        return None
    contacts = holded_get_contacts()
    name_normalized = name.strip().lower()
    for contact in contacts:
        contact_name = contact.get('name') or ''
        if contact_name and contact_name.strip().lower() == name_normalized:
            return contact
    return None


def holded_update_contact(contact_id, data):
    """
    Actualiza un contacto existente en Holded con los datos proporcionados.
    Solo actualiza campos que tengan valor (no sobreescribe con vacíos).
    """
    try:
        update_payload = {}
        if data.get('email'):
            update_payload['email'] = data['email']
        if data.get('phone'):
            update_payload['phone'] = data['phone']
            update_payload['mobile'] = data['phone']
        if data.get('vatnumber'):
            update_payload['vatnumber'] = data['vatnumber']
        
        # Actualizar dirección si hay datos
        if data.get('address') or data.get('city') or data.get('postal_code'):
            update_payload['billAddress'] = {
                'address': data.get('address', ''),
                'city': data.get('city', ''),
                'postalCode': data.get('postal_code', ''),
                'province': data.get('province', ''),
                'country': data.get('country', 'España'),
                'countryCode': data.get('country_code', 'ES')
            }
        
        if not update_payload:
            return True  # Nada que actualizar
        
        response = requests.put(
            f'{HOLDED_BASE_URL}/contacts/{contact_id}',
            headers=HEADERS,
            json=update_payload,
            timeout=10
        )
        if response.status_code in [200, 201]:
            print(f"[Holded] Contacto {contact_id} actualizado con: {list(update_payload.keys())}")
            return True
        print(f"[Holded] Error actualizando contacto {contact_id}: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        print(f"[Holded] Error actualizando contacto {contact_id}: {e}")
        return False


def holded_create_contact(data):
    """
    Crea un nuevo contacto en Holded.
    data debe incluir: name, email, y opcionalmente: phone, address, city, postalCode, etc.
    """
    try:
        contact_payload = {
            'name': data.get('name', ''),
            'email': data.get('email', ''),
            'phone': data.get('phone', ''),
            'mobile': data.get('phone', ''),
            'type': 'client',
            'billAddress': {
                'address': data.get('address', ''),
                'city': data.get('city', ''),
                'postalCode': data.get('postal_code', ''),
                'province': data.get('province', ''),
                'country': data.get('country', 'España'),
                'countryCode': data.get('country_code', 'ES')
            }
        }
        response = requests.post(
            f'{HOLDED_BASE_URL}/contacts',
            headers=HEADERS,
            json=contact_payload,
            timeout=10
        )
        if response.status_code in [200, 201]:
            return response.json()
        print(f"[Holded] Error creando contacto: {response.status_code} - {response.text}")
        return None
    except Exception as e:
        print(f"[Holded] Error creando contacto: {e}")
        return None


def _contact_update_payload(data):
    """Build the legacy Holded contact fields without including empty values."""
    update_payload = {}
    if data.get('email'):
        update_payload['email'] = data['email']
    if data.get('phone'):
        update_payload['phone'] = data['phone']
        update_payload['mobile'] = data['phone']
    if data.get('vatnumber'):
        update_payload['vatnumber'] = data['vatnumber']
    if data.get('address') or data.get('city') or data.get('postal_code'):
        update_payload['billAddress'] = {
            'address': data.get('address', ''),
            'city': data.get('city', ''),
            'postalCode': data.get('postal_code', ''),
            'province': data.get('province', ''),
            'country': data.get('country', 'España'),
            'countryCode': data.get('country_code', 'ES')
        }
    return update_payload


def _contact_create_payload(data):
    """Build a legacy Holded client-contact payload."""
    return {
        'name': data.get('name', ''),
        'email': data.get('email', ''),
        'phone': data.get('phone', ''),
        'mobile': data.get('phone', ''),
        'type': 'client',
        'billAddress': {
            'address': data.get('address', ''),
            'city': data.get('city', ''),
            'postalCode': data.get('postal_code', ''),
            'province': data.get('province', ''),
            'country': data.get('country', 'España'),
            'countryCode': data.get('country_code', 'ES')
        }
    }


def holded_get_or_create_contact_detailed(
    email, name, phone='', address_data=None, vatnumber=''
):
    """Return a contact ID or the unmodified Holded HTTP failure details.

    This is used only when a complete invoice needs an identified recipient.
    Unlike the legacy helper, it never collapses an ERP failure into ``None``.
    """
    try:
        contacts_response = requests.get(
            f'{HOLDED_BASE_URL}/contacts', headers=HEADERS, timeout=15
        )
        if contacts_response.status_code != 200:
            return None, _holded_error('consulta de contactos', contacts_response)
        contacts = contacts_response.json()
    except Exception as exc:
        return None, _holded_error('consulta de contactos', exception=exc)

    name_normalized = (name or '').strip().lower()
    email_normalized = (email or '').strip().lower()
    existing = next(
        (
            contact for contact in contacts
            if email_normalized and (contact.get('email') or '').strip().lower() == email_normalized
        ),
        None
    )
    if not existing and name_normalized:
        existing = next(
            (
                contact for contact in contacts
                if (contact.get('name') or '').strip().lower() == name_normalized
            ),
            None
        )

    contact_data = {
        'name': name,
        'email': email,
        'phone': phone,
        'vatnumber': vatnumber
    }
    if address_data:
        contact_data.update(address_data)

    if existing:
        contact_id = existing.get('id')
        if not contact_id:
            return None, {
                'stage': 'lectura de contacto existente',
                'http_status': None,
                'literal_response': 'Holded devolvió un contacto sin identificador.'
            }
        update_payload = _contact_update_payload(contact_data)
        if not update_payload:
            return contact_id, None
        try:
            update_response = requests.put(
                f'{HOLDED_BASE_URL}/contacts/{contact_id}',
                headers=HEADERS,
                json=update_payload,
                timeout=10
            )
        except Exception as exc:
            return None, _holded_error('actualización de contacto', exception=exc)
        if update_response.status_code not in (200, 201):
            return None, _holded_error('actualización de contacto', update_response)
        return contact_id, None

    try:
        create_response = requests.post(
            f'{HOLDED_BASE_URL}/contacts',
            headers=HEADERS,
            json=_contact_create_payload(contact_data),
            timeout=10
        )
    except Exception as exc:
        return None, _holded_error('creación de contacto', exception=exc)
    if create_response.status_code not in (200, 201):
        return None, _holded_error('creación de contacto', create_response)
    contact_id = create_response.json().get('id')
    if not contact_id:
        return None, {
            'stage': 'creación de contacto',
            'http_status': create_response.status_code,
            'literal_response': 'Holded respondió sin identificador de contacto.'
        }
    return contact_id, None


# ============================================================
# PEDIDOS DE VENTA (Sales Orders)
# ============================================================

def _validated_document_items(items, require_product_id=False):
    """Build document lines with master tax and optional stock linkage.

    ``product_id`` links a v2 ticket line to Holded's catalogue. Tickets must
    require it: without it a document can be correct fiscally but cannot move
    inventory. Legacy invoice and sales-order payloads are left unchanged.
    """
    document_items = []
    for item in items:
        tax_id = item.get('tax')
        sku = item.get('sku') or item.get('name') or 'sin referencia'
        if not tax_id:
            raise ValueError(f'La línea {sku} no tiene impuesto maestro de Holded; emisión cancelada.')
        product_id = str(item.get('product_id') or '').strip()
        if require_product_id and not product_id:
            raise ValueError(
                f'La línea {sku} no tiene identificador maestro de Holded; '
                'emisión cancelada para no desajustar stock.'
            )
        document_item = {
            'name': item.get('name', ''),
            'desc': item.get('description', ''),
            'units': item.get('units', 1),
            'subtotal': item.get('subtotal', 0),
            'taxes': [tax_id],  # Holded espera array 'taxes', no string 'tax'
            'sku': item.get('sku', ''),
        }
        # Legacy invoice and sales-order endpoints retain their unmodified
        # schema.  v2 tickets need the explicit product reference to move stock.
        if require_product_id:
            document_item['product_id'] = product_id
        document_items.append(document_item)
    if not document_items:
        raise ValueError('No hay líneas con impuesto maestro para emitir en Holded.')
    return document_items


def holded_create_sales_order(contact_id, items, notes=''):
    """
    Crea un pedido de venta en Holded.
    items: lista de dicts con {name, units, subtotal, tax (ej: 's_iva_4')}
    subtotal = precio unitario SIN IVA
    """
    try:
        order_items = _validated_document_items(items)

        payload = {
            'contactId': contact_id,
            'items': order_items,
            'notes': notes,
            'date': int(datetime.now().timestamp())
        }

        response = requests.post(
            f'{HOLDED_BASE_URL}/documents/salesorder',
            headers=HEADERS,
            json=payload,
            timeout=15
        )

        if response.status_code in [200, 201]:
            return True, response.json()
        print(f"[Holded] Error creando pedido: {response.status_code} - {response.text}")
        return False, response.text
    except Exception as e:
        print(f"[Holded] Error creando pedido de venta: {e}")
        return False, str(e)


# ============================================================
# FACTURAS (Invoices)
# ============================================================

def holded_create_invoice(contact_id, items, notes=''):
    """
    Crea una factura en Holded.
    items: lista de dicts con {name, units, subtotal, tax}
    subtotal = precio unitario SIN IVA
    tax = identificador del impuesto (ej: 's_iva_4')
    """
    try:
        invoice_items = _validated_document_items(items)

        payload = {
            'contactId': contact_id,
            'items': invoice_items,
            'notes': notes,
            'date': int(datetime.now().timestamp()),
            'approveDoc': True  # Aprobar directamente (no borrador) según API Holded
        }

        response = requests.post(
            f'{HOLDED_BASE_URL}/documents/invoice',
            headers=HEADERS,
            json=payload,
            timeout=15
        )

        if response.status_code in [200, 201]:
            return True, response.json()
        print(f"[Holded] Error creando factura: {response.status_code} - {response.text}")
        return False, response.text
    except Exception as e:
        print(f"[Holded] Error creando factura: {e}")
        return False, str(e)


def holded_create_salesreceipt(items, notes=''):
    """Create and approve a simplified ticket through Holded v2.

    Sales receipts under the legal threshold do not identify the recipient. The
    v2 API explicitly supports this: no ``contact_id`` is sent and no customer
    contact is looked up or created. The document and its sequential number are
    still generated exclusively in Holded.
    """
    headers = _holded_v2_headers()
    if not headers:
        return False, {
            'stage': 'configuración de ticket',
            'http_status': None,
            'literal_response': 'Falta la variable HOLDED_V2_API_TOKEN.'
        }
    try:
        legacy_items = _validated_document_items(items, require_product_id=True)
        receipt_items = [
            {
                'type': 'product',
                'name': item['name'],
                'description': item.get('desc', ''),
                'product_id': item['product_id'],
                'units': item['units'],
                'price': item['subtotal'],
                'taxes': item['taxes'],
                'sku': item.get('sku', '')
            }
            for item in legacy_items
        ]
        payload = {
            'items': receipt_items,
            'notes': notes,
            'date': datetime.now().date().isoformat(),
            'currency': 'EUR',
            'language': 'es'
        }
        create_response = requests.post(
            f'{HOLDED_V2_BASE_URL}/sales-receipts',
            headers=headers,
            json=payload,
            timeout=15
        )
    except Exception as exc:
        return False, _holded_error('creación de ticket', exception=exc)
    if create_response.status_code != 201:
        return False, _holded_error('creación de ticket', create_response)

    receipt_id = create_response.json().get('id')
    if not receipt_id:
        return False, {
            'stage': 'creación de ticket',
            'http_status': create_response.status_code,
            'literal_response': 'Holded respondió sin identificador de ticket.'
        }
    try:
        approve_response = requests.post(
            f'{HOLDED_V2_BASE_URL}/sales-receipts/{receipt_id}/approve',
            headers=headers,
            timeout=15
        )
    except Exception as exc:
        return False, _holded_error('aprobación de ticket', exception=exc)
    if approve_response.status_code != 200:
        return False, _holded_error('aprobación de ticket', approve_response)

    try:
        detail_response = requests.get(
            f'{HOLDED_V2_BASE_URL}/sales-receipts/{receipt_id}',
            headers=headers,
            timeout=15
        )
    except Exception as exc:
        return False, _holded_error('lectura de ticket aprobado', exception=exc)
    if detail_response.status_code != 200:
        return False, _holded_error('lectura de ticket aprobado', detail_response)
    receipt = detail_response.json()
    return True, {
        'id': receipt_id,
        'document_number': receipt.get('document_number', ''),
        'document': receipt
    }


def holded_get_invoice_pdf(document_id):
    """Obtiene el PDF de una factura de Holded"""
    try:
        response = requests.get(
            f'{HOLDED_BASE_URL}/documents/invoice/{document_id}/pdf',
            headers=HEADERS,
            timeout=15
        )
        if response.status_code == 200:
            return response.content  # bytes del PDF
        return None
    except Exception as e:
        print(f"[Holded] Error obteniendo PDF factura {document_id}: {e}")
        return None


def holded_send_document_email(doc_type, doc_id, emails, subject=None, message=None):
    """
    Envía un documento (factura/ticket) por email a través de Holded.
    doc_type: 'invoice' o 'salesreceipt'
    doc_id: ID del documento en Holded
    emails: lista de emails destinatarios
    subject: asunto personalizado (opcional)
    message: mensaje personalizado (opcional)
    """
    try:
        payload = {'emails': emails if isinstance(emails, list) else [emails]}
        if subject:
            payload['subject'] = subject
        if message:
            payload['message'] = message

        if doc_type == 'salesreceipt':
            headers = _holded_v2_headers()
            if not headers:
                return False, {
                    'stage': 'envío de ticket',
                    'http_status': None,
                    'literal_response': 'Falta la variable HOLDED_V2_API_TOKEN.'
                }
            response = requests.post(
                f'{HOLDED_V2_BASE_URL}/sales-receipts/{doc_id}/send',
                headers=headers,
                json=payload,
                timeout=15
            )
            if response.status_code == 200:
                return True, response.json() if response.text else {}
            return False, _holded_error('envío de ticket', response)

        response = requests.post(
            f'{HOLDED_BASE_URL}/documents/{doc_type}/{doc_id}/send',
            headers=HEADERS,
            json=payload,
            timeout=15
        )

        if response.status_code in [200, 201]:
            print(f"[Holded] Documento {doc_id} enviado por email a {emails}")
            return True, response.json() if response.text else {}
        return False, _holded_error('envío de documento', response)
    except Exception as e:
        return False, _holded_error('envío de documento', exception=e)


# ============================================================
# DOCUMENTOS DE UN CONTACTO
# ============================================================

def holded_get_contact(contact_id):
    """Obtiene un contacto específico de Holded por ID"""
    try:
        response = requests.get(f'{HOLDED_BASE_URL}/contacts/{contact_id}', headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"[Holded] Error obteniendo contacto {contact_id}: {e}")
        return None


def holded_get_contact_invoices(contact_id=None):
    """Obtiene facturas de Holded.
    Si contact_id es None, devuelve TODAS las facturas.
    Si contact_id tiene valor, filtra por ese contacto."""
    try:
        response = requests.get(
            f'{HOLDED_BASE_URL}/documents/invoice',
            headers=HEADERS,
            timeout=20
        )
        if response.status_code == 200:
            all_invoices = response.json()
            if contact_id is None:
                return all_invoices
            # Filtrar por el campo 'contact' que es el ID real del contacto en documentos
            return [inv for inv in all_invoices if inv.get('contact') == contact_id]
        return []
    except Exception as e:
        print(f"[Holded] Error obteniendo facturas: {e}")
        return []


def holded_get_contact_salesorders(contact_id):
    """Obtiene todos los pedidos de venta de un contacto específico de Holded.
    La API v1 no filtra por contactId en query params, así que filtramos manualmente."""
    try:
        response = requests.get(
            f'{HOLDED_BASE_URL}/documents/salesorder',
            headers=HEADERS,
            timeout=20
        )
        if response.status_code == 200:
            all_orders = response.json()
            # Filtrar por el campo 'contact' que es el ID real del contacto en documentos
            return [so for so in all_orders if so.get('contact') == contact_id]
        return []
    except Exception as e:
        print(f"[Holded] Error obteniendo pedidos del contacto {contact_id}: {e}")
        return []


def holded_get_contact_salesreceipts(contact_id):
    """Obtiene todos los tickets (salesreceipt/T) de un contacto específico de Holded.
    La API v1 no filtra por contactId en query params, así que filtramos manualmente."""
    try:
        response = requests.get(
            f'{HOLDED_BASE_URL}/documents/salesreceipt',
            headers=HEADERS,
            timeout=20
        )
        if response.status_code == 200:
            all_receipts = response.json()
            return [r for r in all_receipts if r.get('contact') == contact_id]
        return []
    except Exception as e:
        print(f"[Holded] Error obteniendo tickets del contacto {contact_id}: {e}")
        return []


def holded_get_all_salesreceipts():
    """Obtiene todos los tickets (salesreceipt/T) de Holded."""
    try:
        response = requests.get(
            f'{HOLDED_BASE_URL}/documents/salesreceipt',
            headers=HEADERS,
            timeout=20
        )
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        print(f"[Holded] Error obteniendo todos los tickets: {e}")
        return []


def holded_get_document(doc_type, doc_id):
    """Obtiene un documento individual de Holded por tipo e ID.
    doc_type: 'invoice', 'salesorder', 'salesreceipt'
    Devuelve el documento completo con items/products."""
    try:
        response = requests.get(
            f'{HOLDED_BASE_URL}/documents/{doc_type}/{doc_id}',
            headers=HEADERS,
            timeout=15
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"[Holded] Error obteniendo documento {doc_type}/{doc_id}: {e}")
        return None


# ============================================================
# ALMACENES Y STOCK
# ============================================================

def holded_get_warehouses():
    """Obtiene todos los almacenes de Holded"""
    try:
        response = requests.get(f'{HOLDED_BASE_URL}/warehouses', headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        print(f"[Holded] Error obteniendo almacenes: {e}")
        return []


# ============================================================
# UTILIDADES
# ============================================================

def holded_get_or_create_contact(email, name, phone='', address_data=None):
    """
    Busca un contacto por email o por nombre. Si existe, actualiza sus datos.
    Si no existe, lo crea.
    Devuelve el ID del contacto.
    """
    # 1. Buscar por email
    existing = holded_find_contact_by_email(email)
    
    # 2. Si no se encuentra por email, buscar por nombre
    if not existing:
        existing = holded_find_contact_by_name(name)
    
    # 3. Si existe, actualizar sus datos y devolver su ID
    if existing:
        contact_id = existing.get('id')
        # Preparar datos para actualizar (solo los que tengan valor)
        update_data = {}
        if email and not (existing.get('email') or ''):
            update_data['email'] = email
        elif email:
            update_data['email'] = email
        if phone:
            update_data['phone'] = phone
        if address_data:
            update_data['address'] = address_data.get('address', '')
            update_data['city'] = address_data.get('city', '')
            update_data['postal_code'] = address_data.get('postal_code', '')
            update_data['country'] = address_data.get('country', 'España')
        
        # Actualizar el contacto con los nuevos datos
        holded_update_contact(contact_id, update_data)
        return contact_id

    # 4. Si no existe ni por email ni por nombre, crear nuevo
    data = {
        'name': name,
        'email': email,
        'phone': phone
    }
    if address_data:
        data.update(address_data)

    result = holded_create_contact(data)
    if result:
        return result.get('id')
    return None
