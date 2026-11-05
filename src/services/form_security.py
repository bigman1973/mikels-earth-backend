"""Minimal form protection; IP admission is explicitly DISABLED.

Railway documents X-Real-IP at its public edge, but the complete ingress path
of this deployment is not certified. Never infer identity from XFF/socket peer.
No shared/IP admission schema is required or consumed by these routes.
"""
import ipaddress
import re

from flask import jsonify, request

IP_RATE_LIMITS_ENABLED = False
HONEYPOT_FIELDS = ('website', '_hp')
HONEYPOT_OUTCOME = 'honeypot_discarded'
GENERIC_SUCCESS = {'success': True, 'message': 'Solicitud recibida correctamente.'}
MAX_BODY_BYTES = 16384
UNVERIFIED_IP = 'unverified'


def read_form_payload():
    request.max_content_length = MAX_BODY_BYTES
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def honeypot_filled(data):
    """Fixed integration hook; no DB, logging, metrics or network side effects."""
    return any(data.get(field) not in (None, '') for field in HONEYPOT_FIELDS)


def silent_success():
    return jsonify(GENERIC_SUCCESS), 200


def phone_is_rejected(value):
    """Optional phone: reject exactly ten digits without +/00 prefix."""
    if value in (None, ''):
        return False
    if not isinstance(value, str):
        return True
    compact = re.sub(r'[\s().-]', '', value)
    if compact.startswith(('+', '00')):
        return False
    return re.fullmatch(r'[0-9]{10}', compact) is not None


def consent_ip_evidence():
    """Retain a syntactically valid observation, NEVER a certified client IP.

    Only the dedicated consent row may retain this value. The existing source
    field records its provenance. No XFF or proxy socket fallback is allowed.
    No IP is logged or used as an admission key or remote consent assertion.
    """
    value = request.headers.get('X-Real-IP', '')
    try:
        if '%' in value:
            raise ValueError
        address = str(ipaddress.ip_address(value.strip()))
    except (ValueError, TypeError):
        return UNVERIFIED_IP, 'vip_black_friday_2026:ip_unverified:unavailable'
    return address, 'vip_black_friday_2026:ip_unverified:x_real_ip'
