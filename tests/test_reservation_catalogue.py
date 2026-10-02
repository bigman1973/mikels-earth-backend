import unittest
from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.models.stock import StockMovement
from src.services.checkout_pricing import calculate_checkout_line_price
from src.services.stock_service import adjust_web_stock


class ReservationCatalogueTests(unittest.TestCase):
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
                reservation_only=True,
                reservation_message=(
                    'La cosecha 2026/27 se sirve por reserva. Se embotella a finales de octubre y te llega en cuanto salga.'
                ),
                reservation_stock_total=1080,
                tiered_discount=[{
                    'minQuantity': 12,
                    'label': 'Caja de 12',
                    'bundleQuantity': 12,
                    'paidQuantity': 11,
                }],
            )
            db.session.add(self.product)
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_reservation_box_pricing_is_exact_for_each_full_case(self):
        with self.app.app_context():
            product = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one()
            self.assertEqual(str(calculate_checkout_line_price(product, 2).expected_line_total), '39.80')
            self.assertEqual(str(calculate_checkout_line_price(product, 12).expected_line_total), '218.90')
            self.assertEqual(str(calculate_checkout_line_price(product, 24).expected_line_total), '437.80')
            self.assertEqual(str(calculate_checkout_line_price(product, 36).expected_line_total), '656.70')

    def test_harvest_allocation_records_an_auditable_web_stock_adjustment(self):
        with self.app.app_context():
            product = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one()
            movement = adjust_web_stock(
                product,
                1080,
                reason='reservation_harvest_2026_27',
                reference='Asignación web de reserva · cosecha 2026/27',
            )
            db.session.commit()
            recorded = StockMovement.query.one()
            self.assertIsNone(recorded.order_id)
            self.assertEqual(recorded.quantity_delta, -4200)
            self.assertEqual(recorded.stock_before, 5280)
            self.assertEqual(recorded.stock_after, 1080)
            self.assertEqual(recorded.reason, 'reservation_harvest_2026_27')
            self.assertEqual(movement.id, recorded.id)

    def test_public_catalogue_marks_product_as_reservation(self):
        with self.app.app_context():
            product = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one()
            data = product.to_frontend_dict()
            self.assertTrue(data['reservationOnly'])
            self.assertEqual(data['reservationStockTotal'], 1080)
            self.assertIn('cosecha 2026/27', data['reservationMessage'])


if __name__ == '__main__':
    unittest.main()
