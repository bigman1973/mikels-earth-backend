"""Route and service integration: no real network or customer profiles."""
from contextlib import redirect_stdout
from io import StringIO
import os
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from src.models.user import db
from src.models.vip_signup import VipSignup
from src.routes import vip_routes
from src.services import klaviyo_service


class VipExistingConsentRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(vip_routes.vip_bp, url_prefix='/api/vip')
        with self.app.app_context():
            db.create_all()
        vip_routes._vip_rate_store.clear()
        klaviyo_service._vip_list_id_cache = None
        self.client = self.app.test_client()
        self.environment = patch.dict(os.environ, {
            'KLAVIYO_API_KEY': 'unit-test-key',
            'KLAVIYO_VIP_LIST_ID': 'vip-list-id',
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        klaviyo_service._vip_list_id_cache = None
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def submit(self):
        return self.client.post('/api/vip/signup', json={
            'email': 'vip@example.com',
            'first_name': 'Submitted name must not overwrite the profile',
            'marketing_consent': True,
        })

    def profile_response(self, **overrides):
        marketing = {
            'consent': 'SUBSCRIBED',
            'can_receive_email_marketing': True,
            'suppression': [],
            'list_suppressions': [],
        }
        marketing.update(overrides)
        response = Mock(status_code=200)
        response.json.return_value = {
            'data': [{
                'type': 'profile',
                'id': 'existing-profile-id',
                'attributes': {
                    'email': 'vip@example.com',
                    'first_name': 'Original name',
                    'subscriptions': {'email': {'marketing': marketing}},
                },
            }],
            'links': {'next': None},
        }
        return response

    @patch('src.services.klaviyo_service.requests')
    def test_existing_consented_signup_adds_only_relationship_and_non_mutating_event(self, requests):
        requests.get.return_value = self.profile_response()
        requests.post.side_effect = [Mock(status_code=204), Mock(status_code=202)]
        captured = StringIO()
        with redirect_stdout(captured):
            response = self.submit()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(requests.post.call_count, 2)
        requests.patch.assert_not_called()
        first, second = requests.post.call_args_list
        self.assertEqual(first.args[0], f'{klaviyo_service.KLAVIYO_API_URL}/lists/vip-list-id/relationships/profiles')
        self.assertEqual(first.kwargs['json'], {
            'data': [{'type': 'profile', 'id': 'existing-profile-id'}],
        })
        self.assertEqual(second.args[0], f'{klaviyo_service.KLAVIYO_API_URL}/events')
        attributes = second.kwargs['json']['data']['attributes']['profile']['data'].get('attributes', {})
        self.assertNotIn('first_name', attributes)
        self.assertNotIn('properties', attributes)
        self.assertNotIn('subscriptions', attributes)
        self.assertNotIn('vip@example.com', captured.getvalue())
        with self.app.app_context():
            signup = VipSignup.query.one()
            self.assertEqual(signup.klaviyo_subscription_status, 'accepted')
            self.assertEqual(signup.klaviyo_event_status, 'accepted')

    @patch('src.services.klaviyo_service.requests')
    def test_form_consent_never_overrides_preexisting_unsubscribed_state(self, requests):
        requests.get.return_value = self.profile_response(consent='UNSUBSCRIBED')
        response = self.submit()
        self.assertEqual(response.status_code, 502)
        requests.post.assert_not_called()
        requests.patch.assert_not_called()
        with self.app.app_context():
            signup = VipSignup.query.one()
            self.assertTrue(signup.consent_marketing)
            self.assertEqual(signup.klaviyo_subscription_status, 'ineligible')
            self.assertEqual(signup.klaviyo_event_status, 'not_sent')
            self.assertEqual(signup.klaviyo_error, 'VIP_EXISTING_NO_EMAIL_CONSENT')
        self.assertNotIn('UNSUBSCRIBED', str(response.get_json()))

    @patch('src.services.klaviyo_service.requests')
    def test_list_suppression_is_retained_without_any_remote_write(self, requests):
        requests.get.return_value = self.profile_response(list_suppressions=[{
            'list_id': 'newsletter-list-id', 'reason': 'USER_SUPPRESSED',
            'timestamp': '2026-11-01T09:00:00Z',
        }])
        response = self.submit()
        self.assertEqual(response.status_code, 502)
        requests.post.assert_not_called()
        requests.patch.assert_not_called()
        with self.app.app_context():
            signup = VipSignup.query.one()
            self.assertEqual(signup.klaviyo_subscription_status, 'ineligible')
            self.assertEqual(signup.klaviyo_error, 'VIP_EXISTING_LIST_SUPPRESSED')


if __name__ == '__main__':
    unittest.main()
