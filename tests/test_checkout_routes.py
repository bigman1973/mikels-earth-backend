import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from src.routes.stripe_routes import (
    create_checkout_quote,
    create_checkout_session,
    create_subscription_checkout,
)


class CheckoutRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.customer = {
            'email': 'buyer@example.com',
            'name': 'Buyer',
            'phone': '600000000',
            'address': 'Street 1',
            'city': 'Mahón',
            'postal_code': '07701',
            'country': 'España',
        }

    @patch('src.routes.stripe_routes.catalog_version', return_value='catalog-v1')
    @patch('src.routes.stripe_routes.price_coupon', return_value=(None, 0))
    @patch('src.routes.stripe_routes.price_one_time_item')
    def test_quote_replaces_cached_price_without_creating_stripe(
        self,
        price_item,
        _price_coupon,
        _catalog_version,
    ):
        product = SimpleNamespace(updated_at=None)
        price_item.return_value = {
            'product': product,
            'product_id': 10,
            'sku': 'ACEITE-5L',
            'slug': 'aceite-5l',
            'name': 'Aceite 5L',
            'image': '/images/aceite.jpg',
            'weight': '5 L',
            'quantity': 3,
            'base_unit_amount': 4300,
            'unit_amount': 3909,
            'discount_percent': 9.09,
            'volume_discount': {'minQuantity': 3, 'discount': 9.09},
            'tiered_discount': None,
            'line_total': 11727,
        }
        payload = {
            'items': [{'id': 10, 'price': 20.0, 'quantity': 3}],
            'customer_info': {'email': 'buyer@example.com'},
            'locale': 'es',
        }

        with self.app.test_request_context(json=payload):
            response = create_checkout_quote()

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['items'][0]['unit_amount'], 3909)
        self.assertEqual(data['total'], 11727)
        self.assertEqual(data['pricing_source'], 'server')

    @patch('src.routes.stripe_routes.dispatch_started_checkout_event')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    @patch('src.routes.stripe_routes.catalog_version', return_value='catalog-v1')
    @patch('src.routes.stripe_routes.price_coupon', return_value=(None, 0))
    @patch('src.routes.stripe_routes.price_one_time_item')
    def test_checkout_uses_canonical_unit_amount(
        self,
        price_item,
        _price_coupon,
        _catalog_version,
        create_session,
        _dispatch,
    ):
        product = SimpleNamespace(updated_at=None)
        price_item.return_value = {
            'product': product,
            'product_id': 10,
            'sku': 'ACEITE-5L',
            'slug': 'aceite-5l',
            'name': 'Aceite 5L',
            'weight': '5 L',
            'quantity': 1,
            'base_unit_amount': 4300,
            'unit_amount': 4300,
            'discount_percent': 0,
            'line_total': 4300,
        }
        create_session.return_value = SimpleNamespace(id='cs_test', url='https://checkout.stripe.test/session')
        payload = {
            'items': [{
                'id': 10,
                'slug': 'aceite-5l',
                'price': 20.0,
                'finalPrice': 20.0,
                'quantity': 1,
            }],
            'customer_info': self.customer,
            'discount_amount': 23.0,
            'locale': 'es',
        }

        with self.app.test_request_context(
            json=payload,
            headers={'X-Checkout-Pricing-Version': '2'},
        ):
            response = create_checkout_session()

        self.assertEqual(response.status_code, 200)
        session_params = create_session.call_args.kwargs
        self.assertEqual(session_params['line_items'][0]['price_data']['unit_amount'], 4300)
        self.assertEqual(session_params['metadata']['pricing_source'], 'server')
        self.assertEqual(session_params['metadata']['total'], '43.00')

    @patch('src.routes.stripe_routes.catalog_version', return_value='catalog-v1')
    @patch('src.routes.stripe_routes.price_coupon', return_value=(None, 0))
    @patch('src.routes.stripe_routes.price_one_time_item')
    def test_legacy_client_gets_price_update_instead_of_stripe(
        self,
        price_item,
        _price_coupon,
        _catalog_version,
    ):
        product = SimpleNamespace(updated_at=None)
        price_item.return_value = {
            'product': product,
            'product_id': 10,
            'sku': 'ACEITE-5L',
            'slug': 'aceite-5l',
            'name': 'Aceite 5L',
            'weight': '5 L',
            'quantity': 1,
            'base_unit_amount': 4300,
            'unit_amount': 4300,
            'discount_percent': 0,
            'line_total': 4300,
        }
        payload = {
            'items': [{'id': 10, 'slug': 'aceite-5l', 'price': 20.0, 'quantity': 1}],
            'customer_info': self.customer,
        }

        with self.app.test_request_context(json=payload):
            response, status = create_checkout_session()

        self.assertEqual(status, 409)
        data = response.get_json()
        self.assertEqual(data['error'], 'PRICE_MISMATCH')
        self.assertEqual(data['price_updates'][0]['current_price'], 43.0)

    @patch('src.routes.stripe_routes.stripe.Price.create')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    @patch('src.routes.stripe_routes.catalog_version', return_value='catalog-v1')
    @patch('src.routes.stripe_routes.price_subscription_item')
    def test_subscription_uses_canonical_unit_amount(
        self,
        price_item,
        _catalog_version,
        create_session,
        create_price,
    ):
        product = SimpleNamespace(updated_at=None)
        price_item.return_value = {
            'product': product,
            'product_id': 10,
            'sku': 'ACEITE-5L',
            'slug': 'aceite-5l',
            'name': 'Aceite 5L',
            'quantity': 1,
            'frequency': 'monthly',
            'base_unit_amount': 4300,
            'unit_amount': 3870,
            'discount_percent': 10,
            'line_total': 3870,
        }
        create_price.return_value = SimpleNamespace(id='price_test')
        create_session.return_value = SimpleNamespace(id='cs_test', url='https://checkout.stripe.test/subscription')
        payload = {
            'item': {
                'id': 10,
                'slug': 'aceite-5l',
                'price': 1.0,
                'quantity': 1,
                'subscription_frequency': 'monthly',
            },
            'customer_info': self.customer,
            'locale': 'es',
        }

        with self.app.test_request_context(json=payload):
            response = create_subscription_checkout()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(create_price.call_args.kwargs['unit_amount'], 3870)
        self.assertEqual(create_session.call_args.kwargs['metadata']['pricing_source'], 'server')


if __name__ == '__main__':
    unittest.main()
