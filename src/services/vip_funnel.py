"""Best-effort PostgreSQL aggregate telemetry, isolated from signup transactions.

No local counter store, identities or retry queue. If storage cannot observe an
attempt, its count cannot be reconstructed: all numbers remain lower bounds.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import g, request
from sqlalchemy import text

from src.models.user import db

MADRID = ZoneInfo('Europe/Madrid')
OUTCOMES = frozenset({
    'successful_signup', 'ineligible', 'technical_failure',
    'validation_rejected', 'honeypot', 'rate_limited',
})
COUNTERS = OUTCOMES | {'pageviews', 'submissions', 'unknown_outcome', 'telemetry_failure'}


def increment_sql(deltas):
    """Whitelist SQL identifiers; one UPSERT serializes each day's increments."""
    if not deltas or not set(deltas) <= COUNTERS:
        raise ValueError('Invalid aggregate counters')
    names = sorted(deltas)
    columns = ', '.join(names)
    values = ', '.join(':' + name for name in names)
    updates = ', '.join(f'{name} = vip_funnel_daily.{name} + :{name}' for name in names)
    # Result-only UPDATE must not insert a negative unknown_outcome if the
    # first-write row disappeared. The predicate also avoids negative counts.
    if deltas.get('unknown_outcome', 0) < 0:
        return text(f"UPDATE vip_funnel_daily SET {updates}, "
                    "last_at = GREATEST(last_at, :at) "
                    "WHERE day = :day AND unknown_outcome > 0")
    streams = [name for name in ('pageviews', 'submissions') if deltas.get(name, 0) > 0]
    stream_columns = ''.join(', first_' + ('pageview' if name == 'pageviews' else 'submission') + '_at' for name in streams)
    stream_values = ', :at' * len(streams)
    stream_updates = ''.join(', first_' + ('pageview' if name == 'pageviews' else 'submission') +
        '_at = LEAST(vip_funnel_daily.first_' + ('pageview' if name == 'pageviews' else 'submission') + '_at, :at)' for name in streams)
    return text(f"INSERT INTO vip_funnel_daily (day, first_at, last_at, {columns}{stream_columns}) "
                f"VALUES (:day, :at, :at, {values}{stream_values}) ON CONFLICT (day) DO UPDATE SET "
                f"{updates}{stream_updates}, first_at = LEAST(vip_funnel_daily.first_at, :at), "
                "last_at = GREATEST(vip_funnel_daily.last_at, :at)")


def record_aggregate(deltas, at=None):
    """Separate connection: never commit/rollback the business db.session.

    No exception strings are logged (database errors can contain credentials or
    bind values). Returns storage health only; never blocks a successful signup.
    """
    at = at or datetime.now(timezone.utc)
    try:
        if db.engine.dialect.name != 'postgresql':
            return False
        parameters = {'day': at.astimezone(MADRID).date(), 'at': at, **deltas}
        with db.engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '1000ms'"))
            connection.execute(text("SET LOCAL lock_timeout = '250ms'"))
            result = connection.execute(increment_sql(deltas), parameters)
            return result.rowcount == 1
    except Exception:
        return False


def mark_vip_outcome(outcome):
    """Contract for early rejection and explicit persisted success hooks."""
    if outcome not in OUTCOMES:
        raise ValueError('Invalid VIP outcome')
    g.vip_funnel_outcome = outcome


def install_vip_funnel(app):
    """Register before application validation hooks; count all matching POSTs."""
    app.config.setdefault('VIP_FUNNEL_RATE_LIMIT_ACTIVE', True)  # existing route limiter
    app.config.setdefault('VIP_FUNNEL_HONEYPOT_ACTIVE', False)

    @app.before_request
    def begin_vip_submission():
        if request.method == 'POST' and request.path.rstrip('/') == '/api/vip/signup':
            g.vip_funnel_started_at = datetime.now(timezone.utc)
            g.vip_funnel_submission_recorded = record_aggregate(
                {'submissions': 1, 'unknown_outcome': 1}, g.vip_funnel_started_at)

    @app.after_request
    def finish_vip_submission(response):
        at = getattr(g, 'vip_funnel_started_at', None)
        if at is None:
            return response
        outcome = getattr(g, 'vip_funnel_outcome', None)
        if outcome is None:
            if response.status_code == 429:
                outcome = 'rate_limited'
            elif response.status_code in {400, 413, 415, 422}:
                outcome = 'validation_rejected'
            elif response.status_code >= 500:
                outcome = 'technical_failure'
            # Never infer success or ineligible from a public status or body.
        started = getattr(g, 'vip_funnel_submission_recorded', False)
        if outcome:
            deltas = {outcome: 1}
            if started:
                deltas['unknown_outcome'] = -1
            else:
                deltas.update(submissions=1, telemetry_failure=1)
            finished = record_aggregate(deltas, at)
        elif not started:
            finished = record_aggregate({'submissions': 1, 'unknown_outcome': 1,
                                         'telemetry_failure': 1}, at)
        else:
            finished = True  # An unclassified response is deliberately unknown.
        if not finished:
            # Best effort diagnostic, not a promise to reconstruct lost traffic.
            record_aggregate({'telemetry_failure': 1}, at)
        return response
