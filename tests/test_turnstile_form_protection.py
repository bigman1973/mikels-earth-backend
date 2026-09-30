import os
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from src.routes import contact_routes, newsletter_routes
from src.routes.contact_routes import contact_bp
from src.routes.newsletter_routes import newsletter_bp
from src.services.turnstile_service import verify_turnstile


class TurnstileServiceTests(unittest.TestCase):
    def setUp(self):
        self.previous_secret = os.environ.get('TURNSTILE_SECRET_KEY')

    def tearDown(self):
        if self.previous_secret is None:
            os.environ.pop('TURNSTILE_SECRET_KEY', None)
        else:
            os.environ['TURNSTILE_SECRET_KEY'] = self.previous_secret

    def test_fails_closed_when_secret_is_not_configured(self):
        os.environ.pop('TURNSTILE_SECRET_KEY', None)
        self.assertEqual(verify_turnstile('token').reason, 'not_configured')
        self.assertFalse(verify_turnstile('token').accepted)

    @patch('src.services.turnstile_service.requests.post')
    def test_accepts_only_valid_token_for_expected_action_and_hostname(self, mocked_post):
        os.environ['TURNSTILE_SECRET_KEY'] = 'test-secret'
        mocked_response = Mock()
        mocked_response.json.return_value = {
            'success': True,
            'action': 'contact_form',
            'hostname': 'www.mikels.es',
        }
        mocked_post.return_value = mocked_response

        result = verify_turnstile('token', '203.0.113.1', expected_action='contact_form')

        self.assertTrue(result.accepted)
        mocked_post.assert_called_once()

    @patch('src.services.turnstile_service.requests.post')
    def test_rejects_wrong_action_or_hostname(self, mocked_post):
        os.environ['TURNSTILE_SECRET_KEY'] = 'test-secret'
        mocked_response = Mock()
        mocked_response.json.return_value = {
            'success': True,
            'action': 'other_form',
            'hostname': 'invalid.example',
        }
        mocked_post.return_value = mocked_response

        self.assertEqual(
            verify_turnstile('token', expected_action='contact_form').reason,
            'wrong_action',
        )


class FormTurnstileContractTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.register_blueprint(contact_bp, url_prefix='/api/contact')
        self.app.register_blueprint(newsletter_bp, url_prefix='/api/newsletter')
        contact_routes._contact_rate_store.clear()
        newsletter_routes._newsletter_rate_store.clear()
        self.client = self.app.test_client()

    @patch('src.routes.contact_routes.verify_turnstile')
    def test_contact_refuses_unverified_submission_before_delivery(self, verify):
        verify.return_value = Mock(accepted=False, reason='rejected')
        with patch('src.routes.contact_routes.dispatch_contact_notification') as notify:
            response = self.client.post('/api/contact/send-message', json={
                'name': 'Cliente Real',
                'email': 'client@example.com',
                'message': 'Necesito información sobre un pedido.',
                '_ts': 1,
            })
        self.assertEqual(response.status_code, 400)
        notify.assert_not_called()

    @patch('src.routes.newsletter_routes.verify_turnstile')
    def test_newsletter_refuses_unverified_submission_before_consent_or_coupon(self, verify):
        verify.return_value = Mock(accepted=False, reason='rejected')
        response = self.client.post('/api/newsletter/subscribe', json={
            'email': 'cliente@example.com',
            'first_name': 'Cliente',
            'last_name': 'Prueba',
            'source': 'popup',
            'privacy_policy_accepted': True,
        })
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
