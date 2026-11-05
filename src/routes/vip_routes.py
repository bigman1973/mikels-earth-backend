"""Black Friday VIP landing signup API.

Persist each affirmative form declaration locally. Existing Klaviyo profiles
may join the dedicated list only with current email consent and no suppression;
the form must never renew or override their remote subscription state.
"""
from datetime import datetime

from flask import Blueprint, jsonify

from src.models.user import db
from src.models.vip_signup import VipSignup
from src.services.email_identity import normalize_email_address
from src.services.form_security import (
    UNVERIFIED_IP, consent_ip_evidence, honeypot_filled, phone_is_rejected,
    read_form_payload, silent_success,
)
from src.services.klaviyo_service import (
    klaviyo_subscribe_vip_contact,
    klaviyo_track_vip_signup,
)


vip_bp = Blueprint('vip', __name__)
_VIP_INELIGIBLE_ERRORS = frozenset({
    'VIP_EXISTING_NO_EMAIL_CONSENT',
    'VIP_EXISTING_EMAIL_SUPPRESSED',
    'VIP_EXISTING_LIST_SUPPRESSED',
})


@vip_bp.route('/signup', methods=['POST'])
def signup_for_vip_list():
    """Record explicit VIP consent without overriding an existing profile's state."""
    data = read_form_payload()
    if data is None:
        return jsonify({'error': 'El formulario no es válido.', 'code': 'invalid_payload'}), 400
    # Fixed hook for funnel integration: HONEYPOT_OUTCOME = honeypot_discarded.
    # This branch must remain before IP observation, consent DB and all providers.
    if honeypot_filled(data):
        return silent_success()
    if phone_is_rejected(data.get('phone')):
        return jsonify({'error': 'El teléfono de diez dígitos necesita prefijo internacional.', 'code': 'invalid_phone'}), 400

    email = normalize_email_address(data.get('email'))
    first_name = str(data.get('first_name') or '').strip()[:120]
    marketing_consent = data.get('marketing_consent') is True

    if not email:
        return jsonify({'error': 'Introduce un email válido.'}), 400
    if not marketing_consent:
        return jsonify({'error': 'Debes aceptar recibir comunicaciones comerciales para entrar en la lista VIP.'}), 400

    consented_at = datetime.utcnow()
    observed_ip, ip_provenance = consent_ip_evidence()
    signup = VipSignup(
        email=email,
        first_name=first_name or None,
        consent_marketing=True,
        consented_at=consented_at,
        source_ip=observed_ip,
        source=ip_provenance,
    )
    db.session.add(signup)
    db.session.commit()

    subscribed, subscription_error = klaviyo_subscribe_vip_contact(
        email=email,
        first_name=first_name,
        consented_at=consented_at,
        source_ip=UNVERIFIED_IP,
    )
    if subscribed:
        signup.klaviyo_subscription_status = 'accepted'
    elif subscription_error in _VIP_INELIGIBLE_ERRORS:
        signup.klaviyo_subscription_status = 'ineligible'
    else:
        signup.klaviyo_subscription_status = 'failed'

    if not subscribed:
        signup.klaviyo_event_status = 'not_sent'
        signup.klaviyo_error = subscription_error or 'VIP_SIGNUP_FAILED'
        db.session.commit()
        # Same neutral response for policy rejections and technical failures.
        # Never expose whether an address exists, has consent or is suppressed.
        return jsonify({'error': 'No se ha podido completar el alta. Inténtalo de nuevo más tarde.'}), 502

    event_sent, event_error = klaviyo_track_vip_signup(
        email=email,
        first_name=first_name,
        consented_at=consented_at,
        source_ip=UNVERIFIED_IP,
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
