import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from src.models.order import Order
from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.stripe_routes import stripe_bp


class OrderLineSkuTests(unittest.TestCase):
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
            db.session.add_all([
                WebProduct(
                    name='Aceite Temprano',
                    slug='aceite-temprano-sin-filtrar',
                    sku='MIKVET500',
                    price=17.15,
                    category='Aceites',
                    active=True,
                    visible_in_store=True,
                ),
                WebProduct(
                    name='Estuche Temprano',
                    slug='estuche-regalo-temprano',
                    sku='MIKESTTEM',
                    price=5.00,
                    category='Packs',
                    active=True,
                    visible_in_store=False,
                ),
                WebProduct(
                    name='Estuche Ecológico',
                    slug='estuche-regalo-ecologico',
                    sku='MIKESTBIO',
                    price=5.00,
                    category='Packs',
                    active=True,
                    visible_in_store=False,
                ),
                WebProduct(
                    name='Estuche Virgen Extra',
                    slug='estuche-regalo-virgen-extra',
                    sku='MIKESTEV',
                    price=5.00,
                    category='Packs',
                    active=True,
                    visible_in_store=False,
                ),
            ])
            db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_public_product_payload_exposes_sku(self):
        with self.app.app_context():
            product = WebProduct.query.filter_by(
                slug='aceite-temprano-sin-filtrar'
            ).one()
            self.assertEqual(product.to_frontend_dict()['sku'], 'MIKVET500')

    @patch('src.routes.stripe_routes.dispatch_started_checkout_event')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_checkout_uses_database_sku_not_browser_sku(
        self,
        create_session,
        dispatch_checkout,
    ):
        create_session.return_value = SimpleNamespace(
            id='cs_test_sku',
            url='https://checkout.stripe.test/session',
        )

        response = self.client.post('/api/stripe/create-checkout-session', json={
            'items': [{
                'id': 1,
                'slug': 'aceite-temprano-sin-filtrar',
                'sku': 'SKU-FALSO-DEL-NAVEGADOR',
                'name': 'Aceite Temprano',
                'price': 17.15,
                'quantity': 2,
                'weight': '500 ml',
            }],
            'customer_info': {
                'email': 'cliente@example.com',
                'name': 'Cliente de prueba',
                'phone': '',
                'address': 'Calle de prueba 1',
                'city': 'Alcarràs',
                'postal_code': '25180',
                'country': 'España',
            },
        })

        self.assertEqual(response.status_code, 200)
        session_params = create_session.call_args.kwargs
        metadata = session_params['line_items'][0]['price_data']['product_data']['metadata']
        self.assertEqual(metadata['sku'], 'MIKVET500')
        self.assertEqual(metadata['slug'], 'aceite-temprano-sin-filtrar')
        dispatch_checkout.assert_called_once()

    @patch('src.routes.stripe_routes.dispatch_started_checkout_event')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_checkout_uses_specific_database_sku_for_each_hidden_box(
        self,
        create_session,
        dispatch_checkout,
    ):
        create_session.return_value = SimpleNamespace(
            id='cs_test_box_sku',
            url='https://checkout.stripe.test/session',
        )
        cases = [
            ('estuche-regalo-temprano', 'MIKESTTEM'),
            ('estuche-regalo-ecologico', 'MIKESTBIO'),
            ('estuche-regalo-virgen-extra', 'MIKESTEV'),
        ]

        for slug, expected_sku in cases:
            with self.subTest(slug=slug):
                create_session.reset_mock()
                response = self.client.post('/api/stripe/create-checkout-session', json={
                    'items': [{
                        'slug': slug,
                        'sku': 'MIKEST01',
                        'name': 'Estuche de Regalo Premium',
                        'price': 5.00,
                        'quantity': 1,
                        'weight': '50 g',
                    }],
                    'customer_info': {
                        'email': 'cliente@example.com',
                        'name': 'Cliente de prueba',
                        'phone': '',
                        'address': 'Calle de prueba 1',
                        'city': 'Alcarràs',
                        'postal_code': '25180',
                        'country': 'España',
                    },
                })

                self.assertEqual(response.status_code, 200)
                session_params = create_session.call_args.kwargs
                metadata = session_params['line_items'][0]['price_data']['product_data']['metadata']
                self.assertEqual(metadata['sku'], expected_sku)
                self.assertEqual(metadata['slug'], slug)

        self.assertEqual(dispatch_checkout.call_count, 3)

    @patch('src.services.email_dispatcher.dispatch_post_purchase_event')
    @patch('src.routes.stripe_routes.dispatch_order_confirmation')
    @patch('src.routes.stripe_routes.dispatch_order_notification')
    @patch('src.routes.stripe_routes.notify_new_order')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items')
    @patch('src.routes.stripe_routes.stripe.Webhook.construct_event')
    def test_webhook_persists_sku_from_stripe_product_metadata(
        self,
        construct_event,
        list_line_items,
        notify_order,
        dispatch_notification,
        dispatch_confirmation,
        dispatch_post_purchase,
    ):
        construct_event.return_value = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_test_completed',
                    'mode': 'payment',
                    'amount_total': 3430,
                    'payment_intent': 'pi_test',
                    'customer_email': 'cliente@example.com',
                    'customer_details': {
                        'email': 'cliente@example.com',
                        'phone': '',
                    },
                    'shipping_details': {
                        'name': 'Cliente de prueba',
                        'address': {
                            'line1': 'Calle de prueba 1',
                            'city': 'Alcarràs',
                            'postal_code': '25180',
                            'country': 'ES',
                        },
                    },
                    'metadata': {
                        'order_number': 'MKL-TEST-SKU',
                        'customer_name': 'Cliente de prueba',
                        'subtotal': '34.30',
                        'discount_amount': '0',
                        'discount_code': '',
                        'needs_invoice': 'False',
                    },
                },
            },
        }
        list_line_items.return_value = SimpleNamespace(data=[SimpleNamespace(
            amount_total=3430,
            quantity=2,
            description='Aceite Temprano',
            price=SimpleNamespace(product=SimpleNamespace(metadata={
                'sku': 'MIKVET500',
                'slug': 'aceite-temprano-sin-filtrar',
            })),
        )])

        response = self.client.post(
            '/api/stripe/webhook',
            data=b'{}',
            headers={'Stripe-Signature': 'test-signature'},
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            order = Order.query.filter_by(order_number='MKL-TEST-SKU').one()
            self.assertEqual(order.items[0]['sku'], 'MIKVET500')
            self.assertEqual(order.items[0]['slug'], 'aceite-temprano-sin-filtrar')

        notify_order.assert_called_once()
        dispatch_notification.assert_called_once()
        dispatch_confirmation.assert_called_once()
        dispatch_post_purchase.assert_called_once()


if __name__ == '__main__':
    unittest.main()
