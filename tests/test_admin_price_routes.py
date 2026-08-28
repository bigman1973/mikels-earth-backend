import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.admin_panel_routes import (
    get_web_price,
    update_web_price,
    update_web_product,
)
from src.services.pricing_service import catalog_version, price_one_time_item


def undecorated(function):
    """Unwrap auth decorators; these tests target persistence and pricing behavior."""
    while hasattr(function, '__wrapped__'):
        function = function.__wrapped__
    return function


class AdminPriceRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.product = WebProduct(
            name='Aceite 5L',
            slug='aceite-5l',
            sku='ACEITE-5L',
            category='Aceites',
            price=33.00,
            stock=10,
            active=True,
        )
        db.session.add(self.product)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    @patch('src.routes.admin_panel_routes._sync_price_to_holded', return_value=True)
    def test_quick_price_update_is_persisted_verified_and_used_by_quote(self, _holded):
        with self.app.test_request_context(json={'price': 43.0}):
            response = undecorated(update_web_price)('ACEITE-5L')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['verified'])
        self.assertEqual(payload['pricing_source'], 'database')
        self.assertEqual(payload['price_cents'], 4300)
        self.assertTrue(payload['holded_updated'])

        db.session.expire_all()
        persisted = WebProduct.query.filter_by(sku='ACEITE-5L').one()
        self.assertEqual(persisted.price, 43.0)

        priced = price_one_time_item({
            'sku': 'ACEITE-5L',
            'quantity': 1,
            'price': 0.01,
        })
        self.assertEqual(priced['unit_amount'], 4300)

    def test_exact_readback_returns_database_price_and_catalog_version(self):
        with self.app.test_request_context():
            response = undecorated(get_web_price)('ACEITE-5L')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['pricing_source'], 'database')
        self.assertEqual(payload['price_cents'], 3300)
        self.assertNotEqual(payload['catalog_version'], 'unknown')

    def test_full_editor_update_verifies_price_in_cents(self):
        with self.app.test_request_context(json={'price': 45.67}):
            response = undecorated(update_web_product)(self.product.id)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['verified'])
        self.assertEqual(payload['pricing_source'], 'database')
        self.assertEqual(payload['price_cents'], 4567)
        self.assertEqual(round(payload['product']['price'] * 100), 4567)

    @patch('src.routes.admin_panel_routes._sync_price_to_holded', side_effect=RuntimeError('Holded unavailable'))
    def test_holded_failure_does_not_undo_verified_web_price(self, _holded):
        with self.app.test_request_context(json={'price': 44.0}):
            response = undecorated(update_web_price)('ACEITE-5L')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['verified'])
        self.assertFalse(payload['holded_updated'])
        self.assertIn('Holded unavailable', payload['holded_error'])
        self.assertEqual(payload['price_cents'], 4400)
        self.assertEqual(WebProduct.query.filter_by(sku='ACEITE-5L').one().price, 44.0)

    def test_catalog_version_distinguishes_changes_in_the_same_second(self):
        self.product.updated_at = datetime(2026, 8, 28, 12, 0, 0, 100000)
        db.session.commit()
        first_version = catalog_version([self.product])

        self.product.updated_at = datetime(2026, 8, 28, 12, 0, 0, 200000)
        db.session.commit()
        second_version = catalog_version([self.product])

        self.assertNotEqual(first_version, second_version)

    def test_admin_price_rejects_zero_and_invalid_values(self):
        for invalid_value in (0, -1, None, 'not-a-price'):
            with self.subTest(invalid_value=invalid_value):
                with self.app.test_request_context(json={'price': invalid_value}):
                    response, status = undecorated(update_web_price)('ACEITE-5L')
                self.assertEqual(status, 400)
                self.assertIn('precio', response.get_json()['error'].lower())


if __name__ == '__main__':
    unittest.main()
