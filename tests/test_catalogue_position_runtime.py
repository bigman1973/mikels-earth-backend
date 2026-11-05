"""Generic public order only; no campaign configuration or provider network."""
import unittest
from flask import Flask
from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.product_routes import product_bp


class CatalogueRuntimePositionTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(product_bp, url_prefix='/api')
        with self.app.app_context():
            db.create_all()
            rows = [
                ('ZERO', 0, True, True), ('SECOND', 100, True, True),
                ('FIRST', 7, True, True), ('TIED', 100, True, True),
                ('NEGATIVE', -1, True, True), ('NULL', None, True, True),
                ('DRAFT', 1, False, False), ('HIDDEN', 2, True, False),
            ]
            for sku, position, active, visible in rows:
                product = WebProduct(name=sku, sku=sku, slug=sku.lower(), category='Packs', price=10,
                                     display_order=position, active=active, visible_in_store=visible)
                db.session.add(product)
            db.session.commit()
            # ORM default should not turn the legacy SQL NULL fixture into zero.
            WebProduct.query.filter_by(sku='NULL').one().display_order = None
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_positive_positions_then_id_tiebreak_then_unpositioned(self):
        for lang in ('es', 'en'):
            result = self.client.get('/api/products?lang=' + lang)
            self.assertEqual(result.status_code, 200)
            self.assertEqual([item['sku'] for item in result.json['products']],
                             ['FIRST', 'SECOND', 'TIED', 'ZERO', 'NEGATIVE', 'NULL'])

    def test_order_never_activates_or_exposes_inactive_or_hidden_products(self):
        for slug in ('draft', 'hidden'):
            self.assertEqual(self.client.get('/api/products/' + slug).status_code, 404)
        with self.app.app_context():
            self.assertFalse(WebProduct.query.filter_by(sku='DRAFT').one().active)
            self.assertFalse(WebProduct.query.filter_by(sku='HIDDEN').one().visible_in_store)

    def test_ordering_does_not_expose_inventory(self):
        for item in self.client.get('/api/products').json['products']:
            self.assertNotIn('stock', item)
            self.assertNotIn('reservationStockTotal', item)
            self.assertNotIn('inventoryLevel', item)


if __name__ == '__main__':
    unittest.main()
