"""Explicit BF26 cutoff sweep; importing this module performs no work.

The application owner must orchestrate run_once at/after cutoff and repeat on
retry/failure. There is intentionally no daemon, cron registration or web route.
No catalogue, physical stock, ERP, refund or consent operation exists here.
"""
from datetime import datetime

from src.services.bf26_checkout_cutoff import (
    BF26_CLOSE_TIMESTAMP, BF26_METADATA_KEY, STRIPE_MAX_TTL,
    expire_open_session, is_closed, utc_now, value,
)


def run_once(session_api, *, legacy_session_ids=(), now=None, max_pages=100):
    """Catch up marked Stripe sessions plus trusted legacy local BF identities.

    Expired/complete sessions make reruns harmless. A crash needs no checkpoint:
    the next run discovers remaining open sessions again. Page/failure flags
    require another run; output is aggregate counts only (no IDs, PII or errors).
    Stripe Sessions live at most 24h, so an open-session scan need not traverse
    all campaign history. No key is loaded or provider called before cutoff.
    """
    now = now or utc_now()
    counts = {'before_cutoff': False, 'matched': 0, 'expired': 0,
              'complete': 0, 'retry': 0, 'scan_failed': False,
              'page_limit_reached': False}
    if not is_closed(now):
        counts['before_cutoff'] = True
        return counts
    if max_pages < 1:
        raise ValueError('max_pages must be positive')
    seen = set()

    def expire(session_id):
        if not session_id or session_id in seen:
            return
        seen.add(session_id)
        counts['matched'] += 1
        counts[expire_open_session(session_api, session_id)] += 1

    # Trusted local linkage also covers BF Sessions created by older code
    # before technical campaign metadata was added. Never infer from a title.
    for session_id in legacy_session_ids:
        expire(session_id)

    # Cover sessions that crossed cutoff in creation or lost the local bind.
    # Do not bound upper date at cutoff: an in-flight API call may create later.
    cursor = None
    for _ in range(max_pages):
        params = {'status': 'open', 'limit': 100,
                  'created': {'gte': max(BF26_CLOSE_TIMESTAMP - STRIPE_MAX_TTL,
                                        int(now.timestamp()) - STRIPE_MAX_TTL)}}
        if cursor:
            params['starting_after'] = cursor
        try:
            page = session_api.list(**params)
        except Exception:
            counts['scan_failed'] = True
            return counts
        data = value(page, 'data', []) or []
        for session in data:
            metadata = value(session, 'metadata', {}) or {}
            if (value(session, 'mode') == 'payment'
                    and metadata.get(BF26_METADATA_KEY) == 'BF26'):
                expire(value(session, 'id'))
        if not value(page, 'has_more', False):
            return counts
        if not data:
            counts['scan_failed'] = True
            return counts
        cursor = value(data[-1], 'id')
        if not cursor:
            counts['scan_failed'] = True
            return counts
    counts['page_limit_reached'] = True
    return counts


def local_bf26_session_ids():
    """Read linkage only, including expired/released local allocations.

    A stale local reservation does NOT prove Stripe's session is expired.
    No reservation/snapshot is removed, so late paid webhooks remain usable.
    Requires an already configured application context; no DB init at import.
    """
    from src.models.order import Order  # Register the reservation relationship target.
    from src.models.stock import StockReservation
    from src.models.web_product import WebProduct
    from src.services.campaign_packs import CAMPAIGN_PACKS_BY_SKU
    return [row[0] for row in StockReservation.query.with_entities(
        StockReservation.stripe_session_id,
    ).join(WebProduct, StockReservation.product_id == WebProduct.id).filter(
        WebProduct.sku.in_(tuple(CAMPAIGN_PACKS_BY_SKU)),
        StockReservation.created_at >= datetime.utcfromtimestamp(
            BF26_CLOSE_TIMESTAMP - STRIPE_MAX_TTL,
        ),
        StockReservation.stripe_session_id.isnot(None),
        StockReservation.stripe_session_id != '',
    ).distinct().all()]


def main():
    """One explicit invocation; no import of main.py or auto-migration/seed."""
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description='BF26 cutoff sweep — explicit operator action')
    parser.add_argument('--execute', action='store_true',
                        help='Explicit approval to expire open BF26 sessions after cutoff')
    parser.add_argument('--max-pages', type=int, default=100)
    args = parser.parse_args()
    if not args.execute:
        parser.error('No action: --execute requires operator approval')
    if not is_closed():
        print(json.dumps({'before_cutoff': True, 'provider_calls': 0}))
        return 0
    from flask import Flask
    import stripe
    from src.models.user import db

    database_url = os.getenv('DATABASE_URL', '')
    stripe_key = os.getenv('STRIPE_SECRET_KEY', '')
    if not database_url or not stripe_key:
        print(json.dumps({'configuration_missing': True}))
        return 2
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app = Flask('bf26-cutoff-job')
    app.config.update(SQLALCHEMY_DATABASE_URI=database_url,
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    stripe.api_key = stripe_key
    with app.app_context():
        try:
            legacy_ids = local_bf26_session_ids()
            counts = run_once(stripe.checkout.Session, legacy_session_ids=legacy_ids,
                              max_pages=args.max_pages)
        except Exception:
            # Fail safe: never dump raw DB/provider exceptions or identities.
            print(json.dumps({'job_failed': True, 'retry_required': True}))
            return 2
    print(json.dumps(counts, sort_keys=True))
    return 2 if (counts['retry'] or counts['scan_failed']
                 or counts['page_limit_reached']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
