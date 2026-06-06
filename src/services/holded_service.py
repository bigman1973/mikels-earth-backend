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
    contacts = holded_get_contacts()
    for contact in contacts:
        if contact.get('email', '').lower() == email.lower():
            return contact
    return None


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
    """
    try:
        order_items = []
        for item in items:
            order_items.append({
                'name': item.get('name', ''),
                'desc': item.get('description', ''),
                'units': item.get('units', 1),
                'subtotal': item.get('subtotal', 0),
                'tax': item.get('tax', 's_iva_4'),
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
    """
    try:
        invoice_items = []
        for item in items:
            invoice_items.append({
                'name': item.get('name', ''),
                'desc': item.get('description', ''),
                'units': item.get('units', 1),
                'subtotal': item.get('subtotal', 0),
                'tax': item.get('tax', 's_iva_4'),
                'sku': item.get('sku', '')
            })

        payload = {
            'contactId': contact_id,
            'items': invoice_items,
            'notes': notes,
            'date': int(datetime.now().timestamp())
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
    Busca un contacto por email. Si no existe, lo crea.
    Devuelve el ID del contacto.
    """
    existing = holded_find_contact_by_email(email)
    if existing:
        return existing.get('id')

    # Crear nuevo contacto
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
