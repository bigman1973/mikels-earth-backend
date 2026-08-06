"""
Rutas para formulario de contacto con protección anti-spam
"""
from flask import Blueprint, request, jsonify
from src.services.email_dispatcher import dispatch_contact_notification, dispatch_contact_confirmation
import re
import time
from collections import defaultdict

contact_bp = Blueprint('contact', __name__)

# ============================================================
# ANTI-SPAM: Rate limiting por IP (en memoria)
# ============================================================
_contact_rate_store = defaultdict(list)
RATE_LIMIT_MAX = 3  # Max submissions per window
RATE_LIMIT_WINDOW = 3600  # 1 hour


def _is_rate_limited(ip):
    """Check if an IP has exceeded the rate limit."""
    now = time.time()
    _contact_rate_store[ip] = [t for t in _contact_rate_store[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_contact_rate_store[ip]) >= RATE_LIMIT_MAX:
        return True
    _contact_rate_store[ip].append(now)
    return False


def _is_gibberish(text):
    """
    Detect gibberish text by checking for patterns that don't appear in real names/messages.
    """
    if not text or len(text) < 4:
        return False
    
    clean = re.sub(r'[\s\-\'\.\,\!\?\:]', '', text.lower())
    
    # Check for 5+ consecutive consonants (very rare in real text)
    if re.findall(r'[bcdfghjklmnpqrstvwxyz]{5,}', clean):
        return True
    
    # Check vowel ratio (real text has ~35-45% vowels)
    if len(clean) > 6:
        vowels = sum(1 for c in clean if c in 'aeiouáéíóúàèìòù')
        vowel_ratio = vowels / len(clean)
        if vowel_ratio < 0.15:
            return True
    
    # Check for too many uppercase letters mixed randomly (camelCase gibberish)
    if len(text) > 6:
        upper_count = sum(1 for c in text[1:] if c.isupper())
        if upper_count > len(text) * 0.35:
            return True
    
    return False


def _is_spam_contact(data):
    """
    Detect spam in contact form submissions.
    Returns (is_spam: bool, reason: str)
    """
    # 1. Honeypot check
    if data.get('_hp'):
        return True, 'honeypot_filled'
    
    # 2. Time check: less than 3 seconds = bot
    form_start = data.get('_ts')
    if form_start:
        try:
            elapsed_ms = time.time() * 1000 - float(form_start)
            if elapsed_ms < 3000:
                return True, 'too_fast'
        except (ValueError, TypeError):
            pass
    
    # 3. Name gibberish check
    name = data.get('name', '')
    if _is_gibberish(name):
        return True, 'gibberish_name'
    
    # 4. Message gibberish check
    message = data.get('message', '')
    if _is_gibberish(message):
        return True, 'gibberish_message'
    
    # 5. Message too short (less than 10 chars) and looks random
    if message and len(message.strip()) < 10 and not any(c == ' ' for c in message.strip()):
        return True, 'message_too_short_no_spaces'
    
    # 6. Check for excessive URLs in message
    url_count = len(re.findall(r'https?://', message))
    if url_count > 3:
        return True, 'too_many_urls'
    
    return False, ''


@contact_bp.route('/send-message', methods=['POST'])
def send_message():
    """
    Endpoint para enviar mensaje de contacto con protección anti-spam
    """
    try:
        # Rate limiting
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip:
            client_ip = client_ip.split(',')[0].strip()
        
        if _is_rate_limited(client_ip):
            print(f"⚠️ Contact form rate limited: {client_ip}")
            return jsonify({
                'success': True,
                'message': 'Mensaje enviado correctamente'
            }), 200
        
        data = request.get_json()
        
        # Anti-spam checks
        is_spam, spam_reason = _is_spam_contact(data)
        if is_spam:
            print(f"🚫 Contact spam blocked ({spam_reason}): name={data.get('name', '?')} email={data.get('email', '?')} IP={client_ip}")
            return jsonify({
                'success': True,
                'message': 'Mensaje enviado correctamente'
            }), 200
        
        name = data.get('name')
        email = data.get('email')
        phone = data.get('phone', '')
        message = data.get('message')
        
        if not name or not email or not message:
            return jsonify({'error': 'Nombre, email y mensaje son requeridos'}), 400
        
        # Enviar notificación a info@mikels.es (Klaviyo + Brevo fallback)
        dispatch_contact_notification(name, email, phone, message)
        
        # Enviar confirmación al cliente (Klaviyo + Brevo fallback)
        dispatch_contact_confirmation(name, email, message)
        
        print(f"✅ Contact form processed: {name} ({email})")
        
        return jsonify({
            'success': True,
            'message': 'Mensaje enviado correctamente'
        }), 200
        
    except Exception as e:
        print(f"Error in contact form: {str(e)}")
        return jsonify({'error': str(e)}), 500
