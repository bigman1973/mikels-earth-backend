"""
Rutas para pedidos HORECA (Hostelería, Restauración y Catering)
Con protección anti-spam: honeypot, rate limiting, validación inteligente
"""
from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
import re
from collections import defaultdict
import time

horeca_bp = Blueprint('horeca', __name__)

# ============================================================
# ANTI-SPAM: Rate limiting por IP (en memoria)
# ============================================================
_rate_limit_store = defaultdict(list)  # {ip: [timestamp1, timestamp2, ...]}
RATE_LIMIT_MAX = 3  # Max submissions per window
RATE_LIMIT_WINDOW = 3600  # 1 hour in seconds


def _is_rate_limited(ip):
    """Check if an IP has exceeded the rate limit."""
    now = time.time()
    # Clean old entries
    _rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit_store[ip]) >= RATE_LIMIT_MAX:
        return True
    _rate_limit_store[ip].append(now)
    return False


# ============================================================
# ANTI-SPAM: Validación inteligente
# ============================================================
def _is_spam_submission(data):
    """
    Detect spam submissions using multiple heuristics.
    Returns (is_spam: bool, reason: str)
    """
    # 1. Honeypot check: if _hp field is filled, it's a bot
    if data.get('_hp'):
        return True, 'honeypot_filled'
    
    # 2. Time check: if form was submitted in less than 5 seconds, it's a bot
    form_start = data.get('_ts')
    if form_start:
        try:
            elapsed_ms = time.time() * 1000 - float(form_start)
            if elapsed_ms < 5000:  # Less than 5 seconds
                return True, 'too_fast'
        except (ValueError, TypeError):
            pass
    
    # 3. Name validation: reject if name has too many consecutive consonants (gibberish)
    name = data.get('establishmentName', '')
    contact = data.get('contactName', '')
    if _is_gibberish(name) or _is_gibberish(contact):
        return True, 'gibberish_name'
    
    # 4. Phone validation: must look like a valid phone (Spanish or international)
    phone = data.get('phone', '').strip()
    # Remove common formatting characters
    phone_clean = re.sub(r'[\s\-\.\(\)\+]', '', phone)
    if not phone_clean.isdigit():
        return True, 'invalid_phone'
    if len(phone_clean) < 9 or len(phone_clean) > 15:
        return True, 'invalid_phone_length'
    
    # 5. Email domain check: reject known spam domains
    email = data.get('email', '').lower().strip()
    spam_domains = [
        'teamupcleaning.com', 'tempmail.com', 'guerrillamail.com',
        'mailinator.com', 'throwaway.email', 'yopmail.com',
        'sharklasers.com', 'guerrillamailblock.com', 'grr.la',
        'dispostable.com', 'maildrop.cc', 'fakeinbox.com'
    ]
    email_domain = email.split('@')[-1] if '@' in email else ''
    if email_domain in spam_domains:
        return True, 'spam_email_domain'
    
    # 6. Check for excessive URLs in comments
    comments = data.get('comments', '')
    url_count = len(re.findall(r'https?://', comments))
    if url_count > 2:
        return True, 'too_many_urls'
    
    return False, ''


def _is_gibberish(text):
    """
    Detect gibberish text by checking for patterns that don't appear in real names.
    Real Spanish/Catalan/English names don't have 4+ consecutive consonants.
    """
    if not text or len(text) < 4:
        return False
    
    # Remove spaces and common separators
    clean = re.sub(r'[\s\-\'\.]', '', text.lower())
    
    # Check for 4+ consecutive consonants (very rare in real names)
    consonants = re.findall(r'[bcdfghjklmnpqrstvwxyz]{5,}', clean)
    if consonants:
        return True
    
    # Check ratio of vowels to total characters (real text has ~35-45% vowels)
    if len(clean) > 5:
        vowels = sum(1 for c in clean if c in 'aeiouáéíóúàèìòù')
        vowel_ratio = vowels / len(clean)
        if vowel_ratio < 0.15:  # Less than 15% vowels = likely gibberish
            return True
    
    # Check for too many uppercase letters mixed randomly
    if len(text) > 5:
        upper_count = sum(1 for c in text[1:] if c.isupper())  # Skip first char
        if upper_count > len(text) * 0.4:  # More than 40% uppercase after first char
            return True
    
    return False


@horeca_bp.route('/order', methods=['POST', 'OPTIONS'])
@cross_origin(origins=['https://www.mikels.es', 'https://mikels.es', 'http://localhost:5173'], supports_credentials=True)
def create_horeca_order():
    """
    Procesa una solicitud de pedido HORECA con protección anti-spam
    """
    try:
        # Rate limiting by IP
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip:
            client_ip = client_ip.split(',')[0].strip()
        
        if _is_rate_limited(client_ip):
            print(f"⚠️ HORECA rate limited: {client_ip}")
            # Return success to not reveal to bots that they're blocked
            return jsonify({
                'success': True,
                'message': 'Solicitud enviada correctamente. Recibirás una propuesta en las próximas 24 horas.'
            }), 200
        
        data = request.get_json()
        
        # Anti-spam checks
        is_spam, spam_reason = _is_spam_submission(data)
        if is_spam:
            print(f"🚫 HORECA spam blocked ({spam_reason}): {data.get('establishmentName', '?')} / {data.get('email', '?')} / IP: {client_ip}")
            # Return success to not reveal detection to bots
            return jsonify({
                'success': True,
                'message': 'Solicitud enviada correctamente. Recibirás una propuesta en las próximas 24 horas.'
            }), 200
        
        # Validar datos requeridos
        required_fields = [
            'establishmentName', 'establishmentType', 'contactName',
            'phone', 'email', 'address', 'city', 'postalCode', 'province'
        ]
        
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'El campo {field} es obligatorio'}), 400
        
        # Validar que al menos un producto tenga cantidad > 0
        aceite_5l = int(data.get('quantity5L', 0))
        aceite_temprano = int(data.get('quantityTemprano', 0))
        subscribe_newsletter = data.get('subscribeNewsletter', False)
        
        if aceite_5l == 0 and aceite_temprano == 0:
            return jsonify({'error': 'Debes seleccionar al menos un producto'}), 400
        
        # La suscripción comercial es independiente de la confirmación
        # transaccional de esta solicitud. Solo se activa cuando el formulario
        # la solicita expresamente.
        if subscribe_newsletter:
            from src.services.email_dispatcher import dispatch_add_contact
            subscription = dispatch_add_contact(
                data['email'],
                first_name=data.get('contactName', '').split(' ', 1)[0],
                source='HORECA',
                subscribe_email=True,
            )
            if not subscription.get('success'):
                print(f"[HORECA] Klaviyo newsletter subscription queued/failed: {subscription.get('error', 'unknown')}")

        # Both operational notifications are Klaviyo events. Event acceptance
        # (HTTP 202) is logged durably; failures remain in the retry ledger.
        from src.services.email_dispatcher import dispatch_horeca_request, dispatch_horeca_confirmation
        admin_accepted = dispatch_horeca_request(data)
        customer_accepted = dispatch_horeca_confirmation(data)
        if not admin_accepted:
            print(f"[HORECA] Internal notification pending Klaviyo retry: {data['establishmentName']}")
        if not customer_accepted:
            print(f"[HORECA] Customer confirmation pending Klaviyo retry: {data['email']}")

        print(f"✅ Pedido HORECA procesado: {data['establishmentName']} ({data['email']})")
        
        return jsonify({
            'success': True,
            'message': 'Solicitud enviada correctamente. Recibirás una propuesta en las próximas 24 horas.'
        }), 200
        
    except Exception as e:
        print(f"Error procesando pedido HORECA: {str(e)}")
        return jsonify({'error': 'Error procesando la solicitud'}), 500
