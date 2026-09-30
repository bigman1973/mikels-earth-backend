"""Server-side Cloudflare Turnstile verification for public forms."""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


@dataclass(frozen=True)
class TurnstileResult:
    accepted: bool
    reason: str


def verify_turnstile(
    token: str | None,
    remote_ip: str | None = None,
    expected_action: str | None = None,
) -> TurnstileResult:
    """Validate a single-use Turnstile token without ever trusting the browser alone."""
    secret_key = os.getenv('TURNSTILE_SECRET_KEY', '').strip()
    if not secret_key:
        # Fail closed: public lead forms must not reopen without server-side bot proof.
        return TurnstileResult(False, 'not_configured')
    if not token or not isinstance(token, str):
        return TurnstileResult(False, 'missing_token')

    payload = {
        'secret': secret_key,
        'response': token,
    }
    if remote_ip:
        payload['remoteip'] = remote_ip

    try:
        response = requests.post(TURNSTILE_VERIFY_URL, data=payload, timeout=5)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        return TurnstileResult(False, 'verification_unavailable')

    if result.get('success') is not True:
        return TurnstileResult(False, 'rejected')
    if expected_action and result.get('action') != expected_action:
        return TurnstileResult(False, 'wrong_action')
    if not result.get('hostname'):
        return TurnstileResult(False, 'missing_hostname')
    if result.get('hostname') not in {'mikels.es', 'www.mikels.es'}:
        return TurnstileResult(False, 'wrong_hostname')
    return TurnstileResult(True, 'accepted')
