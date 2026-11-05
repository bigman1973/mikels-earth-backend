import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import Mock, patch

import requests as http_requests

from src.services import klaviyo_service


EMAIL = 'vip@example.com'
PRIVATE_TEXT = f'{EMAIL} Marisa 198.51.100.42 secret-api-key'


def response(status=200, document=None):
    result = Mock(status_code=status, text=PRIVATE_TEXT)
    result.json.return_value = document
    return result


def existing_profile(**marketing_overrides):
    marketing = {
        'consent': 'SUBSCRIBED',
        'can_receive_email_marketing': True,
        'suppression': [],
        'list_suppressions': [],
    }
    marketing.update(marketing_overrides)
    return {
        'type': 'profile', 'id': 'existing-profile-123',
        'attributes': {
            'email': EMAIL,
            'first_name': 'Original name',
            'properties': {'MikelsVIPConsentAt': 'original-date', 'MikelsVIPConsentIP': 'original-ip'},
            'subscriptions': {'email': {'marketing': marketing}},
        },
    }


class VipExistingConsentTests(unittest.TestCase):
    def setUp(self):
        klaviyo_service._vip_list_id_cache = None
        offline = patch('socket.socket', side_effect=AssertionError('Network disabled in VIP tests'))
        offline.start()
        self.addCleanup(offline.stop)
        self.addCleanup(setattr, klaviyo_service, '_vip_list_id_cache', None)
        env = patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key', 'KLAVIYO_VIP_LIST_ID': 'UcjmJ4'}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        http = patch('src.services.klaviyo_service.requests')
        self.requests = http.start()
        self.addCleanup(http.stop)
        self.requests.get.return_value = response(200, {'data': [existing_profile()]})
        self.requests.post.return_value = response(204)

    def signup(self, **kwargs):
        defaults = dict(email=EMAIL, first_name='Replacement name', consented_at=datetime(2026, 11, 5), source_ip='198.51.100.42')
        defaults.update(kwargs)
        return klaviyo_service.klaviyo_subscribe_vip_contact(**defaults)

    def deny(self, document, code):
        self.requests.reset_mock()
        self.requests.get.return_value = response(200, document)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.signup(), (False, code))
        self.requests.post.assert_not_called()
        self.requests.patch.assert_not_called()
        self.assertEqual(output.getvalue(), '')

    def test_existing_subscribed_unsuppressed_profile_only_posts_exact_relationship(self):
        profile = existing_profile()
        before = copy.deepcopy(profile)
        self.requests.get.return_value = response(200, {'data': [profile], 'links': {'next': None}})
        self.assertEqual(self.signup(), (True, None))
        self.requests.post.assert_called_once_with(
            'https://a.klaviyo.com/api/lists/UcjmJ4/relationships/profiles',
            headers=klaviyo_service._get_headers(),
            json={'data': [{'type': 'profile', 'id': 'existing-profile-123'}]},
            timeout=15,
        )
        self.assertEqual(profile, before)
        self.requests.patch.assert_not_called()
        self.requests.get.assert_called_once_with(
            'https://a.klaviyo.com/api/profiles',
            headers=klaviyo_service._get_headers(),
            params={'filter': 'equals(email,"vip@example.com")', 'additional-fields[profile]': 'subscriptions', 'page[size]': 2},
            timeout=15,
        )

    def test_existing_profile_does_not_need_new_website_consent_timestamp(self):
        self.assertEqual(self.signup(consented_at=None), (True, None))
        self.assertEqual(self.requests.post.call_count, 1)
        self.assertIn('/relationships/profiles', self.requests.post.call_args.args[0])
        self.requests.patch.assert_not_called()

    def test_profile_lookup_escapes_email_as_json_string(self):
        for email in ('o\'hara@example.com', 'double"quote@example.com', 'back\\slash@example.com', 'a),equals(email,"other@example.com")', 'unicode-á@example.com'):
            with self.subTest(email=email):
                self.requests.reset_mock()
                self.assertEqual(self.signup(email=email), (True, None))
                expression = self.requests.get.call_args.kwargs['params']['filter']
                self.assertEqual(expression, f'equals(email,{json.dumps(email)})')
                self.assertEqual(json.loads(expression[len('equals(email,'):-1]), email)

    def test_never_subscribed_even_if_can_receive_true_is_not_email_consent(self):
        self.deny({'data': [existing_profile(consent='NEVER_SUBSCRIBED')]}, 'VIP_EXISTING_NO_EMAIL_CONSENT')

    def test_unsubscribed_even_if_can_receive_true_cannot_be_resubscribed(self):
        self.deny({'data': [existing_profile(consent='UNSUBSCRIBED')]}, 'VIP_EXISTING_NO_EMAIL_CONSENT')

    def test_all_five_global_suppression_reasons_are_rejected(self):
        for reason in ('HARD_BOUNCE', 'INVALID_EMAIL', 'SPAM_COMPLAINT', 'UNSUBSCRIBE', 'USER_SUPPRESSED'):
            with self.subTest(reason=reason):
                self.deny({'data': [existing_profile(suppression=[{'reason': reason, 'timestamp': '2026-11-01T10:00:00+00:00'}])]}, 'VIP_EXISTING_EMAIL_SUPPRESSED')

    def test_can_receive_false_with_subscribed_consent_is_email_suppressed(self):
        self.deny({'data': [existing_profile(can_receive_email_marketing=False)]}, 'VIP_EXISTING_EMAIL_SUPPRESSED')

    def test_any_list_suppression_including_unrelated_list_is_rejected(self):
        for list_id in ('UcjmJ4', 'WWPsb2', 'another-list'):
            with self.subTest(list_id=list_id):
                self.deny({'data': [existing_profile(list_suppressions=[{'list_id': list_id, 'reason': 'UNSUBSCRIBE', 'timestamp': '2026-11-01T10:00:00+00:00'}])]}, 'VIP_EXISTING_LIST_SUPPRESSED')

    def test_each_missing_required_marketing_field_is_unverified(self):
        for key in ('consent', 'can_receive_email_marketing', 'suppression', 'list_suppressions'):
            with self.subTest(key=key):
                profile = existing_profile()
                del profile['attributes']['subscriptions']['email']['marketing'][key]
                self.deny({'data': [profile]}, 'VIP_EXISTING_CONSENT_UNVERIFIED')

    def test_missing_or_malformed_nested_subscription_objects_are_unverified(self):
        for path in (('attributes',), ('attributes', 'subscriptions'), ('attributes', 'subscriptions', 'email'), ('attributes', 'subscriptions', 'email', 'marketing')):
            for value in ('missing', None, [], '', True):
                with self.subTest(path=path, value=value):
                    profile = existing_profile()
                    parent = profile
                    for key in path[:-1]:
                        parent = parent[key]
                    if value == 'missing':
                        del parent[path[-1]]
                    else:
                        parent[path[-1]] = value
                    self.deny({'data': [profile]}, 'VIP_EXISTING_CONSENT_UNVERIFIED')

    def test_malformed_consent_boolean_or_suppression_arrays_are_unverified(self):
        for key, values in (
            ('consent', (None, '', [], {}, 'subscribed', 'UNKNOWN', True)),
            ('can_receive_email_marketing', (None, '', 'true', 'false', 1, 0, [], {})),
            ('suppression', (None, {}, '', True, 0)),
            ('list_suppressions', (None, {}, '', True, 0)),
        ):
            for value in values:
                with self.subTest(key=key, value=value):
                    self.deny({'data': [existing_profile(**{key: value})]}, 'VIP_EXISTING_CONSENT_UNVERIFIED')

    def test_malformed_suppression_array_entries_are_unverified(self):
        for field, entries in (
            ('suppression', [None, 'HARD_BOUNCE', {}, {'reason': 'HARD_BOUNCE'}, {'reason': None, 'timestamp': 'date'}, {'reason': 'HARD_BOUNCE', 'timestamp': False}]),
            ('list_suppressions', [None, 'UcjmJ4', {}, {'list_id': 'UcjmJ4', 'reason': 'UNSUBSCRIBE'}, {'list_id': None, 'reason': 'UNSUBSCRIBE', 'timestamp': 'date'}]),
        ):
            for entry in entries:
                with self.subTest(field=field, entry=entry):
                    self.deny({'data': [existing_profile(**{field: [entry]})]}, 'VIP_EXISTING_CONSENT_UNVERIFIED')

    def test_malformed_profile_collection_is_never_treated_as_absent(self):
        for document in (None, [], {}, {'data': None}, {'data': {}}, {'data': '[]'}, {'data': [None]}, {'data': [False]}, {'data': [{'id': 'profile-123'}]}, {'data': [{'type': 'profile', 'id': None}]}, {'data': [{'type': 'profile', 'id': 'profile/123'}]}, {'data': [], 'errors': []}, {'data': [], 'links': None}, {'data': [], 'links': []}, {'data': [], 'links': {'next': False}}, {'data': [], 'links': {'next': ''}}):
            with self.subTest(document=document):
                self.deny(document, 'VIP_PROFILE_LOOKUP_MALFORMED')

    def test_multiple_profiles_or_next_page_are_ambiguous(self):
        for document in ({'data': [existing_profile(), existing_profile()]}, {'data': [], 'links': {'next': 'next-page'}}, {'data': [existing_profile()], 'links': {'next': 'next-page'}}):
            with self.subTest(document=document):
                self.deny(document, 'VIP_PROFILE_LOOKUP_AMBIGUOUS')

    def test_http_lookup_failures_never_create_update_or_subscribe(self):
        for status in (400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.requests.reset_mock()
                self.requests.get.return_value = response(status)
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(self.signup(), (False, f'VIP_PROFILE_LOOKUP_FAILED_HTTP_{status}'))
                self.requests.post.assert_not_called()
                self.requests.patch.assert_not_called()
                self.assertEqual(output.getvalue(), '')

    def test_lookup_timeouts_and_exceptions_fail_closed_without_pii(self):
        for exc in (http_requests.Timeout(PRIVATE_TEXT), http_requests.ConnectionError(PRIVATE_TEXT), RuntimeError(PRIVATE_TEXT)):
            with self.subTest(exception=type(exc).__name__):
                self.requests.reset_mock()
                self.requests.get.side_effect = exc
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(self.signup(), (False, 'VIP_PROFILE_LOOKUP_FAILED'))
                self.requests.post.assert_not_called()
                self.requests.patch.assert_not_called()
                self.assertEqual(output.getvalue(), '')

    def test_invalid_json_lookup_does_not_mean_profile_absent(self):
        lookup = response(200)
        lookup.json.side_effect = ValueError(PRIVATE_TEXT)
        self.requests.get.return_value = lookup
        self.assertEqual(self.signup(), (False, 'VIP_PROFILE_LOOKUP_MALFORMED'))
        self.requests.post.assert_not_called()
        self.requests.patch.assert_not_called()

    def test_relationship_only_accepts_204_and_never_falls_back_to_bulk(self):
        for status in (200, 201, 202, 400, 403, 409, 429, 500, 503):
            with self.subTest(status=status):
                self.requests.reset_mock()
                self.requests.post.return_value = response(status)
                self.assertEqual(self.signup(), (False, f'VIP_LIST_MEMBERSHIP_FAILED_HTTP_{status}'))
                self.assertEqual(self.requests.post.call_count, 1)
                self.assertIn('/relationships/profiles', self.requests.post.call_args.args[0])
                self.requests.patch.assert_not_called()

    def test_relationship_timeout_never_retries_or_uses_bulk(self):
        self.requests.post.side_effect = http_requests.Timeout(PRIVATE_TEXT)
        self.assertEqual(self.signup(), (False, 'VIP_LIST_MEMBERSHIP_FAILED'))
        self.assertEqual(self.requests.post.call_count, 1)
        self.assertEqual(self.requests.get.call_count, 1)
        self.requests.patch.assert_not_called()

    def test_already_in_vip_list_remains_idempotent_via_relationship_204(self):
        profile = existing_profile()
        profile['relationships'] = {'lists': {'data': [{'type': 'list', 'id': 'UcjmJ4'}]}}
        self.requests.get.return_value = response(200, {'data': [profile]})
        for _ in range(2):
            self.assertEqual(self.signup(), (True, None))
        self.assertEqual(self.requests.get.call_count, 2)
        self.assertEqual(self.requests.post.call_count, 2)
        for call in self.requests.post.call_args_list:
            self.assertIn('/relationships/profiles', call.args[0])
        self.requests.patch.assert_not_called()

    def test_creation_409_reloads_current_consent_and_only_adds_eligible_relationship(self):
        self.requests.get.side_effect = [response(200, {'data': []}), response(200, {'data': [existing_profile()]})]
        self.requests.post.side_effect = [response(409), response(204)]
        self.assertEqual(self.signup(), (True, None))
        self.assertEqual(self.requests.get.call_count, 2)
        self.assertEqual(self.requests.post.call_count, 2)
        create, relationship = self.requests.post.call_args_list
        self.assertEqual(create.args[0], 'https://a.klaviyo.com/api/profiles')
        self.assertIn('/relationships/profiles', relationship.args[0])
        self.assertEqual(relationship.kwargs['json'], {'data': [{'type': 'profile', 'id': 'existing-profile-123'}]})
        self.requests.patch.assert_not_called()

    def test_creation_409_never_bulk_subscribes_ineligible_or_unverified_existing_profiles(self):
        for overrides, code in (
            ({'consent': 'NEVER_SUBSCRIBED'}, 'VIP_EXISTING_NO_EMAIL_CONSENT'),
            ({'consent': 'UNSUBSCRIBED'}, 'VIP_EXISTING_NO_EMAIL_CONSENT'),
            ({'can_receive_email_marketing': False}, 'VIP_EXISTING_EMAIL_SUPPRESSED'),
            ({'list_suppressions': [{'list_id': 'unrelated-list', 'reason': 'USER_SUPPRESSED', 'timestamp': '2026-11-01T10:00:00+00:00'}]}, 'VIP_EXISTING_LIST_SUPPRESSED'),
            ({'suppression': None}, 'VIP_EXISTING_CONSENT_UNVERIFIED'),
        ):
            with self.subTest(overrides=overrides):
                self.requests.reset_mock()
                self.requests.get.side_effect = [response(200, {'data': []}), response(200, {'data': [existing_profile(**overrides)]})]
                self.requests.post.side_effect = [response(409)]
                self.assertEqual(self.signup(), (False, code))
                self.assertEqual(self.requests.post.call_count, 1)
                self.requests.patch.assert_not_called()

    def test_creation_409_with_unresolved_or_failed_lookup_does_not_retry(self):
        for lookup, code in (
            (response(200, {'data': []}), 'VIP_PROFILE_LOOKUP_CONFLICT_UNRESOLVED'),
            (response(429), 'VIP_PROFILE_LOOKUP_FAILED_HTTP_429'),
            (response(200, {}), 'VIP_PROFILE_LOOKUP_MALFORMED'),
            (http_requests.Timeout(PRIVATE_TEXT), 'VIP_PROFILE_LOOKUP_FAILED'),
        ):
            with self.subTest(code=code):
                self.requests.reset_mock()
                self.requests.get.side_effect = [response(200, {'data': []}), lookup]
                self.requests.post.side_effect = [response(409)]
                self.assertEqual(self.signup(), (False, code))
                self.assertEqual(self.requests.post.call_count, 1)
                self.assertEqual(self.requests.get.call_count, 2)
                self.requests.patch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
