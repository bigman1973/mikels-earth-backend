import unittest
from unittest.mock import patch

from flask import Flask

from src.models.user import db
from src.models.vip_signup import VipSignup
from src.routes import vip_routes
from src.routes.vip_routes import vip_bp


class VipSignupTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(vip_bp, url_prefix='/api/vip')
        with self.app.app_context():
            db.create_all()
        vip_routes._vip_rate_store.clear()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def payload(self, **overrides):
        data = {
            'email': 'vip@example.com',
            'first_name': 'Marisa',
            'marketing_consent': True,
        }
        data.update(overrides)
        return data

    def test_requires_explicit_marketing_consent_before_persisting(self):
        response = self.client.post('/api/vip/signup', json=self.payload(marketing_consent=False))

        self.assertEqual(response.status_code, 400)
        self.assertIn('comunicaciones comerciales', response.get_json()['error'])
        with self.app.app_context():
            self.assertEqual(VipSignup.query.count(), 0)

    @patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None))
    @patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None))
    def test_records_consent_ip_and_sends_only_the_vip_klaviyo_actions(self, subscribe, event):
        response = self.client.post(
            '/api/vip/signup',
            json=self.payload(),
            headers={'X-Forwarded-For': '198.51.100.42, 10.0.0.1'},
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()['success'])
        self.assertIsInstance(response.get_json()['signup_id'], int)
        subscribe.assert_called_once()
        event.assert_called_once()
        with self.app.app_context():
            signup = VipSignup.query.one()
            self.assertEqual(signup.email, 'vip@example.com')
            self.assertEqual(signup.first_name, 'Marisa')
            self.assertTrue(signup.consent_marketing)
            self.assertEqual(signup.source_ip, '198.51.100.42')
            self.assertIsNotNone(signup.consented_at)
            self.assertEqual(signup.klaviyo_subscription_status, 'accepted')
            self.assertEqual(signup.klaviyo_event_status, 'accepted')
            self.assertIsNone(signup.klaviyo_error)

    @patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(False, 'Klaviyo HTTP 500'))
    @patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None))
    def test_does_not_report_success_when_the_required_event_fails(self, subscribe, event):
        response = self.client.post('/api/vip/signup', json=self.payload())

        self.assertEqual(response.status_code, 502)
        with self.app.app_context():
            signup = VipSignup.query.one()
            self.assertEqual(signup.klaviyo_subscription_status, 'accepted')
            self.assertEqual(signup.klaviyo_event_status, 'failed')
            self.assertIn('HTTP 500', signup.klaviyo_error)

    @patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(False, 'Klaviyo HTTP 403'))
    def test_keeps_a_failed_delivery_audit_without_sending_the_event(self, subscribe):
        response = self.client.post('/api/vip/signup', json=self.payload())

        self.assertEqual(response.status_code, 502)
        with self.app.app_context():
            signup = VipSignup.query.one()
            self.assertEqual(signup.klaviyo_subscription_status, 'failed')
            self.assertEqual(signup.klaviyo_event_status, 'not_sent')
            self.assertIn('HTTP 403', signup.klaviyo_error)


if __name__ == '__main__':
    unittest.main()
