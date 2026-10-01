import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from src.models.order import Order
from src.models.stock import StockMovement, StockReservation
from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.stripe_routes import stripe_bp
from src.services.stock_service import (
    StockUnavailableError,
    consume_paid_reservation,
    reserve_checkout_stock,
    restock_fully_refunded_order,
)


class StockPaymentIntegrityTests(unittest.TestCase):
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
            self.product = WebProduct(
                name='Producto limitado',
                slug='producto-limitado',
                sku='MIKLIMITADO',
                price=17.15,
                category='Prueba',
                stock=2,
                active=True,
                visible_in_store=True,
            )
            db.session.add(self.product)
            db.session.commit()
            self.product_id = self.product.id
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_reservation_blocks_another_checkout_from_overselling(self):
        with self.app.app_context():
            item = {'id': self.product_id, 'name': 'Producto limitado', 'quantity': 2}
            reserve_checkout_stock([item], 'first', datetime.utcnow() + timedelta(minutes=30))
            db.session.commit()
            with self.assertRaises(StockUnavailableError):
                reserve_checkout_stock([{'id': self.product_id, 'name': 'Producto limitado', 'quantity': 1}], 'second')
            self.assertEqual(WebProduct.query.get(self.product_id).stock, 2)
            self.assertEqual(StockReservation.query.filter_by(status='active').count(), 1)

    def test_paid_capture_deducts_once_and_full_refund_restores_once(self):
        with self.app.app_context():
            reserve_checkout_stock(
                [{'id': self.product_id, 'name': 'Producto limitado', 'quantity': 1}],
                'checkout-token',
                datetime.utcnow() + timedelta(minutes=30),
            )
            order = Order(
                order_number='MKL-STOCK-TEST',
                customer_email='cliente@example.com',
                customer_name='Cliente',
                shipping_address='Calle 1',
                shipping_city='Lleida',
                shipping_postal_code='25001',
                shipping_country='España',
                items=[],
                subtotal=17.15,
                total=17.15,
                payment_status='paid',
            )
            db.session.add(order)
            db.session.flush()
            consume_paid_reservation('checkout-token', order.id, 'cs_test_stock')
            db.session.commit()
            self.assertEqual(WebProduct.query.get(self.product_id).stock, 1)
            self.assertEqual(StockMovement.query.filter_by(reason='payment_capture').count(), 1)

            consume_paid_reservation('checkout-token', order.id, 'cs_test_stock')
            db.session.commit()
            self.assertEqual(WebProduct.query.get(self.product_id).stock, 1)

            restock_fully_refunded_order(order.id, 'pi_test_refund')
            db.session.commit()
            self.assertEqual(WebProduct.query.get(self.product_id).stock, 2)
            self.assertEqual(StockMovement.query.filter_by(reason='refund_restock').count(), 1)

            restock_fully_refunded_order(order.id, 'pi_test_refund')
            db.session.commit()
            self.assertEqual(WebProduct.query.get(self.product_id).stock, 2)
            self.assertEqual(StockMovement.query.filter_by(reason='refund_restock').count(), 1)

    @patch('src.routes.stripe_routes.stripe.checkout.Session.retrieve')
    def test_paid_session_status_exposes_customer_confirmation_details(self, retrieve):
        retrieve.return_value = SimpleNamespace(
            status='complete',
            payment_status='paid',
            customer_details=SimpleNamespace(email='cliente@example.com'),
            metadata={},
        )
        with self.app.app_context():
            db.session.add(Order(
                order_number='MKL-CONFIRMATION-TEST',
                customer_email='cliente@example.com',
                customer_name='Cliente',
                shipping_address='Calle 1',
                shipping_city='Lleida',
                shipping_postal_code='25001',
                shipping_country='España',
                items=[{'name': 'Producto limitado', 'quantity': 1, 'price': 17.15}],
                subtotal=17.15,
                shipping_cost=0.0,
                total=17.15,
                currency='EUR',
                stripe_checkout_session_id='cs_test_confirmation',
                payment_status='paid',
                email_sent=True,
            ))
            db.session.commit()

        response = self.client.get('/api/stripe/session-status/cs_test_confirmation')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['order']['order_number'], 'MKL-CONFIRMATION-TEST')
        self.assertEqual(data['order']['items'][0]['price'], 17.15)
        self.assertEqual(data['order']['shipping_address'], 'Calle 1')
        self.assertEqual(data['customer_email'], 'cliente@example.com')
        self.assertTrue(data['order']['confirmation_sent'])

    @patch('src.services.email_dispatcher.dispatch_post_purchase_event')
    @patch('src.routes.stripe_routes.dispatch_order_confirmation')
    @patch('src.routes.stripe_routes.dispatch_order_notification')
    @patch('src.routes.stripe_routes.notify_new_order')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items')
    @patch('src.routes.stripe_routes.stripe.Webhook.construct_event')
    def test_paid_webhook_consumes_reserved_stock_exactly_once(
        self,
        construct_event,
        list_line_items,
        notify_order,
        dispatch_notification,
        dispatch_confirmation,
        dispatch_post_purchase,
    ):
        with self.app.app_context():
            reserve_checkout_stock(
                [{'id': self.product_id, 'name': 'Producto limitado', 'quantity': 1}],
                'checkout-token-webhook',
                datetime.utcnow() + timedelta(minutes=30),
            )
            db.session.commit()

        construct_event.return_value = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_test_stock_paid',
                    'mode': 'payment',
                    'amount_total': 1715,
                    'payment_intent': 'pi_test_stock_paid',
                    'customer_email': 'cliente@example.com',
                    'customer_details': {'email': 'cliente@example.com', 'phone': ''},
                    'shipping_details': {
                        'name': 'Cliente',
                        'address': {
                            'line1': 'Calle 1',
                            'city': 'Lleida',
                            'postal_code': '25001',
                            'country': 'ES',
                        },
                    },
                    'metadata': {
                        'order_number': 'MKL-WEBHOOK-STOCK',
                        'customer_name': 'Cliente',
                        'subtotal': '17.15',
                        'discount_amount': '0',
                        'discount_code': '',
                        'needs_invoice': 'False',
                        'stock_checkout_token': 'checkout-token-webhook',
                    },
                },
            },
        }
        list_line_items.return_value = SimpleNamespace(data=[SimpleNamespace(
            amount_total=1715,
            quantity=1,
            description='Producto limitado',
            price=SimpleNamespace(product=SimpleNamespace(metadata={
                'sku': 'MIKLIMITADO',
                'slug': 'producto-limitado',
            })),
        )])

        response = self.client.post(
            '/api/stripe/webhook',
            data=b'{}',
            headers={'Stripe-Signature': 'test-signature'},
        )
        self.assertEqual(response.status_code, 200)
        # A retry from Stripe must not consume a second unit.
        retry = self.client.post(
            '/api/stripe/webhook',
            data=b'{}',
            headers={'Stripe-Signature': 'test-signature'},
        )
        self.assertEqual(retry.status_code, 200)

        with self.app.app_context():
            self.assertEqual(WebProduct.query.get(self.product_id).stock, 1)
            self.assertEqual(StockMovement.query.filter_by(reason='payment_capture').count(), 1)
            reservation = StockReservation.query.filter_by(checkout_token='checkout-token-webhook').one()
            self.assertEqual(reservation.status, 'consumed')
        notify_order.assert_called_once()
        dispatch_notification.assert_called_once()
        dispatch_confirmation.assert_called_once()
        dispatch_post_purchase.assert_called_once()


if __name__ == '__main__':
    unittest.main()
