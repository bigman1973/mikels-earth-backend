import unittest
from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.models.stock import StockMovement
from src.services.checkout_pricing import calculate_checkout_line_price
from src.services.stock_service import adjust_web_stock


class TempranoOrdinarySaleCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()
            self.product = WebProduct(
                name='Aceite temprano sin filtrar',
                slug='aceite-temprano-sin-filtrar',
                sku='MIKVET500R',
                price=19.90,
                category='Aceites',
                stock=5280,
                active=True,
                visible_in_store=True,
                tiered_discount=[
                    {'actualQuantity': 1, 'label': '1 botella 500 ml', 'price': 19.90},
                    {'actualQuantity': 3, 'label': 'Pack de 3', 'featured': True, 'price': 56.70},
                    {'actualQuantity': 6, 'label': 'Pack de 6', 'price': 107.40},
                    {'actualQuantity': 12, 'label': 'Caja de 12', 'price': 202.80},
                ],
            )
            db.session.add(self.product)
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_ordinary_sale_packs_keep_their_published_prices(self):
        with self.app.app_context():
            product = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one()
            self.assertEqual(str(calculate_checkout_line_price(product, 1).expected_line_total), '19.90')
            self.assertEqual(str(calculate_checkout_line_price(product, 3).expected_line_total), '56.70')
            self.assertEqual(str(calculate_checkout_line_price(product, 6).expected_line_total), '107.40')
            self.assertEqual(str(calculate_checkout_line_price(product, 12).expected_line_total), '202.80')

    def test_stock_adjustment_remains_auditable_without_changing_sale_mode(self):
        with self.app.app_context():
            product = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one()
            movement = adjust_web_stock(
                product,
                1080,
                reason='ordinary_sale_stock_adjustment',
                reference='Ajuste interno de stock Temprano 2026/27',
            )
            db.session.commit()
            recorded = StockMovement.query.one()
            self.assertIsNone(recorded.order_id)
            self.assertEqual(recorded.quantity_delta, -4200)
            self.assertEqual(recorded.stock_before, 5280)
            self.assertEqual(recorded.stock_after, 1080)
            self.assertEqual(recorded.reason, 'ordinary_sale_stock_adjustment')
            self.assertEqual(movement.id, recorded.id)

    def test_ordinary_sale_audit_reason_fits_the_persisted_limit(self):
        reason = 'ordinary_sale_stock_adjustment'
        self.assertLessEqual(
            len(reason),
            StockMovement.reason.property.columns[0].type.length,
        )

    def test_public_catalogue_exposes_an_available_ordinary_product(self):
        with self.app.app_context():
            product = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one()
            data = product.to_frontend_dict()
            self.assertTrue(data['available'])
            self.assertEqual(data['price'], 19.90)
            self.assertNotIn('stock', data)
            self.assertNotIn('reservationStockTotal', data)
            self.assertNotIn('reservationOnly', data)
            self.assertNotIn('reservationMessage', data)
            self.assertTrue(all('reservation' not in key.lower() for key in data))


if __name__ == '__main__':
    unittest.main()
