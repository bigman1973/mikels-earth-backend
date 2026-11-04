"""Conservative, side-effect-free selection for the private welcome minimum repair.

Only an exact issuance marker AND one matching canonical subscriber link prove
origin. Prefixes, a 10% discount or the word 'welcome' alone are not evidence.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json

from src.services.email_identity import newsletter_email_key

WELCOME_DESCRIPTION = 'Cupón de bienvenida newsletter'
WELCOME_MINIMUM = Decimal('49.00')


def decimal_value(value):
    """Parse without rounding: an existing minimum above 49 must never be lowered."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def classify_welcome_minimum(row, *, now):
    """Return one non-personal audit category; never mutate the supplied row."""
    if row['discount_type'] != 'percentage' or decimal_value(row['discount_value']) != Decimal('10'):
        return 'skip_other_discount'
    if row['description'] != WELCOME_DESCRIPTION:
        return 'skip_unproven_metadata'
    if not str(row['code'] or '').startswith('MIKELS-'):
        return 'skip_unproven_prefix'
    owner_key = newsletter_email_key(row['email'])
    if not owner_key:
        return 'skip_public_or_invalid_owner'
    if row['link_count'] != 1:
        return 'skip_missing_or_multiple_links'
    if (
        row['subscriber_email_key'] != owner_key
        or newsletter_email_key(row['subscriber_email']) != owner_key
    ):
        return 'skip_identity_mismatch'
    if row['active'] is not True:
        return 'skip_inactive'
    if row['used'] is not False or row['used_at'] is not None or row['current_uses'] != 0:
        return 'skip_used_or_unknown_usage'
    if row['max_uses'] != 1 or row['max_uses_per_customer'] not in (None, 1):
        return 'skip_non_single_use'
    expiry = row['expires_at']
    if not isinstance(expiry, datetime) or expiry <= now:
        return 'skip_expired_or_unknown_expiry'
    if row['min_order_amount'] is None:
        return 'eligible_raise_minimum'
    minimum = decimal_value(row['min_order_amount'])
    if minimum is None:
        return 'skip_invalid_minimum'
    if minimum >= WELCOME_MINIMUM:
        return 'keep_minimum_at_or_above_49'
    return 'eligible_raise_minimum'


def audit_welcome_minimum(rows, *, now):
    """Count every bounded audit row and hash only DB ids, minima and categories.

    No codes, emails, timestamps or credential values enter the output/hash.
    The opaque aggregate fingerprint lets apply fail closed on a changed plan.
    """
    counts = {'audited': len(rows), 'eligible_raise_minimum': 0}
    fingerprint_rows = []
    eligible_ids = []
    for row in sorted(rows, key=lambda value: value['id']):
        category = classify_welcome_minimum(row, now=now)
        counts[category] = counts.get(category, 0) + 1
        if category == 'eligible_raise_minimum':
            eligible_ids.append(row['id'])
        parsed_minimum = decimal_value(row['min_order_amount'])
        minimum = str(parsed_minimum) if parsed_minimum is not None else None
        fingerprint_rows.append([row['id'], minimum, category])
    encoded = json.dumps(fingerprint_rows, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return {
        'counts': counts,
        'fingerprint': hashlib.sha256(encoded).hexdigest(),
        'minimum': str(WELCOME_MINIMUM),
    }, eligible_ids
