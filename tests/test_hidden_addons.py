import unittest

from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.product_routes import product_bp


class HiddenAddonCatalogTests(unittest.TestCase):
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
            self.early_oil = WebProduct(
                name='Aceite Temprano',
                slug='aceite-temprano-sin-filtrar',
                sku='OIL-EARLY',
                price=17.15,
                category='Aceites',
                active=True,
                visible_in_store=True,
                addons=[{
                    'productSlug': 'estuche-regalo',
                    'variantId': 'temprano',
                    'label': 'Añadir Estuche Regalo Premium Temprano',
                }],
            )
            self.eco_oil = WebProduct(
                name='Aceite Ecológico',
                slug='aceite-oliva-ecologico',
                sku='OIL-ECO',
                price=19.90,
                category='Aceites',
                active=True,
                visible_in_store=True,
                addons=[{
                    'productSlug': 'estuche-regalo',
                    'variantId': 'eco',
                    'label': 'Añadir Estuche Regalo Premium Eco',
                }],
            )
            self.gift_box = WebProduct(
                name='Estuche de Regalo Premium',
                slug='estuche-regalo',
                sku='MIKEST01',
                price=5.00,
                category='Packs',
                active=True,
                visible_in_store=False,
                variants=[
                    {'id': 'temprano', 'name': 'Temprano'},
                    {'id': 'eco', 'name': 'Ecológico'},
                ],
            )
            db.session.add_all([self.early_oil, self.eco_oil, self.gift_box])
            db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_hidden_addon_is_not_listed_but_is_embedded_in_both_oils(self):
        response = self.client.get('/api/products?lang=es')
        self.assertEqual(response.status_code, 200)
        products = response.get_json()['products']

        self.assertEqual(
            {product['slug'] for product in products},
            {'aceite-temprano-sin-filtrar', 'aceite-oliva-ecologico'},
        )

        for product in products:
            self.assertEqual(len(product['addons']), 1)
            addon = product['addons'][0]
            self.assertEqual(addon['productSlug'], 'estuche-regalo')
            self.assertEqual(addon['product']['slug'], 'estuche-regalo')
            self.assertEqual(addon['product']['price'], 5.0)

    def test_hidden_addon_has_no_public_product_page(self):
        response = self.client.get('/api/products/estuche-regalo')
        self.assertEqual(response.status_code, 404)

    def test_inactive_addon_is_omitted_entirely(self):
        with self.app.app_context():
            gift_box = WebProduct.query.filter_by(slug='estuche-regalo').one()
            gift_box.active = False
            db.session.commit()

        response = self.client.get('/api/products')
        products = response.get_json()['products']
        for product in products:
            self.assertEqual(product.get('addons'), [])

    def test_invalid_addon_price_is_omitted_entirely(self):
        with self.app.app_context():
            gift_box = WebProduct.query.filter_by(slug='estuche-regalo').one()
            gift_box.price = 0
            db.session.commit()

        response = self.client.get('/api/products')
        products = response.get_json()['products']
        for product in products:
            self.assertEqual(product.get('addons'), [])

    def test_missing_variant_is_omitted_entirely(self):
        with self.app.app_context():
            early_oil = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one()
            early_oil.addons = [{
                'productSlug': 'estuche-regalo',
                'variantId': 'inexistente',
                'label': 'Complemento inválido',
            }]
            db.session.commit()

        response = self.client.get('/api/products/aceite-temprano-sin-filtrar')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get('addons'), [])

    def test_admin_payload_exposes_both_independent_flags(self):
        with self.app.app_context():
            gift_box = WebProduct.query.filter_by(slug='estuche-regalo').one()
            payload = gift_box.to_admin_dict()

        self.assertTrue(payload['active'])
        self.assertFalse(payload['visibleInStore'])


if __name__ == '__main__':
    unittest.main()
