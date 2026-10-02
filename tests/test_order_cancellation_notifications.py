import json
import os
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from flask import Flask

from src.models.order import Order
from src.models.user import db
from src.routes.stripe_routes import stripe_bp
from src.services import klaviyo_service
from src.services.order_cancellation import (
    build_cancellation_order_data,
    dispatch_full_refund_cancellation,
)
from src.services.order_receipt import build_receipt_snapshot


class OrderCancellationNotificationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(stripe_bp)
        with self.app.app_context():
            db.create_all()
            self.order = Order(
                order_number='MKL-CANCEL-TEST',
                customer_email='cliente@example.com',
                customer_name='Cliente de prueba',
                customer_phone='+34600000000',
                shipping_address='Calle de prueba 1',
                shipping_city='Lleida',
                shipping_postal_code='25001',
                shipping_country='España',
                items=[{
                    'name': 'Aceite Temprano 500 ml',
                    'quantity': 1,
                    'price': 19.90,
                    'reservation_only': True,
                }],
                subtotal=19.90,
                shipping_cost=0.0,
                total=19.90,
                payment_status='paid',
                order_status='processing',
                stripe_payment_intent_id='pi_cancel_test',
            )
            db.session.add(self.order)
            db.session.flush()
            self.order.receipt_snapshot = build_receipt_snapshot(self.order)
            db.session.commit()
            self.order_id = self.order.id
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _order(self):
        return db.session.get(Order, self.order_id)

    def test_reservation_cancellation_reuses_paid_receipt_and_exact_refund(self):
        with self.app.app_context():
            payload = build_cancellation_order_data(self._order(), Decimal('19.90'))

        receipt = payload['receipt']
        self.assertEqual(receipt['heading'], 'Reserva anulada')
        self.assertEqual(receipt['lines'][0]['amount_display'], '19,90 €')
        self.assertEqual(receipt['totals']['total_display'], '19,90 €')
        self.assertEqual(receipt['cancellation']['refunded_amount_display'], '19,90 €')
        self.assertEqual(
            receipt['cancellation']['message'],
            'Hemos anulado tu reserva y te hemos devuelto 19,90 €. Según tu banco, el ingreso puede tardar unos días en aparecer.',
        )
        self.assertEqual(receipt['next_steps'], [])

    @patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key'}, clear=False)
    @patch('src.services.klaviyo_service.requests.post')
    def test_cancellation_event_is_json_safe_and_idempotent(self, post):
        post.return_value = Mock(status_code=202, text='accepted')
        with self.app.app_context():
            payload = build_cancellation_order_data(self._order(), Decimal('19.90'))
            self.assertTrue(klaviyo_service.klaviyo_send_order_cancellation(payload))

        outbound = post.call_args.kwargs['json']
        json.dumps(outbound)
        attrs = outbound['data']['attributes']
        self.assertEqual(attrs['metric']['data']['attributes']['name'], 'Mikels Order Cancelled')
        self.assertEqual(attrs['unique_id'], 'order-cancelled-MKL-CANCEL-TEST')
        self.assertEqual(
            attrs['properties']['Receipt']['cancellation']['refunded_amount_display'],
            '19,90 €',
        )

    @patch('src.services.email_dispatcher.dispatch_order_cancellation')
    def test_full_refund_persists_once_and_webhook_retries_do_not_resend(self, cancellation_sender):
        cancellation_sender.return_value = (True, None)
        event = {
            'type': 'charge.refunded',
            'data': {
                'object': {
                    'payment_intent': 'pi_cancel_test',
                    'amount_refunded': 1990,
                    'amount': 1990,
                },
            },
        }
        with patch('src.routes.stripe_routes.stripe.Webhook.construct_event', return_value=event), \
             patch('src.services.stock_service.restock_fully_refunded_order'):
            first = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 'test'})
            second = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 'test'})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(cancellation_sender.call_count, 1)
        with self.app.app_context():
            order = self._order()
            self.assertEqual(order.payment_status, 'refunded')
            self.assertEqual(order.order_status, 'cancelled')
            self.assertEqual(order.cancellation_delivery_status, 'accepted')
            self.assertEqual(order.cancellation_refund_amount, 19.90)

    @patch('src.services.email_dispatcher.dispatch_order_delivery_alert')
    @patch('src.services.email_dispatcher.dispatch_order_cancellation')
    def test_failed_cancellation_event_records_failure_and_alerts_owner(self, cancellation_sender, alert_sender):
        cancellation_sender.return_value = (False, 'Klaviyo HTTP 503: unavailable')
        alert_sender.return_value = True
        with self.app.app_context():
            accepted, error, attempted = dispatch_full_refund_cancellation(self._order(), Decimal('19.90'))

            self.assertFalse(accepted)
            self.assertTrue(attempted)
            self.assertIn('HTTP 503', error)
            order = self._order()
            self.assertEqual(order.cancellation_delivery_status, 'failed')
            self.assertTrue(order.cancellation_alert_sent)
            alert_sender.assert_called_once()


if __name__ == '__main__':
    unittest.main()
