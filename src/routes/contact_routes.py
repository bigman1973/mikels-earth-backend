"""Contact form with zero-effect honeypot and optional phone validation."""
import re
import time

from flask import Blueprint, jsonify
from src.services.email_dispatcher import dispatch_contact_notification, dispatch_contact_confirmation
from src.services.form_security import honeypot_filled, phone_is_rejected, read_form_payload, silent_success

contact_bp = Blueprint('contact', __name__)

# IP limits are explicitly disabled: ingress identity is not certified.


def _is_gibberish(text):
    if not text or len(text) < 4:
        return False
    clean = re.sub(r'[\s\-\'\.\,\!\?\:]', '', text.lower())
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


def _is_spam_contact(data):
    """Preserve pre-existing timing/name/message heuristics without logging PII."""
    form_start = data.get('_ts')
    if form_start:
        try:
            if time.time() * 1000 - float(form_start) < 3000:
                return True, 'too_fast'
        except (ValueError, TypeError):
            pass
    name = data.get('name', '')
    if _is_gibberish(name):
        return True, 'gibberish_name'
    message = data.get('message', '')
    if _is_gibberish(message):
        return True, 'gibberish_message'
    if message and len(message.strip()) < 10 and ' ' not in message.strip():
        return True, 'message_too_short_no_spaces'
    if len(re.findall(r'https?://', message)) > 3:
        return True, 'too_many_urls'
    return False, ''


@contact_bp.route('/send-message', methods=['POST'])
def send_message():
    data = read_form_payload()
    if data is None:
        return jsonify({'error': 'El formulario no es válido.', 'code': 'invalid_payload'}), 400
    # HONEYPOT_OUTCOME = honeypot_discarded; fixed hook for the funnel agent.
    if honeypot_filled(data):
        return silent_success()
    if phone_is_rejected(data.get('phone')):
        return jsonify({'error': 'El teléfono de diez dígitos necesita prefijo internacional.', 'code': 'invalid_phone'}), 400
    if any(not isinstance(data.get(field, ''), str) for field in ('name', 'email', 'message')):
        return jsonify({'error': 'El formulario no es válido.', 'code': 'invalid_payload'}), 400
    try:
        is_spam, _ = _is_spam_contact(data)
        if is_spam:
            return jsonify({'success': True, 'message': 'Mensaje enviado correctamente'}), 200
        name = data.get('name')
        email = data.get('email')
        phone = data.get('phone') or ''
        message = data.get('message')
        if not name or not email or not message:
            return jsonify({'error': 'Nombre, email y mensaje son requeridos'}), 400
        dispatch_contact_notification(name, email, phone, message)
        dispatch_contact_confirmation(name, email, message)
        return jsonify({'success': True, 'message': 'Mensaje enviado correctamente'}), 200
    except Exception:
        # No exception strings, payloads, addresses or provider responses.
        return jsonify({'error': 'No se ha podido enviar el mensaje. Inténtalo más tarde.'}), 500
