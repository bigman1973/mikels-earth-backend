import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from flask import Flask

from src.models.coupon import Coupon
from src.models.newsletter_consent import NewsletterConsent
from src.models.newsletter_subscriber import NewsletterSubscriber
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
        self.turnstile_patcher = patch('src.routes.newsletter_routes.verify_turnstile')
        self.turnstile = self.turnstile_patcher.start()
        self.turnstile.return_value = Mock(accepted=True, reason='accepted')
        self.client = self.app.test_client()

    def tearDown(self):
        self.turnstile_patcher.stop()
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
            self.assertEqual(NewsletterSubscriber.query.count(), 0)
            self.assertEqual(Coupon.query.count(), 0)

    @patch('src.routes.newsletter_routes.dispatch_newsletter_welcome')
    @patch('src.routes.newsletter_routes.dispatch_newsletter_subscription_notification')
    @patch('src.routes.newsletter_routes.dispatch_add_contact')
    def test_first_popup_records_choices_and_creates_expiring_single_use_coupon(
        self,
        dispatch_contact,
        dispatch_notification,
        dispatch_welcome,
    ):
        dispatch_contact.return_value = {'success': True, 'id': 'contact-test'}

        response = self.client.post('/api/newsletter/subscribe', json=self.popup_payload())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertTrue(response.get_json()['coupon_code'].startswith('MIKELS-'))
        dispatch_contact.assert_called_once_with(
            'cliente@example.com',
            first_name='Cliente',
            last_name='Prueba',
            phone=None,
            source='popup',
            whatsapp_marketing_accepted=False,
            subscribe_email=True,
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

            subscriber = NewsletterSubscriber.query.one()
            coupon = Coupon.query.one()
            self.assertEqual(subscriber.welcome_coupon_id, coupon.id)
            self.assertEqual(coupon.email, 'cliente@example.com')
            self.assertEqual(coupon.max_uses, 1)
            self.assertEqual(coupon.max_uses_per_customer, 1)
            self.assertTrue(coupon.active)
            self.assertFalse(coupon.used)
            self.assertGreater(coupon.expires_at, datetime.utcnow() + timedelta(days=29))
            self.assertLess(coupon.expires_at, datetime.utcnow() + timedelta(days=31))

    @patch('src.routes.newsletter_routes.dispatch_newsletter_welcome')
    @patch('src.routes.newsletter_routes.dispatch_newsletter_subscription_notification')
    @patch('src.routes.newsletter_routes.dispatch_add_contact')
    def test_repeat_subscription_keeps_consent_history_without_new_coupon_or_welcome(
        self,
        dispatch_contact,
        dispatch_notification,
        dispatch_welcome,
    ):
        dispatch_contact.return_value = {'success': True, 'id': 'contact-test'}
        first = self.client.post('/api/newsletter/subscribe', json=self.popup_payload())
        second = self.client.post(
            '/api/newsletter/subscribe',
            json=self.popup_payload(whatsapp_marketing_accepted=True),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()['success'])
        self.assertTrue(second.get_json()['already_subscribed'])
        self.assertEqual(
            second.get_json()['message'],
            'Ya estás suscrito. Si no encuentras tu cupón, escríbenos.',
        )
        self.assertEqual(dispatch_contact.call_count, 2)
        self.assertFalse(dispatch_contact.call_args.kwargs['subscribe_email'])
        self.assertTrue(dispatch_contact.call_args.kwargs['whatsapp_marketing_accepted'])
        self.assertEqual(dispatch_contact.call_args.kwargs['phone'], '+34621144701')
        dispatch_notification.assert_called_once()
        dispatch_welcome.assert_called_once()

        with self.app.app_context():
            self.assertEqual(NewsletterConsent.query.count(), 2)
            self.assertEqual(NewsletterSubscriber.query.count(), 1)
            self.assertEqual(Coupon.query.count(), 1)
            newest = NewsletterConsent.query.order_by(NewsletterConsent.id.desc()).first()
            self.assertTrue(newest.whatsapp_marketing_accepted)

    @patch('src.routes.newsletter_routes.dispatch_newsletter_welcome')
    @patch('src.routes.newsletter_routes.dispatch_newsletter_subscription_notification')
    @patch('src.routes.newsletter_routes.dispatch_add_contact')
    def test_gmail_plus_and_dot_aliases_cannot_create_a_second_coupon(
        self,
        dispatch_contact,
        dispatch_notification,
        dispatch_welcome,
    ):
        dispatch_contact.return_value = {'success': True, 'id': 'contact-test'}
        self.client.post(
            '/api/newsletter/subscribe',
            json=self.popup_payload(email='jor.di+newsletter@gmail.com'),
        )
        response = self.client.post(
            '/api/newsletter/subscribe',
            json=self.popup_payload(email='jordi@gmail.com'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['already_subscribed'])
        self.assertEqual(dispatch_notification.call_count, 1)
        self.assertEqual(dispatch_welcome.call_count, 1)
        with self.app.app_context():
            self.assertEqual(NewsletterConsent.query.count(), 2)
            self.assertEqual(NewsletterSubscriber.query.count(), 1)
            self.assertEqual(Coupon.query.count(), 1)

    @patch('src.routes.newsletter_routes.dispatch_newsletter_welcome')
    @patch('src.routes.newsletter_routes.dispatch_newsletter_subscription_notification')
    @patch('src.routes.newsletter_routes.dispatch_add_contact')
    def test_historical_gmail_welcome_coupon_is_not_reissued_to_an_alias(
        self,
        dispatch_contact,
        dispatch_notification,
        dispatch_welcome,
    ):
        dispatch_contact.return_value = {'success': True, 'id': 'contact-test'}
        with self.app.app_context():
            db.session.add(Coupon(
                code='MIKELS-LEGACY01',
                email='jor.di@gmail.com',
                discount_type='percentage',
                discount_value=10,
                max_uses=1,
                active=True,
            ))
            db.session.commit()

        response = self.client.post(
            '/api/newsletter/subscribe',
            json=self.popup_payload(email='jordi+new@gmail.com'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['already_subscribed'])
        dispatch_notification.assert_not_called()
        dispatch_welcome.assert_not_called()
        with self.app.app_context():
            self.assertEqual(Coupon.query.count(), 1)
            self.assertEqual(NewsletterSubscriber.query.one().welcome_coupon.code, 'MIKELS-LEGACY01')

    def test_invalid_email_creates_no_consent_or_coupon(self):
        response = self.client.post(
            '/api/newsletter/subscribe',
            json=self.popup_payload(email='not-an-email'),
        )

        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertEqual(NewsletterConsent.query.count(), 0)
            self.assertEqual(NewsletterSubscriber.query.count(), 0)
            self.assertEqual(Coupon.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
