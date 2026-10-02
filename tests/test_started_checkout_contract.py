import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from src.models.abandoned_cart import AbandonedCart
from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.abandoned_cart_routes import abandoned_cart_bp
from src.routes.stripe_routes import stripe_bp
from src.services import klaviyo_service


class StartedCheckoutContractTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(stripe_bp)
        self.app.register_blueprint(abandoned_cart_bp, url_prefix='/api/abandoned-cart')

        with self.app.app_context():
            db.create_all()
            db.session.add(WebProduct(
                id=1,
                name='Producto de prueba',
                slug='producto-prueba',
                sku='TEST-CHECKOUT-01',
                price=19.90,
                category='Conservas',
                stock=10,
            ))
            db.session.commit()
        self.client = self.app.test_client()
        self.holded_products = patch(
            'src.services.holded_service.holded_get_products',
            return_value=[{'sku': 'TEST-CHECKOUT-01'}],
        )
        self.tax_rules = patch(
            'src.services.order_tax_snapshot.build_tax_rule_snapshot',
            return_value={'TEST-CHECKOUT-01': {'kind': 'single', 'rate': '0.10'}},
        )
        self.holded_products.start()
        self.tax_rules.start()

    def tearDown(self):
        self.holded_products.stop()
        self.tax_rules.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    @staticmethod
    def item():
        return {
            'id': 1,
            'name': 'Producto de prueba',
            'slug': 'producto-prueba',
            'image': 'https://www.mikels.es/producto-prueba.jpg',
            'price': 19.90,
            'quantity': 1,
        }

    @patch('src.services.klaviyo_service.send_klaviyo_event')
    def test_both_supported_function_contracts_emit_the_flow_metric(self, send_event):
        send_event.return_value = {'success': True}

        legacy_result = klaviyo_service.klaviyo_track_started_checkout({
            'customer_email': 'cliente@example.com',
            'customer_name': 'Cliente Prueba',
            'items': [self.item()],
            'subtotal': 19.90,
            'total': 19.90,
            'checkout_url': 'https://www.mikels.es/checkout',
            'order_number': 'MKL-TEST-001',
        })
        dispatcher_result = klaviyo_service.klaviyo_track_started_checkout(
            email='cliente@example.com',
            customer_name='Cliente Prueba',
            items=[self.item()],
            total=19.90,
            checkout_url='https://www.mikels.es/recuperar-carrito/token-test',
            items_html='<p>Producto de prueba</p>',
            cart_token='token-test',
        )

        self.assertEqual(legacy_result, {'success': True})
        self.assertEqual(dispatcher_result, {'success': True})
        self.assertEqual(send_event.call_count, 2)
        for call in send_event.call_args_list:
            self.assertEqual(call.kwargs['metric_name'], 'Mikels Started Checkout')
            self.assertEqual(call.kwargs['profile_email'], 'cliente@example.com')
            self.assertEqual(call.kwargs['properties']['Items'][0]['ProductName'], 'Producto de prueba')

    @patch('src.routes.abandoned_cart_routes.dispatch_started_checkout_event')
    def test_abandoned_cart_endpoint_dispatches_canonical_contract(self, dispatch_event):
        response = self.client.post('/api/abandoned-cart/', json={
            'email': 'cliente@example.com',
            'customer_name': 'Cliente Prueba',
            'items': [self.item()],
            'total': 19.90,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        dispatch_event.assert_called_once()
        kwargs = dispatch_event.call_args.kwargs
        self.assertEqual(kwargs['email'], 'cliente@example.com')
        self.assertEqual(kwargs['customer_name'], 'Cliente Prueba')
        self.assertEqual(kwargs['items'][0]['slug'], 'producto-prueba')
        self.assertEqual(kwargs['total'], 19.90)
        self.assertIn(kwargs['cart_token'], kwargs['checkout_url'])
        self.assertIn('Producto de prueba', kwargs['items_html'])
        with self.app.app_context():
            self.assertEqual(AbandonedCart.query.count(), 1)

    @patch('src.routes.stripe_routes.dispatch_started_checkout_event')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_stripe_checkout_path_dispatches_canonical_contract(self, create_session, dispatch_event):
        create_session.return_value = SimpleNamespace(id='cs_test_started_checkout', url='https://checkout.stripe.test/session')
        previous_frontend_url = os.environ.get('FRONTEND_URL')
        os.environ['FRONTEND_URL'] = 'https://www.mikels.es'
        try:
            response = self.client.post('/api/stripe/create-checkout-session', json={
                'items': [self.item()],
                'customer_info': {
                    'email': 'cliente@example.com',
                    'name': 'Cliente Prueba',
                    'phone': '+34621144701',
                    'address': 'Calle de prueba 1',
                    'city': 'Lleida',
                    'postal_code': '25003',
                },
            })
        finally:
            if previous_frontend_url is None:
                os.environ.pop('FRONTEND_URL', None)
            else:
                os.environ['FRONTEND_URL'] = previous_frontend_url

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['sessionId'], 'cs_test_started_checkout')
        dispatch_event.assert_called_once()
        kwargs = dispatch_event.call_args.kwargs
        self.assertEqual(kwargs['email'], 'cliente@example.com')
        self.assertEqual(kwargs['customer_name'], 'Cliente Prueba')
        self.assertEqual(kwargs['items'][0]['sku'], 'TEST-CHECKOUT-01')
        self.assertEqual(kwargs['total'], 19.90)
        self.assertEqual(kwargs['checkout_url'], 'https://www.mikels.es/checkout')
        self.assertTrue(kwargs['cart_token'].startswith('MKL-'))

    def test_tracker_has_one_definition(self):
        source_path = os.path.join(
            os.path.dirname(__file__), '..', 'src', 'services', 'klaviyo_service.py'
        )
        with open(source_path, encoding='utf-8') as source_file:
            source = source_file.read()
        self.assertEqual(source.count('def klaviyo_track_started_checkout('), 1)


if __name__ == '__main__':
    unittest.main()
