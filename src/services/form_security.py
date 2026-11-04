"""Keyless VIP/contact security. No I/O on import; no memory fallback.

Production uses the existing PostgreSQL database and an explicitly applied
migration. SQLite is allowed ONLY in explicit in-memory testing fixtures.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import ipaddress
import math
import os
import re

from flask import current_app, jsonify, request
from sqlalchemy import and_, case, func, select, text

from src.models.form_attempt import FormAttempt
from src.models.user import db
from src.models.vip_signup import VipSignup

WINDOW_SECONDS = 3600
MAX_REQUESTS = 3
MAX_BODY_BYTES = 16384
PG_LOCK_ID = 2026110403
HASH_PREFIX = b'mikels:vip-contact:ip:v1:'
HONEYPOT_FIELDS = ('website', '_hp')
GENERIC_SUCCESS = {'success': True, 'message': 'Solicitud recibida correctamente.'}


@dataclass
class FormSecurityError(Exception):
    code: str
    status: int = 503
    retry_after: int = 60

    def response(self):
        messages = {
            'rate_limited': 'Demasiados intentos. Inténtalo más tarde.',
            'invalid_payload': 'El formulario no es válido.',
            'invalid_phone': 'El teléfono de diez dígitos necesita prefijo internacional.',
        }
        response = jsonify({
            'error': messages.get(self.code, 'Formulario temporalmente no disponible. Inténtalo más tarde.'),
            'code': self.code,
        })
        response.status_code = self.status
        if self.retry_after:
            response.headers['Retry-After'] = str(self.retry_after)
        return response


@dataclass(frozen=True)
class AttemptClaim:
    attempt_id: int
    duplicate: bool
    attempted_at: datetime


def configure_form_security(app, environ=None):
    environ = os.environ if environ is None else environ
    # No additional secret: use Flask's existing stable SECRET_KEY at runtime.
    app.config['FORM_CLIENT_IP_MODE'] = environ.get('FORM_CLIENT_IP_MODE', 'direct')
    app.config['FORM_TRUSTED_PROXY_CIDRS'] = environ.get('FORM_TRUSTED_PROXY_CIDRS', '')
    app.config['FORM_VERIFIED_IP_HEADER'] = environ.get('FORM_VERIFIED_IP_HEADER', '')


def read_form_payload():
    request.max_content_length = MAX_BODY_BYTES
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise FormSecurityError('invalid_payload', 400, 0)
    return data


def honeypot_filled(data):
    # Never log or persist trapped submissions, even when DB/IP config is absent.
    return any(data.get(field) not in (None, '') for field in HONEYPOT_FIELDS)


def silent_success():
    return jsonify(GENERIC_SUCCESS), 200


def normalize_form_email(value):
    """Preserve the delivery mailbox; do not strip embedded whitespace."""
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if len(email) > 255 or re.search(r'\s', email) or email.count('@') != 1:
        return None
    local, domain = email.rsplit('@', 1)
    if not local or not domain or '.' not in domain or local.startswith('.') or local.endswith('.') or '..' in local:
        return None
    if domain in ('gmail.com', 'googlemail.com') and not local.split('+', 1)[0].replace('.', ''):
        return None
    return email


def canonical_form_email(value):
    """Dedupe only: Gmail dots and documented plus aliases share one mailbox.

    The existing newsletter identity already uses these Gmail semantics. Do not
    generalize them to custom Workspace/other domains or delivery addresses.
    """
    email = normalize_form_email(value)
    if not email:
        return None
    local, domain = email.rsplit('@', 1)
    if domain in ('gmail.com', 'googlemail.com'):
        local = local.split('+', 1)[0].replace('.', '')
        domain = 'gmail.com'
    return f'{local}@{domain}'


def phone_is_rejected(value):
    if value in (None, ''):
        return False
    if not isinstance(value, str):
        return True
    compact = re.sub(r'[\s().-]', '', value)
    if compact.startswith(('+', '00')):
        return False
    return re.fullmatch(r'[0-9]{10}', compact) is not None


def _canonical_ip(value):
    if not isinstance(value, str) or '%' in value:
        raise ValueError('Invalid IP')
    address = ipaddress.ip_address(value.strip())
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def client_ip():
    """Trust socket peer only unless verified proxy configuration is approved.

    Never use client XFF's first entry. Verified-header mode requires a real
    peer allowlist and one explicitly verified, edge-overwritten IP header.
    No header name or Railway CIDR is assumed by this code.
    """
    try:
        peer = _canonical_ip(request.remote_addr)
        mode = current_app.config.get('FORM_CLIENT_IP_MODE', 'direct')
        if mode == 'direct':
            return peer
        if mode != 'verified_header':
            raise ValueError('Unknown IP mode')
        configured = current_app.config.get('FORM_TRUSTED_PROXY_CIDRS', '')
        networks = [ipaddress.ip_network(item.strip(), strict=True)
                    for item in configured.split(',') if item.strip()]
        header = current_app.config.get('FORM_VERIFIED_IP_HEADER', '')
        if (not networks or any(network.prefixlen == 0 for network in networks)
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', header)
                or header.lower() in ('x-forwarded-for', 'forwarded', 'host')):
            raise ValueError('Unverified proxy configuration')
        if not any(ipaddress.ip_address(peer) in network for network in networks):
            raise ValueError('Untrusted proxy peer')
        # ip_address rejects lists/chains. The ingress must overwrite this value.
        return _canonical_ip(request.headers.get(header))
    except (ValueError, TypeError, AttributeError):
        raise FormSecurityError('form_unavailable') from None


def _ip_hash(ip):
    secret = current_app.config.get('SECRET_KEY')
    if not isinstance(secret, str) or not secret.strip():
        raise FormSecurityError('form_unavailable')
    return hmac.new(secret.encode(), HASH_PREFIX + ip.encode(), hashlib.sha256).hexdigest()


def _storage_engine():
    engine = db.engine
    if engine.dialect.name == 'postgresql':
        return engine
    if (current_app.testing and current_app.config.get('FORM_SECURITY_SQLITE_FIXTURE') is True
            and engine.dialect.name == 'sqlite' and engine.url.database in (None, '', ':memory:')):
        return engine
    raise FormSecurityError('form_unavailable')


def _legacy_vip_duplicate(connection, canonical):
    """Read only: do not backfill or mutate existing consent/profile rows."""
    if connection.dialect.name == 'sqlite':
        return any(canonical_form_email(email) == canonical
                   for email in connection.execute(select(VipSignup.email)).scalars())
    email = func.lower(func.trim(VipSignup.email))
    domain = func.split_part(email, '@', 2)
    local = func.split_part(email, '@', 1)
    identity = case(
        (domain.in_(('gmail.com', 'googlemail.com')),
         func.concat(func.replace(func.split_part(local, '+', 1), '.', ''), '@gmail.com')),
        else_=email,
    )
    return connection.execute(select(VipSignup.id).where(identity == canonical).limit(1)).first() is not None


def claim_attempt(endpoint, ip, email=None, eligible=False):
    """Atomically consume rolling shared+endpoint buckets and reserve VIP identity.

    A short PG transaction advisory lock serializes claims across ALL workers.
    DB time is read after acquiring it; all admission queries/inserts commit
    before network/consent work. The unique partial VIP index is a second guard.
    Invalid submissions consume quota too; honeypots never reach this function.
    Denied attempts have no new row (avoids unbounded flood-driven deny audit).
    """
    if endpoint not in ('vip', 'contact'):
        raise FormSecurityError('form_unavailable')
    ip_hash = _ip_hash(ip)
    canonical = canonical_form_email(email) if eligible else None
    attempts = FormAttempt.__table__
    try:
        engine = _storage_engine()
        with engine.begin() as connection:
            if connection.dialect.name == 'postgresql':
                # After waiting for the global lock, each SELECT must see the
                # previous worker's commit, even if PG defaults to RR isolation.
                connection.execute(text('SET TRANSACTION ISOLATION LEVEL READ COMMITTED'))
                connection.execute(text("SET LOCAL lock_timeout = '1500ms'"))
                connection.execute(text("SET LOCAL statement_timeout = '3000ms'"))
                connection.execute(text('SELECT pg_advisory_xact_lock(:lock_id)'), {'lock_id': PG_LOCK_ID})
                now = connection.execute(text("SELECT clock_timestamp() AT TIME ZONE 'UTC'")).scalar_one()
            else:
                # Deterministic unit fixture only; NOT a production concurrency proof.
                now = datetime.utcnow()
            recent = and_(attempts.c.ip_hash == ip_hash, attempts.c.rate_consumed.is_(True),
                          attempts.c.attempted_at > now - timedelta(seconds=WINDOW_SECONDS))
            rows = connection.execute(select(attempts.c.endpoint, attempts.c.attempted_at)
                                      .where(recent).order_by(attempts.c.attempted_at)).all()
            endpoint_rows = [row for row in rows if row.endpoint == endpoint]
            if len(rows) >= MAX_REQUESTS or len(endpoint_rows) >= MAX_REQUESTS:
                retry = max(1, math.ceil((rows[0].attempted_at + timedelta(seconds=WINDOW_SECONDS) - now).total_seconds()))
                raise FormSecurityError('rate_limited', 429, retry)
            duplicate = False
            if canonical:
                prior = connection.execute(select(attempts.c.id).where(
                    attempts.c.endpoint == endpoint,
                    attempts.c.canonical_email == canonical,
                    attempts.c.outcome.in_(('vip_pending', 'vip_accepted', 'vip_failed',
                                            'vip_duplicate', 'contact_pending', 'contact_accepted',
                                            'contact_failed')),
                ).limit(1)).first()
                duplicate = prior is not None
                if endpoint == 'vip' and not duplicate:
                    duplicate = _legacy_vip_duplicate(connection, canonical)
            outcome = 'invalid'
            if eligible and canonical:
                outcome = 'vip_duplicate' if endpoint == 'vip' and duplicate else f'{endpoint}_pending'
            result = connection.execute(attempts.insert().values(
                endpoint=endpoint, ip_hash=ip_hash, attempted_at=now, rate_consumed=True,
                email=email, canonical_email=canonical, duplicate=duplicate,
                vip_claim=bool(endpoint == 'vip' and eligible and canonical and not duplicate),
                outcome=outcome,
            ))
            return AttemptClaim(result.inserted_primary_key[0], duplicate, now)
    except FormSecurityError:
        raise
    except Exception:
        # Do not emit DB URLs, email, IP, secrets or exception strings.
        current_app.logger.warning('form_security storage_unavailable endpoint=%s', endpoint)
        raise FormSecurityError('form_unavailable') from None


def finish_attempt(attempt_id, outcome):
    """Only update this new audit row, never historical signup/consent evidence."""
    try:
        with _storage_engine().begin() as connection:
            connection.execute(FormAttempt.__table__.update().where(FormAttempt.id == attempt_id)
                               .values(outcome=outcome))
    except Exception:
        current_app.logger.warning('form_security audit_update_unavailable')
        raise FormSecurityError('form_unavailable') from None
