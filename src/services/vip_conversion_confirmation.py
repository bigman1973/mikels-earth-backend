"""Signed candidate for a new VIP entry; live eligible GET alone confirms it.

Analytics transactions use a separate engine connection, never db.session.
PostgreSQL locks a row while checking/claiming; a committed CAS claim also
serializes SQLite. A claim is persisted BEFORE HTTP (crash => sending, no
recovery automation). At most two explicit attempts share event ID AND event
time; never background retries, backfill, or resubscription.
"""
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from flask import current_app
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select, update, insert, text

from src.models.user import db
from src.models.vip_signup import VipSignup
from src.models.vip_conversion import vip_signup_conversions as conversions
from src.services.vip_membership_verification import read_vip_membership
from src.services.meta_vip_conversions_api import (
    EVENT_NAME, dispatch_meta_vip_event, valid_user_agent,
)

CAMPAIGN = 'vip_black_friday_2026'
PROOF_TTL_SECONDS = 1800
MAX_CAPI_ATTEMPTS = 2
TRANSIENT_REASONS = frozenset({'meta_timeout', 'meta_request_failed'})
SAFE_REASONS = TRANSIENT_REASONS | frozenset({
    'accepted', 'meta_not_configured', 'meta_invalid_configuration',
    'meta_http_failed', 'meta_receipt_unverified', 'invalid_event',
    'invalid_identity', 'user_agent_required', 'marketing_not_granted',
})
NEUTRAL = {'confirmed': False}
PENDING = {'confirmed': False, 'pending': True}


def _now():
    return datetime.utcnow()


def _serializer():
    secret = current_app.config.get('SECRET_KEY')
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError('Proof signing unavailable')
    return URLSafeTimedSerializer(secret, salt='vip-confirmed-meta-v1',
                                  signer_kwargs={'digest_method': sha256})


def _email_hash(email):
    return sha256(email.strip().lower().encode('utf-8')).hexdigest()


def _timeouts(connection):
    if connection.dialect.name == 'postgresql':
        connection.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        connection.execute(text("SET LOCAL lock_timeout = '300ms'"))


def _accepted_signup(connection, signup_id):
    signup = connection.execute(select(VipSignup.__table__).where(
        VipSignup.__table__.c.id == signup_id)).mappings().first()
    if (not signup or signup['consent_marketing'] is not True
            or signup['klaviyo_subscription_status'] != 'accepted'
            or signup['klaviyo_event_status'] != 'accepted'
            or not signup['source'].startswith(CAMPAIGN)):
        return None
    return signup


def prepare_vip_conversion(signup_id, before):
    """Optional signed candidate: prior absence + local accepted signup/event.

    Subscribe 202 is NOT membership. Absent/unknown post-GET still permits a
    candidate with NULL profile/post evidence, never a confirmed conversion.
    An explicit ineligible/mismatched post-GET disqualifies it. Real eligible
    post evidence, if already available, binds the profile for later rechecks.
    Missing table/signing and analytics failures never change business success.
    """
    try:
        if before.state != 'absent' or not before.list_id or not before.checked_at:
            return None
        serializer = _serializer()
        with db.engine.connect() as connection:
            signup = _accepted_signup(connection, signup_id)
        if not signup or before.checked_at > signup['created_at']:
            return None
        after = read_vip_membership(signup['email'])
        if (after.state not in ('member', 'absent', 'unknown')
                or (after.list_id is not None and after.list_id != before.list_id)):
            return None
        profile_id = post_checked_at = None
        if after.state == 'member':
            if (after.list_id != before.list_id or not after.profile_id
                    or not after.checked_at or after.checked_at < before.checked_at):
                return None
            profile_id, post_checked_at = after.profile_id, after.checked_at
        created_at = _now()
        conversion_id, event_id = str(uuid4()), str(uuid4())
        proof = serializer.dumps({'conversion_id': conversion_id,
                                  'signup_id': signup_id, 'event_id': event_id,
                                  'campaign': CAMPAIGN})
        with db.engine.begin() as connection:
            _timeouts(connection)
            # Recheck persisted success; neither HTTP 201 nor Subscribe 202 is proof.
            accepted = _accepted_signup(connection, signup_id)
            if not accepted or _email_hash(accepted['email']) != _email_hash(signup['email']):
                return None
            connection.execute(insert(conversions).values(
                id=conversion_id, campaign=CAMPAIGN,
                email_hash=_email_hash(signup['email']), signup_id=signup_id,
                event_id=event_id, list_id=before.list_id, profile_id=profile_id,
                pre_checked_at=before.checked_at, post_checked_at=post_checked_at,
                created_at=created_at,
                expires_at=created_at + timedelta(seconds=PROOF_TTL_SECONDS),
                capi_state='pending', capi_attempts=0,
            ))
        return {'token': proof, 'event_id': event_id, 'event_name': EVENT_NAME}
    except Exception:
        return None


def _verified_proof(token):
    if not isinstance(token, str) or not token or len(token) > 2048:
        return None
    data = _serializer().loads(token, max_age=PROOF_TTL_SECONDS)
    if (not isinstance(data, dict)
            or set(data) != {'conversion_id', 'signup_id', 'event_id', 'campaign'}
            or data['campaign'] != CAMPAIGN or type(data['signup_id']) is not int
            or data['signup_id'] <= 0
            or not all(isinstance(data[key], str) and len(data[key]) == 36
                       for key in ('conversion_id', 'event_id'))):
        return None
    return data


def _confirmed(event_id, sent):
    return {'confirmed': True, 'event_id': event_id,
            'event_name': EVENT_NAME, 'capi_sent': sent is True}


def confirm_vip_conversion(token, *, marketing_consent, user_agent):
    """Generic dictionary status; only actual eligible membership confirms.

    Absent/unknown live GET => pending False/202 via the route. Ineligible,
    mismatched or invalid proof => terminal False/200. No consent/PII reason.
    Replays revalidate membership, consent and local accepted states as well.
    """
    if marketing_consent is not True or not valid_user_agent(user_agent):
        return dict(NEUTRAL)
    try:
        proof = _verified_proof(token)
        if not proof:
            return dict(NEUTRAL)
        with db.engine.begin() as connection:
            _timeouts(connection)
            query = select(conversions).where(conversions.c.id == proof['conversion_id'])
            if connection.dialect.name == 'postgresql':
                query = query.with_for_update()
            row = connection.execute(query).mappings().first()
            now = _now()
            if (not row or row['campaign'] != CAMPAIGN
                    or row['signup_id'] != proof['signup_id']
                    or row['event_id'] != proof['event_id'] or row['expires_at'] <= now
                    or (row['profile_id'] is None) != (row['post_checked_at'] is None)
                    or (row['post_checked_at'] is not None
                        and row['pre_checked_at'] > row['post_checked_at'])
                    or (row['confirmed_at'] is not None and row['post_checked_at'] is None)):
                return dict(NEUTRAL)
            signup = _accepted_signup(connection, row['signup_id'])
            if (not signup or _email_hash(signup['email']) != row['email_hash']
                    or row['pre_checked_at'] > signup['created_at']):
                return dict(NEUTRAL)
            try:
                membership = read_vip_membership(signup['email'])
            except Exception:
                # A failed GET is indeterminate, not membership or consent evidence.
                return dict(PENDING)
            if ((membership.list_id is not None and membership.list_id != row['list_id'])
                    or (row['profile_id'] is not None and membership.profile_id is not None
                        and membership.profile_id != row['profile_id'])):
                return dict(NEUTRAL)
            if membership.state in ('absent', 'unknown'):
                return dict(PENDING)
            if (membership.state != 'member' or membership.list_id != row['list_id']
                    or not membership.profile_id or not membership.checked_at
                    or membership.checked_at < row['pre_checked_at']):
                return dict(NEUTRAL)
            # Only now can missing candidate evidence be filled from an actual GET.
            confirmed_at = row['confirmed_at'] or _now()
            evidence = dict(profile_id=membership.profile_id,
                            post_checked_at=row['post_checked_at'] or membership.checked_at,
                            confirmed_at=confirmed_at, last_checked_at=membership.checked_at)
            # Sent replays use the SAME browser identifier; CAPI is never repeated.
            if row['capi_state'] == 'sent':
                connection.execute(update(conversions).where(
                    conversions.c.id == row['id']).values(**evidence))
                return _confirmed(row['event_id'], True)
            can_claim = (row['capi_state'] == 'pending' or
                         (row['capi_state'] in ('failed', 'unknown')
                          and row['capi_reason'] in TRANSIENT_REASONS))
            if not can_claim or row['capi_attempts'] >= MAX_CAPI_ATTEMPTS:
                connection.execute(update(conversions).where(
                    conversions.c.id == row['id']).values(**evidence))
                return _confirmed(row['event_id'], False)
            claim = connection.execute(update(conversions).where(
                conversions.c.id == row['id'],
                conversions.c.capi_state == row['capi_state'],
                conversions.c.capi_attempts == row['capi_attempts'],
            ).values(**evidence, capi_state='sending', capi_attempts=row['capi_attempts'] + 1,
                     capi_attempted_at=_now(), capi_reason=None))
            if claim.rowcount != 1:
                return dict(NEUTRAL)
        # Claim and actual membership COMMITTED before send; no rolled-back HTTP.
        try:
            delivery = dispatch_meta_vip_event(
                event_id=row['event_id'], email_hash=row['email_hash'],
                user_agent=user_agent, event_time=confirmed_at, marketing_consent=True)
        except Exception:
            delivery = {'sent': False, 'reason': 'meta_request_failed'}
        sent = isinstance(delivery, dict) and delivery.get('sent') is True
        reason = delivery.get('reason') if isinstance(delivery, dict) else None
        reason = reason if reason in SAFE_REASONS else 'meta_receipt_unverified'
        state = 'sent' if sent else ('unknown' if reason in
                ('meta_timeout', 'meta_receipt_unverified', 'meta_request_failed') else 'failed')
        with db.engine.begin() as connection:
            _timeouts(connection)
            updated = connection.execute(update(conversions).where(
                conversions.c.id == row['id'], conversions.c.capi_state == 'sending',
                conversions.c.capi_attempts == row['capi_attempts'] + 1,
            ).values(capi_state=state, capi_reason=reason,
                     capi_sent_at=_now() if sent else None))
            if updated.rowcount != 1:
                return dict(NEUTRAL)
        return _confirmed(row['event_id'], sent)
    except Exception:
        # Never expose profile consent, database errors, token, email or keys.
        return dict(NEUTRAL)
