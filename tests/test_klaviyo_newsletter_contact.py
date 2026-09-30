import os
import unittest
from unittest.mock import Mock, patch

from src.services import klaviyo_service


class KlaviyoNewsletterContactTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'KLAVIYO_API_KEY': 'test-key'}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    @patch('src.services.klaviyo_service.requests')
    def test_existing_profile_phone_is_cleared_when_whatsapp_consent_is_withdrawn(self, requests):
        lookup = Mock(status_code=200)
        lookup.json.return_value = {'data': [{'id': 'profile-123'}]}
        patch_response = Mock(status_code=200)
        requests.get.return_value = lookup
        requests.patch.return_value = patch_response

        result = klaviyo_service.add_contact_to_klaviyo(
            'cliente@example.com',
            first_name='Cliente',
            last_name='Prueba',
            phone=None,
            source='popup',
            whatsapp_marketing_accepted=False,
            subscribe_email=False,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['id'], 'profile-123')
        requests.post.assert_not_called()
        payload = requests.patch.call_args.kwargs['json']
        self.assertEqual(payload['data']['id'], 'profile-123')
        self.assertIsNone(payload['data']['attributes']['phone_number'])
        self.assertFalse(payload['data']['attributes']['properties']['NewsletterWhatsAppMarketingConsent'])

    @patch('src.services.klaviyo_service.requests')
    def test_new_profile_is_subscribed_only_when_requested(self, requests):
        lookup = Mock(status_code=200)
        lookup.json.return_value = {'data': []}
        profile_created = Mock(status_code=201)
        subscription_created = Mock(status_code=202)
        requests.get.return_value = lookup
        requests.post.side_effect = [profile_created, subscription_created]

        result = klaviyo_service.add_contact_to_klaviyo(
            'cliente@example.com',
            first_name='Cliente',
            last_name='Prueba',
            phone='+34621144701',
            source='popup',
            whatsapp_marketing_accepted=True,
            subscribe_email=True,
        )

        self.assertTrue(result['success'])
        self.assertEqual(requests.post.call_count, 2)
        profile_payload = requests.post.call_args_list[0].kwargs['json']
        self.assertEqual(profile_payload['data']['attributes']['phone_number'], '+34621144701')
        self.assertTrue(profile_payload['data']['attributes']['properties']['NewsletterWhatsAppMarketingConsent'])
        subscription_payload = requests.post.call_args_list[1].kwargs['json']
        self.assertEqual(
            subscription_payload['data']['attributes']['profiles']['data'][0]['attributes']['subscriptions']['email']['marketing']['consent'],
            'SUBSCRIBED',
        )


if __name__ == '__main__':
    unittest.main()
