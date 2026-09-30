# PostgreSQL coupons table ready - 2025-12-01 22:45
from flask import Blueprint, request, jsonify
from src.services.email_dispatcher import dispatch_newsletter_subscription_notification, dispatch_newsletter_welcome, dispatch_add_contact
from src.models.coupon import Coupon
from src.models.newsletter_consent import NewsletterConsent
from src.models.newsletter_subscriber import NewsletterSubscriber
from src.models.user import db
from src.services.email_identity import normalize_email_address, newsletter_email_key
from src.services.turnstile_service import verify_turnstile
from datetime import datetime
import re
import time
from collections import defaultdict

newsletter_bp = Blueprint('newsletter', __name__)

# Anti-spam
_newsletter_rate_store = defaultdict(list)


def _is_gibberish(text):
    if not text or len(text) < 4:
        return False
    clean = re.sub(r'[\s\-\'\.]', '', text.lower())
    if re.findall(r'[bcdfghjklmnpqrstvwxyz]{5,}', clean):
        return True
    if len(clean) > 6:
        vowels = sum(1 for c in clean if c in 'aeiou\u00e1\u00e9\u00ed\u00f3\u00fa\u00e0\u00e8\u00ec\u00f2\u00f9')
        if vowels / len(clean) < 0.15:
            return True
    if len(text) > 6:
        upper_count = sum(1 for c in text[1:] if c.isupper())
        if upper_count > len(text) * 0.35:
            return True
    return False


def _is_newsletter_rate_limited(ip):
    now = time.time()
    _newsletter_rate_store[ip] = [t for t in _newsletter_rate_store[ip] if now - t < 3600]
    if len(_newsletter_rate_store[ip]) >= 5:
        return True
    _newsletter_rate_store[ip].append(now)
    return False


@newsletter_bp.route('/subscribe', methods=['POST'])
def subscribe_newsletter():
    """
    Subscribe to the newsletter while issuing one welcome coupon per identity.

    Every popup submission is retained as a consent-history row. Coupon issuance
    is reserved by a canonical identity so an exact repeat or a Gmail alias
    cannot create a second welcome coupon.
    """
    try:
        # Anti-spam: rate limiting
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip:
            client_ip = client_ip.split(',')[0].strip()
        if _is_newsletter_rate_limited(client_ip):
            print(f"⚠️ Newsletter rate limited: {client_ip}")
            return jsonify({
                'success': True,
                'already_subscribed': True,
                'message': 'Ya estás suscrito. Si no encuentras tu cupón, escríbenos.',
            }), 200

        data = request.get_json(silent=True) or {}
        email = normalize_email_address(data.get('email'))
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()
        phone = data.get('phone', '').strip()
        source = data.get('source', 'website')
        privacy_policy_accepted = data.get('privacy_policy_accepted')
        whatsapp_marketing_accepted = data.get('whatsapp_marketing_accepted') is True
        
        if not email:
            return jsonify({'error': 'Introduce un email válido.'}), 400

        if source == 'popup' and privacy_policy_accepted is not True:
            return jsonify({
                'error': 'Debes aceptar la política de privacidad para suscribirte.'
            }), 400

        # Anti-spam: gibberish name check
        if _is_gibberish(first_name) or _is_gibberish(last_name):
            print(f"🚫 Newsletter spam blocked (gibberish): {first_name} {last_name} / {email} / IP={client_ip}")
            return jsonify({'success': True, 'message': 'Subscription successful'}), 200

        turnstile_result = verify_turnstile(
            data.get('turnstile_token'),
            client_ip,
            expected_action='newsletter_signup',
        )
        if not turnstile_result.accepted:
            if turnstile_result.reason == 'not_configured':
                print('🚨 Newsletter signup blocked: Turnstile is not configured')
                return jsonify({'error': 'El formulario no está disponible temporalmente. Inténtalo más tarde.'}), 503
            print(f"🚫 Newsletter signup blocked by Turnstile ({turnstile_result.reason}): IP={client_ip}")
            return jsonify({'error': 'No se ha podido validar el envío. Recarga la página e inténtalo de nuevo.'}), 400

        if source == 'popup':
            consent_recorded_at = datetime.utcnow()
            consent = NewsletterConsent(
                email=email,
                first_name=first_name,
                last_name=last_name,
                phone=phone or None,
                source=source,
                privacy_policy_accepted=True,
                privacy_policy_recorded_at=consent_recorded_at,
                whatsapp_marketing_accepted=whatsapp_marketing_accepted,
                whatsapp_marketing_recorded_at=consent_recorded_at,
            )
            db.session.add(consent)
            db.session.commit()

        email_key = newsletter_email_key(email)
        subscriber = NewsletterSubscriber.query.filter_by(email_key=email_key).first()
        new_subscriber = subscriber is None

        if new_subscriber:
            # Do not let a historical welcome coupon be reissued just because
            # the subscriber table is new. Manual and post-purchase coupons are
            # deliberately excluded by their code prefixes.
            existing_welcome_coupon = next(
                (
                    coupon
                    for coupon in Coupon.query.filter(
                        Coupon.email.isnot(None),
                        Coupon.code.like('MIKELS-%'),
                    ).all()
                    if newsletter_email_key(coupon.email) == email_key
                ),
                None,
            )

            subscriber = NewsletterSubscriber(
                email_key=email_key,
                email=email,
                welcome_coupon=existing_welcome_coupon,
            )
            db.session.add(subscriber)
            try:
                db.session.commit()
            except Exception:
                # A concurrent request for the same canonical identity lost the
                # unique-key race. It must behave as an existing subscriber.
                db.session.rollback()
                subscriber = NewsletterSubscriber.query.filter_by(email_key=email_key).first()
                if not subscriber:
                    raise
                new_subscriber = False

            # A pre-existing welcome code proves that this identity already
            # consumed its one welcome entitlement, even though it is only now
            # being backfilled into newsletter_subscribers.
            if existing_welcome_coupon:
                new_subscriber = False

        coupon = subscriber.welcome_coupon
        if new_subscriber and coupon is None:
            try:
                coupon = Coupon.create_welcome_coupon(email)
                subscriber.welcome_coupon = coupon
                subscriber.email = email
                db.session.commit()
                print(f"Welcome coupon created for newsletter identity {email_key}")
            except Exception:
                db.session.rollback()
                # Do not reserve an identity if its welcome coupon was not
                # created. A later valid submission can safely retry.
                if subscriber.id:
                    db.session.delete(subscriber)
                    db.session.commit()
                raise

        # El teléfono solo se entrega al proveedor de marketing cuando existe
        # consentimiento específico para comunicaciones comerciales por WhatsApp.
        marketing_phone = phone if whatsapp_marketing_accepted else None

        # Añadir contacto a Klaviyo con nombre, apellidos y teléfono consentido.
        contact_result = dispatch_add_contact(
            email, 
            first_name=first_name, 
            last_name=last_name, 
            phone=marketing_phone,
            source=source,
            whatsapp_marketing_accepted=whatsapp_marketing_accepted,
            subscribe_email=new_subscriber,
        )

        if not new_subscriber:
            return jsonify({
                'success': True,
                'already_subscribed': True,
                'message': 'Ya estás suscrito. Si no encuentras tu cupón, escríbenos.',
                'contact_id': contact_result.get('id') if contact_result else None,
            }), 200

        # Only the first subscription causes a notification and welcome email.
        dispatch_newsletter_subscription_notification(
            email,
            coupon.code,
            first_name=first_name,
            last_name=last_name,
            phone=marketing_phone,
        )
        dispatch_newsletter_welcome(email, coupon.code)

        return jsonify({
            'success': True,
            'message': 'Subscription successful',
            'coupon_code': coupon.code,
            'contact_id': contact_result.get('id') if contact_result else None
        }), 200
        
    except Exception as e:
        print(f"Error in newsletter subscription: {str(e)}")
        return jsonify({'error': str(e)}), 500
