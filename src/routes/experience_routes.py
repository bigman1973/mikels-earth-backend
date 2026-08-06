"""
Rutas para solicitudes de visita al obrador / experiencias con protección anti-spam
"""
from flask import Blueprint, request, jsonify
from src.services.email_dispatcher import dispatch_workshop_visit_notification, dispatch_workshop_visit_confirmation
import re
import time
from collections import defaultdict

experience_bp = Blueprint('experience', __name__)

# Rate limiting
_experience_rate_store = defaultdict(list)
RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW = 3600


def _is_rate_limited(ip):
    now = time.time()
    _experience_rate_store[ip] = [t for t in _experience_rate_store[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_experience_rate_store[ip]) >= RATE_LIMIT_MAX:
        return True
    _experience_rate_store[ip].append(now)
    return False


def _is_gibberish(text):
    if not text or len(text) < 4:
        return False
    clean = re.sub(r'[\s\-\'\.\,]', '', text.lower())
    if re.findall(r'[bcdfghjklmnpqrstvwxyz]{5,}', clean):
        return True
    if len(clean) > 6:
        vowels = sum(1 for c in clean if c in 'aeiouáéíóúàèìòù')
        if vowels / len(clean) < 0.15:
            return True
    if len(text) > 6:
        upper_count = sum(1 for c in text[1:] if c.isupper())
        if upper_count > len(text) * 0.35:
            return True
    return False


def _is_spam_experience(data):
    if data.get('_hp'):
        return True, 'honeypot_filled'
    form_start = data.get('_ts')
    if form_start:
        try:
            elapsed_ms = time.time() * 1000 - float(form_start)
            if elapsed_ms < 3000:
                return True, 'too_fast'
        except (ValueError, TypeError):
            pass
    if _is_gibberish(data.get('nombre', '')):
        return True, 'gibberish_name'
    return False, ''


@experience_bp.route('/workshop-visit', methods=['POST'])
def workshop_visit():
    """
    Endpoint para solicitar visita al obrador con protección anti-spam
    """
    try:
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip:
            client_ip = client_ip.split(',')[0].strip()

        if _is_rate_limited(client_ip):
            print(f"⚠️ Experience form rate limited: {client_ip}")
            return jsonify({
                'success': True,
                'message': 'Solicitud de visita enviada correctamente'
            }), 200

        data = request.get_json()

        is_spam, spam_reason = _is_spam_experience(data)
        if is_spam:
            print(f"🚫 Experience spam blocked ({spam_reason}): name={data.get('nombre', '?')} email={data.get('email', '?')} IP={client_ip}")
            return jsonify({
                'success': True,
                'message': 'Solicitud de visita enviada correctamente'
            }), 200

        nombre = data.get('nombre')
        email = data.get('email')
        telefono = data.get('telefono', '')
        interes = data.get('interes', 'visita')
        
        if not nombre or not email:
            return jsonify({'error': 'Nombre y email son requeridos'}), 400
        
        # Enviar notificación a info@mikels.es (Klaviyo + Brevo fallback)
        dispatch_workshop_visit_notification(nombre, email, telefono, interes)
        
        # Enviar confirmación al interesado (Klaviyo + Brevo fallback)
        dispatch_workshop_visit_confirmation(nombre, email, interes)

        print(f"✅ Experience form processed: {nombre} ({email})")
        
        return jsonify({
            'success': True,
            'message': 'Solicitud de visita enviada correctamente'
        }), 200
        
    except Exception as e:
        print(f"Error in workshop visit request: {str(e)}")
        return jsonify({'error': str(e)}), 500
