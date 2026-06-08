"""
Servicio de integración con Holded ERP (API v1)
Gestiona productos, contactos, pedidos de venta y facturación.
"""
import os
import requests
from datetime import datetime

HOLDED_API_KEY = os.environ.get('HOLDED_API_KEY', '5bd8629be1127486298dfd61cb296943')
HOLDED_BASE_URL = 'https://api.holded.com/api/invoicing/v1'

HEADERS = {
    'key': HOLDED_API_KEY,
    'Content-Type': 'application/json'
}


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


# ============================================================
# PEDIDOS DE VENTA (Sales Orders)
# ============================================================

def holded_create_sales_order(contact_id, items, notes=''):
    """
    Crea un pedido de venta en Holded.
    items: lista de dicts con {name, units, subtotal, tax (ej: 's_iva_4')}
    subtotal = precio unitario SIN IVA
    """
    try:
        order_items = []
        for item in items:
            tax_id = item.get('tax', 's_iva_4')
            order_items.append({
                'name': item.get('name', ''),
                'desc': item.get('description', ''),
                'units': item.get('units', 1),
                'subtotal': item.get('subtotal', 0),
                'taxes': [tax_id],  # Holded espera array 'taxes', no string 'tax'
                'sku': item.get('sku', '')
            })

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
        invoice_items = []
        for item in items:
            tax_id = item.get('tax', 's_iva_4')
            invoice_items.append({
                'name': item.get('name', ''),
                'desc': item.get('description', ''),
                'units': item.get('units', 1),
                'subtotal': item.get('subtotal', 0),
                'taxes': [tax_id],  # Holded espera array 'taxes', no string 'tax'
                'sku': item.get('sku', '')
            })

        payload = {
            'contactId': contact_id,
            'items': invoice_items,
            'notes': notes,
            'date': int(datetime.now().timestamp()),
            'draft': False  # No crear como borrador, numerar directamente
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


def holded_create_salesreceipt(contact_id, items, notes=''):
    """
    Crea un ticket (salesreceipt / T) en Holded.
    Se usa para clientes que NO solicitan factura formal.
    items: lista de dicts con {name, units, subtotal, tax}
    subtotal = precio unitario SIN IVA
    """
    try:
        receipt_items = []
        for item in items:
            tax_id = item.get('tax', 's_iva_4')
            receipt_items.append({
                'name': item.get('name', ''),
                'desc': item.get('description', ''),
                'units': item.get('units', 1),
                'subtotal': item.get('subtotal', 0),
                'taxes': [tax_id],  # Holded espera array 'taxes', no string 'tax'
                'sku': item.get('sku', '')
            })

        payload = {
            'contactId': contact_id,
            'items': receipt_items,
            'notes': notes,
            'date': int(datetime.now().timestamp()),
            'draft': False  # No crear como borrador
        }

        response = requests.post(
            f'{HOLDED_BASE_URL}/documents/salesreceipt',
            headers=HEADERS,
            json=payload,
            timeout=15
        )

        if response.status_code in [200, 201]:
            return True, response.json()
        print(f"[Holded] Error creando ticket: {response.status_code} - {response.text}")
        return False, response.text
    except Exception as e:
        print(f"[Holded] Error creando ticket (salesreceipt): {e}")
        return False, str(e)


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
