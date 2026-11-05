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
            self.assertEqual(signup.source_ip, 'unverified')
            self.assertEqual(signup.source, 'vip_black_friday_2026:ip_unverified:unavailable')
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

    def test_ineligible_profiles_are_counted_separately_without_sending_events(self):
        expected_response = None
        for code in sorted(vip_routes._VIP_INELIGIBLE_ERRORS):
            with self.subTest(reason=code), \
                    patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(False, code)), \
                    patch('src.routes.vip_routes.klaviyo_track_vip_signup') as event:
                response = self.client.post('/api/vip/signup', json=self.payload())
                self.assertEqual(response.status_code, 502)
                event.assert_not_called()
                public = response.get_json()
                self.assertNotIn('success', public)
                self.assertNotIn(code, str(public))
                self.assertNotIn('vip@example.com', str(public))
                if expected_response is None:
                    expected_response = public
                self.assertEqual(public, expected_response)
                with self.app.app_context():
                    signup = VipSignup.query.order_by(VipSignup.id.desc()).first()
                    self.assertEqual(signup.klaviyo_subscription_status, 'ineligible')
                    self.assertEqual(signup.klaviyo_event_status, 'not_sent')
                    self.assertEqual(signup.klaviyo_error, code)
                    self.assertTrue(signup.consent_marketing)
        with self.app.app_context():
            self.assertEqual(VipSignup.query.filter_by(klaviyo_subscription_status='ineligible').count(), 3)

    @patch('src.routes.vip_routes.klaviyo_track_vip_signup')
    @patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(False, 'VIP_PROFILE_LOOKUP_HTTP_503'))
    def test_unverified_consent_is_a_technical_failure_not_a_confirmed_ineligible_profile(self, subscribe, event):
        response = self.client.post('/api/vip/signup', json=self.payload())
        self.assertEqual(response.status_code, 502)
        event.assert_not_called()
        with self.app.app_context():
            signup = VipSignup.query.one()
            self.assertEqual(signup.klaviyo_subscription_status, 'failed')
            self.assertEqual(signup.klaviyo_event_status, 'not_sent')
            self.assertEqual(signup.klaviyo_error, 'VIP_PROFILE_LOOKUP_HTTP_503')


if __name__ == '__main__':
    unittest.main()
