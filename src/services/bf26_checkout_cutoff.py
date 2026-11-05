"""BF26 purchase cutoff only: never activates catalogue, schedules or refunds.

A hosted Checkout expiry cannot be less than 30 minutes. The final window
therefore requires the application-native sweep in src.jobs.bf26_expiration.
It is not an atomic payment deadline; already completed payments are retained.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.services.campaign_packs import campaign_pack_for

BF26_LAST_VALID = datetime(2026, 11, 30, 23, 59, 59, tzinfo=ZoneInfo('Europe/Madrid'))
BF26_CLOSE_AT = datetime(2026, 12, 1, tzinfo=ZoneInfo('Europe/Madrid'))
BF26_CLOSE_TIMESTAMP = int(BF26_CLOSE_AT.timestamp())
STRIPE_MIN_TTL = 30 * 60
STRIPE_MAX_TTL = 24 * 60 * 60
# Avoid a timestamp on the minimum boundary arriving a few seconds too late.
# Larger network/clock skew can still fail safely; do not silently close early.
CREATION_MARGIN = 60
BF26_METADATA_KEY = 'purchase_campaign'
BF26_CLOSED_RESPONSE = {
    'error': 'BF26_CAMPAIGN_CLOSED',
    'message': 'La campaña Black Friday ha finalizado. Actualiza tu carrito.',
}


class BF26CampaignClosed(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def _timestamp(now):
    if now.tzinfo is None:
        raise ValueError('An aware server timestamp is required')
    return now.timestamp()


def is_closed(now=None):
    return _timestamp(now or utc_now()) >= BF26_CLOSE_TIMESTAMP


def contains_bf26(products):
    return any(campaign_pack_for(product) for product in products)


def checkout_policy(now=None):
    """Server-only policy; last second stays valid, no browser time accepted.

    Normally expire after 31 minutes or at cutoff, whichever is earlier.
    Inside the minimum-plus-network-margin window use 31 minutes and rely on
    the cutoff sweep. This deliberately preserves the whole last Monday.
    """
    now = now or utc_now()
    if is_closed(now):
        raise BF26CampaignClosed()
    timestamp = int(_timestamp(now))
    minimum_with_margin = timestamp + STRIPE_MIN_TTL + CREATION_MARGIN
    expires_at = min(minimum_with_margin, BF26_CLOSE_TIMESTAMP)
    requires_sweep = BF26_CLOSE_TIMESTAMP < minimum_with_margin
    if requires_sweep:
        expires_at = minimum_with_margin
    return {
        'expires_at': expires_at,
        'metadata': {
            BF26_METADATA_KEY: 'BF26',
            'purchase_campaign_closes_at': str(BF26_CLOSE_TIMESTAMP),
            'purchase_campaign_requires_sweep': str(requires_sweep).lower(),
        },
    }


def value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def expire_open_session(session_api, session_id):
    """Race-aware, repeatable expiration. Never cancels/refunds a payment.

    Exceptions are deliberately not logged or returned as raw provider text.
    The caller must retry failures via the catchup sweep.
    """
    try:
        # State transition itself is idempotent: a repeated expire cannot
        # charge/refund. Do not reuse a permanent request key across sweeps;
        # Stripe v1 caches 500 responses with that key for at least 24h.
        result = session_api.expire(session_id)
        state = value(result, 'status')
        return state if state in ('expired', 'complete') else 'retry'
    except Exception:
        try:
            state = value(session_api.retrieve(session_id), 'status')
            return state if state in ('expired', 'complete') else 'retry'
        except Exception:
            return 'retry'
