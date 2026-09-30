import unittest
from unittest.mock import patch

from flask import Flask

from src.models.coupon import Coupon
from src.models.newsletter_consent import NewsletterConsent
from src.models.user import db
from src.routes import newsletter_routes
from src.routes.newsletter_routes import newsletter_bp


class NewsletterConsentTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(newsletter_bp, url_prefix='/api/newsletter')

        with self.app.app_context():
            db.create_all()

        newsletter_routes._newsletter_rate_store.clear()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def popup_payload(self, **overrides):
        payload = {
            'email': 'cliente@example.com',
            'first_name': 'Cliente',
            'last_name': 'Prueba',
            'phone': '+34621144701',
            'source': 'popup',
            'privacy_policy_accepted': True,
            'whatsapp_marketing_accepted': False,
        }
        payload.update(overrides)
        return payload

    def test_popup_requires_explicit_privacy_consent(self):
        response = self.client.post(
            '/api/newsletter/subscribe',
            json=self.popup_payload(privacy_policy_accepted=False),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('política de privacidad', response.get_json()['error'])
        with self.app.app_context():
            self.assertEqual(NewsletterConsent.query.count(), 0)
            self.assertEqual(Coupon.query.count(), 0)

    @patch('src.routes.newsletter_routes.dispatch_newsletter_welcome')
    @patch('src.routes.newsletter_routes.dispatch_newsletter_subscription_notification')
    @patch('src.routes.newsletter_routes.dispatch_add_contact')
    def test_popup_records_both_choices_and_withholds_phone_without_whatsapp_consent(
        self,
        dispatch_contact,
        dispatch_notification,
        dispatch_welcome,
    ):
        dispatch_contact.return_value = {'success': True, 'id': 'contact-test'}

        response = self.client.post('/api/newsletter/subscribe', json=self.popup_payload())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        dispatch_contact.assert_called_once_with(
            'cliente@example.com',
            first_name='Cliente',
            last_name='Prueba',
            phone=None,
            source='popup',
        )
        dispatch_notification.assert_called_once()
        self.assertIsNone(dispatch_notification.call_args.kwargs['phone'])
        dispatch_welcome.assert_called_once()

        with self.app.app_context():
            consent = NewsletterConsent.query.one()
            self.assertEqual(consent.email, 'cliente@example.com')
            self.assertEqual(consent.phone, '+34621144701')
            self.assertTrue(consent.privacy_policy_accepted)
            self.assertFalse(consent.whatsapp_marketing_accepted)
            self.assertIsNotNone(consent.privacy_policy_recorded_at)
            self.assertIsNotNone(consent.whatsapp_marketing_recorded_at)
            self.assertIsNotNone(consent.created_at)
            self.assertIsNotNone(consent.updated_at)

    @patch('src.routes.newsletter_routes.dispatch_newsletter_welcome')
    @patch('src.routes.newsletter_routes.dispatch_newsletter_subscription_notification')
    @patch('src.routes.newsletter_routes.dispatch_add_contact')
    def test_popup_forwards_phone_only_with_explicit_whatsapp_consent(
        self,
        dispatch_contact,
        dispatch_notification,
        dispatch_welcome,
    ):
        dispatch_contact.return_value = {'success': True, 'id': 'contact-test'}

        response = self.client.post(
            '/api/newsletter/subscribe',
            json=self.popup_payload(
                email='whatsapp@example.com',
                whatsapp_marketing_accepted=True,
            ),
        )

        self.assertEqual(response.status_code, 200)
        dispatch_contact.assert_called_once_with(
            'whatsapp@example.com',
            first_name='Cliente',
            last_name='Prueba',
            phone='+34621144701',
            source='popup',
        )
        self.assertEqual(
            dispatch_notification.call_args.kwargs['phone'],
            '+34621144701',
        )
        dispatch_welcome.assert_called_once()

        with self.app.app_context():
            consent = NewsletterConsent.query.one()
            self.assertTrue(consent.whatsapp_marketing_accepted)


if __name__ == '__main__':
    unittest.main()
