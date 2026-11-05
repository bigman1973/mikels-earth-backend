# Local test fixture only; never imported or registered by src.main.
"""Contact form with shared server-side, keyless anti-spam protection."""
import re
import time

from flask import Blueprint, jsonify
from src.services.email_dispatcher import dispatch_contact_notification, dispatch_contact_confirmation
from src.services.form_security_prepared import (
    FormSecurityError, claim_attempt, client_ip, finish_attempt, honeypot_filled,
    normalize_form_email, phone_is_rejected, read_form_payload, silent_success,
)

contact_bp = Blueprint('prepared_contact_fixture', __name__)


def _is_gibberish(text):
    """Retain existing name/message heuristics, unrelated to email identity."""
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
    """Preserve legacy _hp/_ts and message/name spam checks."""
    if honeypot_filled(data):
        return True, 'honeypot_filled'
    form_start = data.get('_ts')
    if form_start:
        try:
            if time.time() * 1000 - float(form_start) < 3000:
                return True, 'too_fast'
        except (ValueError, TypeError):
            pass
    name = data.get('name', '')
    message = data.get('message', '')
    if _is_gibberish(name):
        return True, 'gibberish_name'
    if _is_gibberish(message):
        return True, 'gibberish_message'
    if message and len(message.strip()) < 10 and ' ' not in message.strip():
        return True, 'message_too_short_no_spaces'
    if len(re.findall(r'https?://', message)) > 3:
        return True, 'too_many_urls'
    return False, ''


@contact_bp.route('/send-message', methods=['POST'])
def send_message():
    claim = None
    try:
        data = read_form_payload()
        if honeypot_filled(data):
            return silent_success()
        ip = client_ip()
        email = normalize_form_email(data.get('email'))
        name = data.get('name')
        message = data.get('message')
        phone = data.get('phone', '')
        valid_text = isinstance(name, str) and isinstance(message, str) and bool(name.strip() and message.strip())
        bad_phone = phone_is_rejected(phone)
        is_spam, _ = _is_spam_contact(data) if valid_text else (False, '')
        claim = claim_attempt('contact', ip, email, eligible=bool(email and valid_text and not bad_phone and not is_spam))
        if not valid_text or not email:
            return jsonify({'error': 'Nombre, email válido y mensaje son requeridos'}), 400
        if bad_phone:
            return FormSecurityError('invalid_phone', 400, 0).response()
        if is_spam:
            finish_attempt(claim.attempt_id, 'contact_spam')
            return silent_success()

        # Canonical duplicates are AUDIT ONLY for contact. A second distinct
        # legitimate message must still be delivered within the IP quota.
        dispatch_contact_notification(name.strip(), email, phone or '', message.strip())
        dispatch_contact_confirmation(name.strip(), email, message.strip())
        finish_attempt(claim.attempt_id, 'contact_accepted')
        return jsonify({'success': True, 'message': 'Mensaje enviado correctamente'}), 200
    except FormSecurityError as error:
        return error.response()
    except Exception:
        if claim is not None:
            try:
                finish_attempt(claim.attempt_id, 'contact_failed')
            except FormSecurityError:
                pass
        # Never return raw exception strings containing contact or config data.
        return jsonify({'error': 'No se ha podido enviar el mensaje. Inténtalo más tarde.'}), 502
