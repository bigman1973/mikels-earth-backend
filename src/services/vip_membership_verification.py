"""Read-only VIP membership evidence (Klaviyo revision 2024-10-15).

GET /lists/{id}/profiles accepts an email filter and subscriptions additional
fields: https://developers.klaviyo.com/en/v2024-10-15/reference/get_profiles_for_list
Requires lists:read and profiles:read. We deliberately reuse the hotfix's private
_get_vip_list_id, _vip_collection and _vip_existing_consent_error contracts,
without changing them or invoking any subscription/profile mutation helpers.
An empty, unambiguous 200 is absence; a 202 or failed read is NOT membership.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from src.services import klaviyo_service as klaviyo
from src.services.email_identity import normalize_email_address


@dataclass(frozen=True)
class VipMembershipEvidence:
    state: str  # absent, member, ineligible, unknown, mismatch; never public
    list_id: str | None = None
    profile_id: str | None = None
    checked_at: datetime | None = None


def _valid_joined_at(value):
    if not isinstance(value, str):
        return False
    try:
        joined = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return joined.tzinfo is not None and joined <= datetime.now(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return False


def read_vip_membership(email):
    """One bounded GET of the filtered VIP list; never POST/PATCH or retry.

    Any member before signup disqualifies a new conversion, regardless of their
    consent. Only state=member proves BOTH membership and current usable consent.
    Malformed, paginated, mismatched and ambiguous responses fail closed.
    """
    unknown = VipMembershipEvidence('unknown')
    try:
        email = normalize_email_address(email)
        if not email or not klaviyo._get_api_key():
            return unknown
        list_id, error = klaviyo._get_vip_list_id()
        if error or not klaviyo._vip_resource_id(list_id):
            return unknown
        response = klaviyo.requests.get(
            f'{klaviyo.KLAVIYO_API_URL}/lists/{list_id}/profiles',
            headers=klaviyo._get_headers(),
            params={
                'filter': f'equals(email,{json.dumps(email)})',
                'additional-fields[profile]': 'subscriptions',
                'page[size]': 2,
            },
            timeout=10,
        )
        if response.status_code != 200:
            return unknown
        document = response.json()
        profiles, error = klaviyo._vip_collection(document, 'VIP_MEMBERSHIP')
        if error:
            return unknown
        checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if not profiles:
            # Absence is probative only with an explicit terminal page marker.
            links = document.get('links')
            if not isinstance(links, dict) or 'next' not in links or links['next'] is not None:
                return unknown
            return VipMembershipEvidence('absent', list_id, checked_at=checked_at)
        profile = profiles[0]
        if (not isinstance(profile, dict) or profile.get('type') != 'profile'
                or not klaviyo._vip_resource_id(profile.get('id'))):
            return unknown
        attributes = profile.get('attributes')
        if not isinstance(attributes, dict):
            return unknown
        actual_email = normalize_email_address(attributes.get('email'))
        if actual_email and actual_email != email:
            return VipMembershipEvidence('mismatch', list_id, profile['id'], checked_at)
        if not actual_email or not _valid_joined_at(attributes.get('joined_group_at')):
            return unknown
        consent_error = klaviyo._vip_existing_consent_error(profile)
        if consent_error == 'VIP_EXISTING_CONSENT_UNVERIFIED':
            return VipMembershipEvidence('unknown', list_id, profile['id'], checked_at)
        return VipMembershipEvidence('ineligible' if consent_error else 'member',
                                     list_id, profile['id'], checked_at)
    except Exception:
        # Exception text can include private keys, email filters and URLs.
        return unknown
