"""Minimal, consent-gated VIP CAPI sender; separate from Purchase.

No client custom_data, clear email, IP, query string or token in the URL.
No retries here; the persisted confirmation service bounds explicit attempts.
A successful HTTP status alone does not prove Meta accepted the event.
"""
from datetime import datetime, timezone
import os
import re
from uuid import UUID

import requests

EVENT_NAME = 'VIPSignupConfirmed'
SOURCE_URL = 'https://www.mikels.es/vip/gracias'
GRAPH_VERSION = 'v22.0'


def valid_user_agent(value):
    # Only the actual confirmation request's header is used, never a fallback.
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 2048
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def dispatch_meta_vip_event(*, event_id, email_hash, user_agent, event_time,
                            marketing_consent):
    """One bounded request. Result contains only fixed, sanitized reason codes."""
    if marketing_consent is not True:
        return {'sent': False, 'reason': 'marketing_not_granted'}
    try:
        if str(UUID(event_id)) != event_id:
            return {'sent': False, 'reason': 'invalid_event'}
        if not isinstance(email_hash, str) or not re.fullmatch('[0-9a-f]{64}', email_hash):
            return {'sent': False, 'reason': 'invalid_identity'}
        if not valid_user_agent(user_agent):
            return {'sent': False, 'reason': 'user_agent_required'}
        if not isinstance(event_time, datetime):
            return {'sent': False, 'reason': 'invalid_event'}
        at = event_time.replace(tzinfo=timezone.utc) if event_time.tzinfo is None else event_time
        timestamp = int(at.timestamp())
        pixel_id = str(os.getenv('META_PIXEL_ID') or '').strip()
        access_token = str(os.getenv('META_CAPI_ACCESS_TOKEN') or '').strip()
        if not pixel_id or not access_token:
            return {'sent': False, 'reason': 'meta_not_configured'}
        if not re.fullmatch('[0-9]+', pixel_id):
            return {'sent': False, 'reason': 'meta_invalid_configuration'}
        payload = {'data': [{
            'event_name': EVENT_NAME,
            'event_id': event_id,
            'event_time': timestamp,
            'event_source_url': SOURCE_URL,
            'action_source': 'website',
            'user_data': {'em': [email_hash], 'client_user_agent': user_agent},
        }]}
        response = requests.post(
            f'https://graph.facebook.com/{GRAPH_VERSION}/{pixel_id}/events',
            headers={'Authorization': f'Bearer {access_token}'},
            json=payload,
            timeout=10,
        )
        if response.status_code != 200:
            return {'sent': False, 'reason': 'meta_http_failed'}
        body = response.json()
        if (not isinstance(body, dict) or 'error' in body
                or type(body.get('events_received')) is not int
                or body['events_received'] != 1):
            return {'sent': False, 'reason': 'meta_receipt_unverified'}
        return {'sent': True, 'reason': 'accepted'}
    except requests.Timeout:
        return {'sent': False, 'reason': 'meta_timeout'}
    except requests.RequestException:
        return {'sent': False, 'reason': 'meta_request_failed'}
    except Exception:
        return {'sent': False, 'reason': 'meta_receipt_unverified'}
