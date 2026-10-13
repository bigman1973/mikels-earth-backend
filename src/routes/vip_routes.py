"""Black Friday VIP landing signup API.

The public endpoint does one specific job: collect an affirmative marketing
consent, persist its evidence, subscribe the contact to the dedicated Klaviyo
list and send the corresponding event. It intentionally never issues coupons
or invokes the regular newsletter welcome flow.
"""
from collections import defaultdict
from datetime import datetime
import time

from flask import Blueprint, jsonify, request

from src.models.user import db
from src.models.vip_signup import VipSignup
from src.services.email_identity import normalize_email_address
from src.services.klaviyo_service import (
    klaviyo_subscribe_vip_contact,
    klaviyo_track_vip_signup,
)


vip_bp = Blueprint('vip', __name__)
_vip_rate_store = defaultdict(list)


def _client_ip():
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    return (forwarded_for.split(',', 1)[0].strip() or request.remote_addr or 'unknown')[:45]


def _is_rate_limited(ip):
    now = time.time()
    _vip_rate_store[ip] = [timestamp for timestamp in _vip_rate_store[ip] if now - timestamp < 3600]
    if len(_vip_rate_store[ip]) >= 5:
        return True
    _vip_rate_store[ip].append(now)
    return False


@vip_bp.route('/signup', methods=['POST'])
def signup_for_vip_list():
    """Collect an explicit Black Friday VIP email marketing consent."""
    data = request.get_json(silent=True) or {}
    client_ip = _client_ip()

    if _is_rate_limited(client_ip):
        return jsonify({'error': 'Demasiados intentos. Inténtalo más tarde.'}), 429

    email = normalize_email_address(data.get('email'))
    first_name = str(data.get('first_name') or '').strip()[:120]
    marketing_consent = data.get('marketing_consent') is True

    if not email:
        return jsonify({'error': 'Introduce un email válido.'}), 400
    if not marketing_consent:
        return jsonify({'error': 'Debes aceptar recibir comunicaciones comerciales para entrar en la lista VIP.'}), 400

    consented_at = datetime.utcnow()
    signup = VipSignup(
        email=email,
        first_name=first_name or None,
        consent_marketing=True,
        consented_at=consented_at,
        source_ip=client_ip,
    )
    db.session.add(signup)
    db.session.commit()

    subscribed, subscription_error = klaviyo_subscribe_vip_contact(
        email=email,
        first_name=first_name,
        consented_at=consented_at,
        source_ip=client_ip,
    )
    signup.klaviyo_subscription_status = 'accepted' if subscribed else 'failed'

    if not subscribed:
        signup.klaviyo_event_status = 'not_sent'
        signup.klaviyo_error = subscription_error or 'Klaviyo rechazó la suscripción VIP.'
        db.session.commit()
        return jsonify({'error': 'No se ha podido completar el alta. Inténtalo de nuevo más tarde.'}), 502

    event_sent, event_error = klaviyo_track_vip_signup(
        email=email,
        first_name=first_name,
        consented_at=consented_at,
        source_ip=client_ip,
        signup_id=signup.id,
    )
    signup.klaviyo_event_status = 'accepted' if event_sent else 'failed'
    signup.klaviyo_error = event_error if not event_sent else None
    db.session.commit()

    if not event_sent:
        return jsonify({'error': 'No se ha podido completar el alta. Inténtalo de nuevo más tarde.'}), 502

    return jsonify({
        'success': True,
        'signup_id': signup.id,
        'message': 'Alta VIP registrada.',
    }), 201
