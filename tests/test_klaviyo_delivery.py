import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from flask import Flask

from src.models.klaviyo_delivery import KlaviyoDelivery
from src.models.user import db
from src.services.klaviyo_delivery_service import queue_and_send_event, retry_due_deliveries


class KlaviyoDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()
        self.env = patch.dict(os.environ, {"KLAVIYO_API_KEY": "test-key"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _response(self, status_code, text=""):
        response = Mock()
        response.status_code = status_code
        response.text = text
        return response

    @patch("src.services.klaviyo_delivery_service.requests.post")
    def test_accepted_event_is_persisted_and_idempotent(self, post):
        post.return_value = self._response(202)
        with self.app.app_context():
            self.assertTrue(
                queue_and_send_event(
                    "Mikels Newsletter Welcome",
                    "cliente@example.com",
                    {"CouponCode": "MIKELS-TEST"},
                    unique_id="welcome-test-1",
                    critical=True,
                )
            )
            # The same provider idempotency key cannot emit a duplicate event.
            self.assertTrue(
                queue_and_send_event(
                    "Mikels Newsletter Welcome",
                    "cliente@example.com",
                    {"CouponCode": "MIKELS-TEST"},
                    unique_id="welcome-test-1",
                    critical=True,
                )
            )
            delivery = KlaviyoDelivery.query.one()
            self.assertEqual(delivery.status, "accepted")
            self.assertEqual(delivery.attempts, 1)
            self.assertEqual(delivery.http_status, 202)
            self.assertIsNotNone(delivery.accepted_at)
            self.assertEqual(post.call_count, 1)

    @patch("src.services.klaviyo_delivery_service.requests.post")
    def test_failed_event_is_recorded_then_retried_to_acceptance(self, post):
        post.side_effect = [self._response(503, "provider unavailable"), self._response(202)]
        with self.app.app_context():
            self.assertFalse(
                queue_and_send_event(
                    "Mikels Placed Order",
                    "cliente@example.com",
                    {"OrderNumber": "1001"},
                    unique_id="order-test-1001",
                    critical=True,
                )
            )
            delivery = KlaviyoDelivery.query.one()
            self.assertEqual(delivery.status, "retrying")
            self.assertEqual(delivery.http_status, 503)
            self.assertEqual(delivery.attempts, 1)
            self.assertEqual(delivery.alert_status, "not_configured")
            self.assertIn("HTTP 503", delivery.failure_reason)
            delivery.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()

            summary = retry_due_deliveries()
            delivery = KlaviyoDelivery.query.one()
            self.assertEqual(summary, {"processed": 1, "accepted": 1, "failed": 0})
            self.assertEqual(delivery.status, "accepted")
            self.assertEqual(delivery.attempts, 2)
            self.assertEqual(post.call_count, 2)

    @patch("src.services.klaviyo_delivery_service.requests.post")
    def test_missing_klaviyo_key_is_queued_without_an_http_call(self, post):
        with patch.dict(os.environ, {"KLAVIYO_API_KEY": ""}, clear=False):
            with self.app.app_context():
                self.assertFalse(
                    queue_and_send_event(
                        "Mikels Contact Confirmation",
                        "cliente@example.com",
                        {"ContactName": "Cliente"},
                        unique_id="contact-test-1",
                        critical=True,
                    )
                )
                delivery = KlaviyoDelivery.query.one()
                self.assertEqual(delivery.status, "retrying")
                self.assertEqual(delivery.failure_reason, "KLAVIYO_API_KEY no configurada")
                self.assertEqual(post.call_count, 0)


if __name__ == "__main__":
    unittest.main()
