import unittest

from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.product_routes import product_bp


class PublicProductAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(product_bp, url_prefix='/api')
        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                WebProduct(
                    name='Temprano',
                    slug='aceite-temprano-sin-filtrar',
                    sku='MIKVET500',
                    price=17.50,
                    category='Aceites',
                    stock=839,
                    reservation_only=True,
                    reservation_stock_total=840,
                    active=True,
                    visible_in_store=True,
                ),
                WebProduct(
                    name='Sin existencias',
                    slug='sin-existencias',
                    sku='MIKOUT001',
                    price=10.00,
                    category='Packs',
                    stock=0,
                    active=True,
                    visible_in_store=True,
                ),
            ])
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_detail_and_listing_expose_only_boolean_availability(self):
        detail = self.client.get('/api/products/aceite-temprano-sin-filtrar')
        self.assertEqual(detail.status_code, 200)
        detail_payload = detail.get_json()
        self.assertTrue(detail_payload['available'])
        self.assertNotIn('stock', detail_payload)
        self.assertNotIn('reservationStockTotal', detail_payload)
        self.assertNotIn('839', detail.get_data(as_text=True))
        self.assertNotIn('840', detail.get_data(as_text=True))

        listing = self.client.get('/api/products')
        self.assertEqual(listing.status_code, 200)
        products = {product['slug']: product for product in listing.get_json()['products']}
        self.assertTrue(products['aceite-temprano-sin-filtrar']['available'])
        self.assertFalse(products['sin-existencias']['available'])
        for product in products.values():
            self.assertNotIn('stock', product)
            self.assertNotIn('reservationStockTotal', product)
            self.assertNotIn('inventoryLevel', product)

    def test_protected_admin_payload_keeps_exact_inventory(self):
        with self.app.app_context():
            payload = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one().to_admin_dict()
        self.assertEqual(payload['stock'], 839)
        self.assertEqual(payload['reservationStockTotal'], 840)


if __name__ == '__main__':
    unittest.main()
