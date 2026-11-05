"""Black Friday VIP signup with current-consent protection and aggregate telemetry.

The public response never reveals a remote profile's consent or suppression.
Existing Klaviyo profiles are linked only; their subscription is never renewed.
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
from src.services.vip_funnel import mark_vip_outcome
from src.services.vip_membership_verification import read_vip_membership
from src.services.vip_conversion_confirmation import prepare_vip_conversion
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
    """Record explicit consent without overriding an existing profile's state."""
    data = read_form_payload()
    if data is None:
        mark_vip_outcome('validation_rejected')
        return jsonify({'error': 'El formulario no es válido.', 'code': 'invalid_payload'}), 400
    # No IP, payload, profile, event, email or consent row for a trapped request.
    # Aggregate-only observation is independent of consent/business transactions.
    if honeypot_filled(data):
        mark_vip_outcome('honeypot')
        return silent_success()
    if phone_is_rejected(data.get('phone')):
        mark_vip_outcome('validation_rejected')
        return jsonify({'error': 'El teléfono de diez dígitos necesita prefijo internacional.', 'code': 'invalid_phone'}), 400

    email = normalize_email_address(data.get('email'))
    first_name = str(data.get('first_name') or '').strip()[:120]
    marketing_consent = data.get('marketing_consent') is True

    if not email:
        mark_vip_outcome('validation_rejected')
        return jsonify({'error': 'Introduce un email válido.'}), 400
    if not marketing_consent:
        mark_vip_outcome('validation_rejected')
        return jsonify({'error': 'Debes aceptar recibir comunicaciones comerciales para entrar en la lista VIP.'}), 400

    # GET-only analytics evidence. Unknown/failed reads NEVER prevent normal
    # signup, and no subscription helper or existing consent state is changed.
    try:
        before_membership = read_vip_membership(email)
    except Exception:
        before_membership = None  # Optional analytics must never break signup.
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
        mark_vip_outcome('ineligible' if signup.klaviyo_subscription_status == 'ineligible' else 'technical_failure')
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
        mark_vip_outcome('technical_failure')
        return jsonify({'error': 'No se ha podido completar el alta. Inténtalo de nuevo más tarde.'}), 502

    mark_vip_outcome('successful_signup')
    response = {
        'success': True,
        'signup_id': signup.id,
        'message': 'Alta VIP registrada.',
    }
    # Optional, isolated analytics: a 202 subscribe job is not list membership.
    try:
        conversion = prepare_vip_conversion(signup.id, before_membership)
    except Exception:
        conversion = None  # No raw error/PII; business success remains intact.
    if conversion:
        response['conversion'] = conversion
    return jsonify(response), 201
