"""Black Friday VIP signup: explicit consent; no newsletter/coupon flow."""
from flask import Blueprint, jsonify

from src.models.user import db
from src.models.vip_signup import VipSignup
from src.services.form_security import (
    FormSecurityError, claim_attempt, client_ip, finish_attempt, honeypot_filled,
    normalize_form_email, phone_is_rejected, read_form_payload, silent_success,
)
from src.services.klaviyo_service import (
    klaviyo_subscribe_vip_contact, klaviyo_track_vip_signup,
)

vip_bp = Blueprint('vip', __name__)


@vip_bp.route('/signup', methods=['POST'])
def signup_for_vip_list():
    """Preserve historic consent; one new VIP action per canonical identity."""
    claim = None
    try:
        data = read_form_payload()
        if honeypot_filled(data):
            return silent_success()
        ip = client_ip()
        email = normalize_form_email(data.get('email'))
        first_name = str(data.get('first_name') or '').strip()[:120]
        consent = data.get('marketing_consent') is True
        bad_phone = phone_is_rejected(data.get('phone'))
        claim = claim_attempt('vip', ip, email, eligible=bool(email and consent and not bad_phone))
        if not email:
            return jsonify({'error': 'Introduce un email válido.'}), 400
        if not consent:
            return jsonify({'error': 'Debes aceptar recibir comunicaciones comerciales para entrar en la lista VIP.'}), 400
        if bad_phone:
            return FormSecurityError('invalid_phone', 400, 0).response()
        if claim.duplicate:
            return silent_success()

        signup = VipSignup(
            email=email, first_name=first_name or None, consent_marketing=True,
            consented_at=claim.attempted_at, source_ip=ip,
        )
        db.session.add(signup)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            finish_attempt(claim.attempt_id, 'vip_failed')
            raise FormSecurityError('form_unavailable') from None

        subscribed, subscription_error = klaviyo_subscribe_vip_contact(
            email=email, first_name=first_name, consented_at=claim.attempted_at, source_ip=ip,
        )
        signup.klaviyo_subscription_status = 'accepted' if subscribed else 'failed'
        if not subscribed:
            signup.klaviyo_event_status = 'not_sent'
            signup.klaviyo_error = subscription_error or 'Klaviyo rechazó la suscripción VIP.'
            db.session.commit()
            finish_attempt(claim.attempt_id, 'vip_failed')
            return jsonify({'error': 'No se ha podido completar el alta. Inténtalo de nuevo más tarde.'}), 502

        sent, error = klaviyo_track_vip_signup(
            email=email, first_name=first_name, consented_at=claim.attempted_at,
            source_ip=ip, signup_id=signup.id,
        )
        signup.klaviyo_event_status = 'accepted' if sent else 'failed'
        signup.klaviyo_error = error if not sent else None
        db.session.commit()
        finish_attempt(claim.attempt_id, 'vip_accepted' if sent else 'vip_failed')
        if not sent:
            return jsonify({'error': 'No se ha podido completar el alta. Inténtalo de nuevo más tarde.'}), 502
        return jsonify({'success': True, 'signup_id': signup.id, 'message': 'Alta VIP registrada.'}), 201
    except FormSecurityError as error:
        return error.response()
    except Exception:
        db.session.rollback()
        if claim is not None:
            try:
                finish_attempt(claim.attempt_id, 'vip_failed')
            except FormSecurityError:
                pass
        return FormSecurityError('form_unavailable').response()
