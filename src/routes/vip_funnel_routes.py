"""Private daily funnel, read-only and aggregate-only. No marketing calls."""
from datetime import date, datetime, timedelta, timezone
from functools import wraps
import re

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text

from src.models.user import db
from src.routes.auth_routes import admin_required
from src.services.vip_funnel import MADRID, record_aggregate

vip_funnel_bp = Blueprint('vip_funnel', __name__)
MAX_DAYS = 93
LIVE_METRICS = ('pageviews', 'submissions', 'successful_signup', 'ineligible',
                'technical_failure', 'validation_rejected', 'honeypot',
                'rate_limited', 'unknown_outcome', 'telemetry_failure')


def safe_aggregate_errors(view):
    """Include auth lookups in the boundary; never use the global raw-error handler."""
    @wraps(view)
    def safe_view(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Exception:
            return jsonify({'error': 'Agregados no disponibles', 'health': 'unavailable', 'daily': None}), 503
    return safe_view


def validated_range(args, today=None):
    today = today or datetime.now(timezone.utc).astimezone(MADRID).date()
    if set(args) - {'start', 'end'} or any(len(args.getlist(key)) != 1 for key in args):
        raise ValueError('Rango inválido')
    start_value = args.get('start', (today - timedelta(days=6)).isoformat())
    end_value = args.get('end', today.isoformat())
    if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) for value in (start_value, end_value)):
        raise ValueError('Fechas inválidas')
    start, end = date.fromisoformat(start_value), date.fromisoformat(end_value)
    if start < date(2020, 1, 1) or end > today or end < start or (end - start).days >= MAX_DAYS:
        raise ValueError('Rango inválido')
    return start, end


def utc_bounds(start, end):
    """Local midnights, not fixed 24-hour offsets: Madrid DST can be 23/25h."""
    return (datetime.combine(start, datetime.min.time(), MADRID).astimezone(timezone.utc),
            datetime.combine(end + timedelta(days=1), datetime.min.time(), MADRID).astimezone(timezone.utc))


@vip_funnel_bp.after_request
def private_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Vary'] = 'Authorization'
    return response


@vip_funnel_bp.route('/api/vip/pageview', methods=['POST'])
def vip_pageview():
    # Intentionally only a write-only empty POST; no arbitrary event/body storage.
    # The administrative GET below is always authenticated.
    if request.args or request.content_length not in (None, 0):
        return jsonify({'error': 'Petición inválida'}), 400
    healthy = record_aggregate({'pageviews': 1})
    return jsonify({'recorded': healthy}), 202 if healthy else 503


HISTORICAL_SQL = text("""
    SELECT ((created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/Madrid')::date AS day,
           count(*) AS persisted_submissions,
           count(*) FILTER (WHERE klaviyo_subscription_status = 'accepted'
               AND klaviyo_event_status = 'accepted') AS successful_signup,
           count(*) FILTER (WHERE klaviyo_subscription_status = 'ineligible') AS ineligible,
           count(*) FILTER (WHERE klaviyo_subscription_status = 'failed'
               OR (klaviyo_subscription_status = 'accepted' AND klaviyo_event_status = 'failed'))
               AS technical_failure,
           count(*) FILTER (WHERE NOT (
               (klaviyo_subscription_status = 'accepted' AND klaviyo_event_status = 'accepted')
               OR klaviyo_subscription_status = 'ineligible'
               OR klaviyo_subscription_status = 'failed'
               OR (klaviyo_subscription_status = 'accepted' AND klaviyo_event_status = 'failed')))
               AS unknown_outcome
    FROM vip_signups
    WHERE source IN ('vip_black_friday_2026',
        'vip_black_friday_2026:ip_unverified:x_real_ip',
        'vip_black_friday_2026:ip_unverified:unavailable')
      AND created_at >= :start_utc AND created_at < :end_utc
      AND created_at < :cutoff_utc
    GROUP BY 1 ORDER BY 1
""")


def daily_payload(start, end, coverage_start, client_start, rows, historical, app_config):
    live_by_day = {row['day']: row for row in rows}
    history_by_day = {row['day']: row for row in historical}
    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        live_row = live_by_day.get(day)
        live = {key: (int(live_row[key]) if live_row else None) for key in LIVE_METRICS}
        if client_start is None or day < client_start.astimezone(MADRID).date():
            live['pageviews'] = None
        if coverage_start is None or day < coverage_start.astimezone(MADRID).date():
            for key in LIVE_METRICS:
                if key not in ('pageviews', 'telemetry_failure'):
                    live[key] = None
        if not app_config.get('VIP_FUNNEL_HONEYPOT_ACTIVE', False) and not live.get('honeypot'):
            live['honeypot'] = None
        if not app_config.get('VIP_FUNNEL_RATE_LIMIT_ACTIVE', True) and not live.get('rate_limited'):
            live['rate_limited'] = None
        historical_row = history_by_day.get(day)
        history = None
        if coverage_start is None or day <= coverage_start.astimezone(MADRID).date():
            history = {key: int(historical_row[key]) if historical_row else 0 for key in
                       ('persisted_submissions', 'successful_signup', 'ineligible',
                        'technical_failure', 'unknown_outcome')}
        days.append({'date': day.isoformat(), 'live': live, 'historical_persisted': history,
                     'health': 'observed_lower_bound' if live_row else 'unknown'})
    return days


@vip_funnel_bp.route('/api/admin/vip/funnel', methods=['GET'])
@safe_aggregate_errors
@admin_required
def vip_funnel_daily():
    try:
        start, end = validated_range(request.args)
    except (ValueError, OverflowError):
        return jsonify({'error': 'Usa fechas YYYY-MM-DD, hasta hoy, inicio <= fin y máximo 93 días.'}), 400
    if db.engine.dialect.name != 'postgresql':
        return jsonify({'error': 'Agregados no disponibles', 'health': 'unavailable', 'daily': None}), 503
    start_utc, end_utc = utc_bounds(start, end)
    try:
        with db.engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout = '3000ms'"))
            metadata = connection.execute(text("""
                SELECT min(first_at) AS instrumentation_start,
                      min(first_submission_at) AS coverage_start,
                       min(first_pageview_at) AS client_start
                FROM vip_funnel_daily
            """)).mappings().one()
            coverage_start = metadata['coverage_start']
            rows = connection.execute(text(
                'SELECT * FROM vip_funnel_daily WHERE day >= :start AND day <= :end ORDER BY day'),
                {'start': start, 'end': end}).mappings().all()
            historical = connection.execute(HISTORICAL_SQL, {
                'start_utc': start_utc.replace(tzinfo=None),
                'end_utc': end_utc.replace(tzinfo=None),
                'cutoff_utc': (coverage_start or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None),
            }).mappings().all()
        return jsonify({
            'timezone': 'Europe/Madrid', 'start': start.isoformat(), 'end': end.isoformat(),
            'coverage_start': coverage_start.isoformat() if coverage_start else None,
            'instrumentation_start': metadata['instrumentation_start'].isoformat() if metadata['instrumentation_start'] else None,
            'pageviews_coverage_start': metadata['client_start'].isoformat() if metadata['client_start'] else None,
            'health': 'read_available_completeness_unknown' if metadata['instrumentation_start'] else 'not_started',
            'measurement': {
                'pageviews': 'POSTs registrados de /vip; no visitantes únicos ni visitas retrospectivas.',
                'submissions': 'Todos los POST recibidos por Flask en /api/vip/signup, incluidos rechazos tempranos.',
                'successful_signup': 'Suscripción accepted Y evento accepted persistidos; no basta un HTTP 2xx.',
                'unknown_outcome': 'Resultado no clasificado o pendiente/no registrado, no equivale a fallo confirmado.',
                'counts': 'Observaciones mínimas; telemetría best-effort. Un cero registrado no prueba ausencia de tráfico.',
                'missing_day': 'null: ningún agregado disponible, nunca cero inventado.',
                'historical': 'Sólo agregados de VipSignup con source VIP y created_at anterior a coverage_start; nunca sumados otra vez en live.',
                'coverage': 'Primer POST de cada stream registrado correctamente, no instante de despliegue. Primer día parcial; pérdidas no reconstruibles.',
            },
            'features_current': {
                'honeypot': bool(current_app.config.get('VIP_FUNNEL_HONEYPOT_ACTIVE', False)),
                'rate_limited': bool(current_app.config.get('VIP_FUNNEL_RATE_LIMIT_ACTIVE', True)),
            },
            'historical_unavailable_metrics': ['pageviews', 'all_submissions', 'validation_rejected', 'honeypot', 'rate_limited'],
            'daily': daily_payload(start, end, coverage_start, metadata['client_start'], rows, historical, current_app.config),
        }), 200
    except Exception:
        # Never return database details, bound values, credentials or audit rows.
        return jsonify({'error': 'Agregados no disponibles', 'health': 'unavailable', 'daily': None}), 503
