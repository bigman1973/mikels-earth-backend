import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from src.services import klaviyo_service


class KlaviyoVipSignupTests(unittest.TestCase):
    def setUp(self):
        klaviyo_service._vip_list_id_cache = None

    @patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key'}, clear=False)
    @patch('src.services.klaviyo_service.requests')
    def test_creates_a_single_opt_in_vip_list_and_subscribes_contact(self, requests):
        lookup = Mock(status_code=200)
        lookup.json.return_value = {'data': []}
        created = Mock(status_code=201)
        created.json.return_value = {'data': {'id': 'vip-list-id'}}
        subscribed = Mock(status_code=202)
        requests.get.return_value = lookup
        requests.post.side_effect = [created, subscribed]

        accepted, error = klaviyo_service.klaviyo_subscribe_vip_contact(
            email='vip@example.com',
            first_name='Marisa',
            consented_at=datetime(2026, 10, 13, 12, 0, 0),
            source_ip='198.51.100.42',
        )

        self.assertTrue(accepted)
        self.assertIsNone(error)
        create_payload = requests.post.call_args_list[0].kwargs['json']
        self.assertEqual(create_payload['data']['attributes']['name'], 'Black Friday VIP 2026')
        self.assertEqual(create_payload['data']['attributes']['opt_in_process'], 'single_opt_in')
        subscription_payload = requests.post.call_args_list[1].kwargs['json']
        profile = subscription_payload['data']['attributes']['profiles']['data'][0]['attributes']
        self.assertEqual(profile['email'], 'vip@example.com')
        self.assertEqual(profile['first_name'], 'Marisa')
        self.assertEqual(profile['properties']['MikelsVIPConsentIP'], '198.51.100.42')
        self.assertEqual(profile['properties']['MikelsVIPConsentAt'], '2026-10-13T12:00:00')
        self.assertEqual(profile['subscriptions']['email']['marketing']['consent'], 'SUBSCRIBED')
        self.assertEqual(subscription_payload['data']['relationships']['list']['data']['id'], 'vip-list-id')

    @patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key'}, clear=False)
    @patch('src.services.klaviyo_service.requests')
    def test_reuses_existing_dedicated_vip_list(self, requests):
        lookup = Mock(status_code=200)
        lookup.json.return_value = {'data': [{'id': 'existing-vip-list'}]}
        subscribed = Mock(status_code=202)
        requests.get.return_value = lookup
        requests.post.return_value = subscribed

        accepted, error = klaviyo_service.klaviyo_subscribe_vip_contact(email='vip@example.com')

        self.assertTrue(accepted)
        self.assertIsNone(error)
        self.assertEqual(requests.post.call_count, 1)
        payload = requests.post.call_args.kwargs['json']
        self.assertEqual(payload['data']['relationships']['list']['data']['id'], 'existing-vip-list')

    @patch('src.services.klaviyo_service.send_klaviyo_event', return_value=(True, None))
    def test_emits_the_approved_vip_signup_event_with_a_unique_audit_id(self, send_event):
        accepted, error = klaviyo_service.klaviyo_track_vip_signup(
            email='vip@example.com',
            first_name='Marisa',
            consented_at=datetime(2026, 10, 13, 12, 0, 0),
            source_ip='198.51.100.42',
            signup_id=42,
        )

        self.assertTrue(accepted)
        self.assertIsNone(error)
        self.assertEqual(send_event.call_args.kwargs['metric_name'], 'Mikels VIP Signup')
        self.assertEqual(send_event.call_args.kwargs['unique_id'], 'vip-signup-42')
        self.assertEqual(send_event.call_args.kwargs['properties']['ConsentIP'], '198.51.100.42')
        self.assertEqual(send_event.call_args.kwargs['profile_attrs'], {'first_name': 'Marisa'})

    @patch.dict(os.environ, {'KLAVIYO_API_KEY': ''}, clear=False)
    def test_reports_a_tuple_when_the_vip_event_has_no_configured_key(self):
        accepted, error = klaviyo_service.klaviyo_track_vip_signup(
            email='vip@example.com',
            consented_at=datetime(2026, 10, 13, 12, 0, 0),
            source_ip='198.51.100.42',
            signup_id=43,
        )

        self.assertFalse(accepted)
        self.assertEqual(error, 'KLAVIYO_API_KEY no configurada')


if __name__ == '__main__':
    unittest.main()
