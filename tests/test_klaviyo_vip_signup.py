import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import Mock, patch

import requests as http_requests

from src.services import klaviyo_service


EMAIL = 'vip@example.com'
CONSENT_AT = datetime(2026, 10, 13, 12, 0, 0)
SOURCE_IP = '198.51.100.42'
PRIVATE_TEXT = f'{EMAIL} Marisa {SOURCE_IP} secret-api-key'


def response(status=200, document=None):
    result = Mock(status_code=status, text=PRIVATE_TEXT)
    result.json.return_value = document
    return result


def created_profile():
    return response(201, {'data': {'type': 'profile', 'id': 'new-profile-123'}})


class KlaviyoVipSignupTests(unittest.TestCase):
    def setUp(self):
        klaviyo_service._vip_list_id_cache = None
        offline = patch('socket.socket', side_effect=AssertionError('Network disabled in VIP tests'))
        offline.start()
        self.addCleanup(offline.stop)
        self.addCleanup(setattr, klaviyo_service, '_vip_list_id_cache', None)
        self.env = patch.dict(os.environ, {
            'KLAVIYO_API_KEY': 'unit-test-key',
            'KLAVIYO_VIP_LIST_ID': 'UcjmJ4',
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.http = patch('src.services.klaviyo_service.requests')
        self.requests = self.http.start()
        self.addCleanup(self.http.stop)
        self.requests.get.return_value = response(200, {'data': [], 'links': {'next': None}})
        self.requests.post.side_effect = [created_profile(), response(202)]

    def signup(self, **kwargs):
        defaults = dict(email=EMAIL, first_name='Marisa', consented_at=CONSENT_AT, source_ip=SOURCE_IP)
        defaults.update(kwargs)
        return klaviyo_service.klaviyo_subscribe_vip_contact(**defaults)

    def test_creates_confirmed_new_profile_with_original_consent_properties_then_bulk_202(self):
        self.assertEqual(self.signup(), (True, None))
        self.assertEqual(self.requests.post.call_count, 2)
        create_call, bulk_call = self.requests.post.call_args_list
        self.assertEqual(create_call.args[0], 'https://a.klaviyo.com/api/profiles')
        self.assertEqual(create_call.kwargs['json'], {'data': {'type': 'profile', 'attributes': {
            'email': EMAIL,
            'first_name': 'Marisa',
            'properties': {
                'MikelsVIPConsentIP': SOURCE_IP,
                'MikelsVIPConsentAt': '2026-10-13T12:00:00',
                'MikelsVIPConsentSource': 'mikels-vip-black-friday-2026',
            },
        }}})
        self.assertEqual(bulk_call.args[0], 'https://a.klaviyo.com/api/profile-subscription-bulk-create-jobs')
        payload = bulk_call.kwargs['json']['data']
        self.assertEqual(payload['type'], 'profile-subscription-bulk-create-job')
        self.assertEqual(payload['relationships']['list']['data'], {'type': 'list', 'id': 'UcjmJ4'})
        self.assertEqual(payload['attributes']['profiles']['data'], [{
            'type': 'profile', 'id': 'new-profile-123', 'attributes': {
                'email': EMAIL,
                'subscriptions': {'email': {'marketing': {'consent': 'SUBSCRIBED'}}},
            },
        }])
        self.assertNotIn('historical_import', payload['attributes'])
        self.assertNotIn('consented_at', payload['attributes']['profiles']['data'][0]['attributes']['subscriptions']['email']['marketing'])
        for call in self.requests.post.call_args_list + self.requests.get.call_args_list:
            self.assertEqual(call.kwargs['timeout'], 15)
            self.assertEqual(call.kwargs['headers']['revision'], '2024-10-15')
        self.requests.patch.assert_not_called()

    def test_new_profile_without_first_name_preserves_original_omission(self):
        self.assertEqual(self.signup(first_name=''), (True, None))
        self.assertNotIn('first_name', self.requests.post.call_args_list[0].kwargs['json']['data']['attributes'])

    def test_new_profile_without_explicit_datetime_consent_cannot_mutate(self):
        for consent in (None, '', False, '2026-10-13T12:00:00'):
            with self.subTest(consent=consent):
                self.requests.reset_mock()
                self.assertEqual(self.signup(consented_at=consent), (False, 'VIP_NEW_CONSENT_REQUIRED'))
                self.requests.post.assert_not_called()
                self.requests.patch.assert_not_called()

    def test_non_201_creation_never_subscribes_or_retries(self):
        for status in (200, 202, 204, 400, 401, 403, 422, 429, 500, 502, 503):
            with self.subTest(status=status):
                self.requests.reset_mock()
                self.requests.post.side_effect = [response(status)]
                self.assertEqual(self.signup(), (False, f'VIP_PROFILE_CREATE_FAILED_HTTP_{status}'))
                self.assertEqual(self.requests.post.call_count, 1)
                self.assertEqual(self.requests.get.call_count, 1)
                self.requests.patch.assert_not_called()

    def test_malformed_creation_or_missing_id_never_subscribes(self):
        documents = [None, [], {}, {'data': None}, {'data': []}, {'data': {}},
                     {'data': {'type': 'profile'}}, {'data': {'type': 'profile', 'id': ''}},
                     {'data': {'type': 'profile', 'id': 42}},
                     {'data': {'type': 'profile', 'id': 'profile/123'}},
                     {'data': {'type': 'profile', 'id': ' profile-123'}},
                     {'data': {'type': 'list', 'id': 'profile-123'}},
                     {'data': {'type': 'profile', 'id': 'profile-123'}, 'errors': []}]
        for document in documents:
            with self.subTest(document=document):
                self.requests.reset_mock()
                self.requests.post.side_effect = [response(201, document)]
                self.assertEqual(self.signup(), (False, 'VIP_PROFILE_CREATE_MALFORMED'))
                self.assertEqual(self.requests.post.call_count, 1)
                self.requests.patch.assert_not_called()

    def test_invalid_json_creation_never_subscribes(self):
        malformed = response(201)
        malformed.json.side_effect = ValueError(PRIVATE_TEXT)
        self.requests.post.side_effect = [malformed]
        self.assertEqual(self.signup(), (False, 'VIP_PROFILE_CREATE_MALFORMED'))
        self.assertEqual(self.requests.post.call_count, 1)

    def test_creation_timeout_or_exception_never_retries_or_subscribes(self):
        for exc in (http_requests.Timeout(PRIVATE_TEXT), http_requests.ConnectionError(PRIVATE_TEXT), RuntimeError(PRIVATE_TEXT)):
            with self.subTest(exception=type(exc).__name__):
                self.requests.reset_mock()
                self.requests.post.side_effect = exc
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(self.signup(), (False, 'VIP_PROFILE_CREATE_FAILED'))
                self.assertEqual(self.requests.post.call_count, 1)
                self.assertEqual(self.requests.get.call_count, 1)
                self.assertEqual(output.getvalue(), '')

    def test_bulk_only_accepts_documented_202_status(self):
        for status in (200, 201, 204, 400, 403, 409, 429, 500, 503):
            with self.subTest(status=status):
                self.requests.reset_mock()
                self.requests.post.side_effect = [created_profile(), response(status)]
                self.assertEqual(self.signup(), (False, f'VIP_NEW_SUBSCRIPTION_FAILED_HTTP_{status}'))
                self.assertEqual(self.requests.post.call_count, 2)
                self.requests.patch.assert_not_called()

    def test_bulk_timeout_does_not_retry(self):
        self.requests.post.side_effect = [created_profile(), http_requests.Timeout(PRIVATE_TEXT)]
        self.assertEqual(self.signup(), (False, 'VIP_NEW_SUBSCRIPTION_FAILED'))
        self.assertEqual(self.requests.post.call_count, 2)
        self.assertEqual(self.requests.get.call_count, 1)

    def test_retry_after_uncertain_bulk_does_not_bulk_subscribe_existing_profile(self):
        from tests.test_vip_existing_consent import existing_profile
        self.requests.post.side_effect = [created_profile(), http_requests.Timeout(PRIVATE_TEXT)]
        self.assertEqual(self.signup(), (False, 'VIP_NEW_SUBSCRIPTION_FAILED'))
        self.requests.reset_mock()
        self.requests.get.return_value = response(200, {'data': [existing_profile(consent='NEVER_SUBSCRIBED')]})
        self.assertEqual(self.signup(), (False, 'VIP_EXISTING_NO_EMAIL_CONSENT'))
        self.requests.post.assert_not_called()
        self.requests.patch.assert_not_called()

    def test_gets_and_caches_existing_dedicated_list_without_creating_it(self):
        os.environ['KLAVIYO_VIP_LIST_ID'] = ''
        self.requests.get.return_value = response(200, {'data': [{'type': 'list', 'id': 'UcjmJ4'}]})
        self.assertEqual(klaviyo_service._get_vip_list_id(), ('UcjmJ4', None))
        self.assertEqual(klaviyo_service._get_vip_list_id(), ('UcjmJ4', None))
        self.assertEqual(self.requests.get.call_count, 1)
        self.assertEqual(self.requests.get.call_args.kwargs['params']['filter'], 'equals(name,"Black Friday VIP 2026")')
        self.requests.post.assert_not_called()
        self.requests.patch.assert_not_called()

    def test_missing_list_is_never_auto_created(self):
        os.environ['KLAVIYO_VIP_LIST_ID'] = ''
        self.assertEqual(klaviyo_service._get_vip_list_id(), (None, 'VIP_LIST_NOT_FOUND'))
        self.requests.post.assert_not_called()
        self.requests.patch.assert_not_called()

    def test_list_lookup_errors_are_sanitized_and_never_create_a_list(self):
        os.environ['KLAVIYO_VIP_LIST_ID'] = ''
        for status in (400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                self.requests.get.return_value = response(status)
                self.assertEqual(klaviyo_service._get_vip_list_id(), (None, f'VIP_LIST_LOOKUP_FAILED_HTTP_{status}'))
        self.requests.get.side_effect = http_requests.Timeout(PRIVATE_TEXT)
        self.assertEqual(klaviyo_service._get_vip_list_id(), (None, 'VIP_LIST_LOOKUP_FAILED'))
        self.requests.post.assert_not_called()

    def test_list_lookup_malformed_and_ambiguous_responses_fail_closed(self):
        os.environ['KLAVIYO_VIP_LIST_ID'] = ''
        for document, code in [
            (None, 'MALFORMED'), ({}, 'MALFORMED'), ({'data': None}, 'MALFORMED'),
            ({'data': [{'id': 'UcjmJ4'}]}, 'MALFORMED'),
            ({'data': [{'type': 'list', 'id': None}]}, 'MALFORMED'),
            ({'data': [], 'links': None}, 'MALFORMED'),
            ({'data': [], 'links': {'next': 123}}, 'MALFORMED'),
            ({'data': [], 'links': {'next': 'next-page'}}, 'AMBIGUOUS'),
            ({'data': [{'id': 'UcjmJ4'}, {'id': 'second-list'}]}, 'AMBIGUOUS'),
        ]:
            with self.subTest(document=document):
                self.requests.get.return_value = response(200, document)
                self.assertEqual(klaviyo_service._get_vip_list_id(), (None, f'VIP_LIST_LOOKUP_{code}'))
        invalid = response(200)
        invalid.json.side_effect = ValueError(PRIVATE_TEXT)
        self.requests.get.return_value = invalid
        self.assertEqual(klaviyo_service._get_vip_list_id(), (None, 'VIP_LIST_LOOKUP_MALFORMED'))
        self.requests.post.assert_not_called()
        self.requests.patch.assert_not_called()

    def test_invalid_configured_list_id_cannot_mutate(self):
        os.environ['KLAVIYO_VIP_LIST_ID'] = '../lists/other'
        self.assertEqual(self.signup(), (False, 'VIP_LIST_CONFIGURATION_INVALID'))
        self.requests.post.assert_not_called()
        self.requests.patch.assert_not_called()

    def test_missing_api_key_is_sanitized_before_lookup_or_mutations(self):
        os.environ['KLAVIYO_API_KEY'] = ''
        self.assertEqual(self.signup(), (False, 'VIP_API_KEY_NOT_CONFIGURED'))
        self.assertEqual(klaviyo_service._get_vip_list_id(), (None, 'VIP_API_KEY_NOT_CONFIGURED'))
        self.requests.get.assert_not_called()
        self.requests.post.assert_not_called()

    @patch('src.services.klaviyo_service.send_klaviyo_event', return_value=(True, None))
    def test_event_preserves_audit_properties_but_does_not_change_profile_name(self, send_event):
        self.assertEqual(klaviyo_service.klaviyo_track_vip_signup(
            email=EMAIL, first_name='Marisa', consented_at=CONSENT_AT, source_ip=SOURCE_IP, signup_id=42,
        ), (True, None))
        options = send_event.call_args.kwargs
        self.assertEqual(options['metric_name'], 'Mikels VIP Signup')
        self.assertEqual(options['unique_id'], 'vip-signup-42')
        self.assertEqual(options['properties'], {
            'ConsentDate': '2026-10-13T12:00:00', 'ConsentIP': SOURCE_IP,
            'Source': 'mikels-vip-black-friday-2026', 'VipSignupId': 42,
        })
        self.assertNotIn('profile_attrs', options)
        self.assertTrue(options['redact_logs'])
        self.assertTrue(options['return_result'])

    def track(self):
        return klaviyo_service.klaviyo_track_vip_signup(
            email=EMAIL, first_name='Marisa', consented_at=CONSENT_AT, source_ip=SOURCE_IP, signup_id=42,
        )

    def test_event_payload_contains_only_email_profile_attribute_and_timeout(self):
        self.requests.post.side_effect = None
        self.requests.post.return_value = response(202)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.track(), (True, None))
        options = self.requests.post.call_args.kwargs
        self.assertEqual(options['timeout'], 15)
        self.assertEqual(options['json']['data']['attributes']['profile']['data']['attributes'], {'email': EMAIL})
        self.assertEqual(output.getvalue().strip(), '[KLAVIYO] VIP_EVENT_ACCEPTED HTTP 202')
        self.requests.patch.assert_not_called()

    def test_event_http_failures_only_return_codes_and_status_not_response_pii(self):
        for status in (200, 201, 204, 400, 403, 429, 500, 503):
            with self.subTest(status=status):
                self.requests.post.side_effect = [response(status)]
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(self.track(), (False, f'VIP_EVENT_HTTP_ERROR_HTTP_{status}'))
                self.assertEqual(output.getvalue(), '')

    def test_event_exception_logs_and_error_never_expose_pii(self):
        self.requests.post.side_effect = http_requests.Timeout(PRIVATE_TEXT)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.track(), (False, 'VIP_EVENT_REQUEST_FAILED'))
        self.assertEqual(output.getvalue().strip(), '[KLAVIYO] VIP_EVENT_REQUEST_FAILED')

    def test_event_missing_key_reports_sanitized_tuple(self):
        os.environ['KLAVIYO_API_KEY'] = ''
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.track(), (False, 'VIP_API_KEY_NOT_CONFIGURED'))
        self.assertEqual(output.getvalue().strip(), 'ERROR: VIP_API_KEY_NOT_CONFIGURED')
        self.requests.post.assert_not_called()

    def test_non_vip_event_defaults_remain_unchanged(self):
        self.requests.post.side_effect = [response(202)]
        with redirect_stdout(io.StringIO()):
            self.assertTrue(klaviyo_service.send_klaviyo_event('Regular event', EMAIL, {}, profile_attrs={'first_name': 'Marisa'}))
        options = self.requests.post.call_args.kwargs
        self.assertNotIn('timeout', options)
        self.assertEqual(options['json']['data']['attributes']['profile']['data']['attributes']['first_name'], 'Marisa')


if __name__ == '__main__':
    unittest.main()
