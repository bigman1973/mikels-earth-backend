import unittest

from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.product_routes import product_bp


ADDON_CASES = {
    'aceite-temprano-sin-filtrar': ('estuche-regalo-temprano', 'MIKESTTEM'),
    'aceite-oliva-ecologico': ('estuche-regalo-ecologico', 'MIKESTBIO'),
    'aceite-oliva-equilibrado': ('estuche-regalo-virgen-extra', 'MIKESTEV'),
}


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
            oils = [
                WebProduct(
                    name='Aceite Temprano',
                    slug='aceite-temprano-sin-filtrar',
                    sku='OIL-EARLY',
                    price=17.15,
                    category='Aceites',
                    active=True,
                    visible_in_store=True,
                    addons=[{
                        'productSlug': 'estuche-regalo-temprano',
                        'label': 'Añadir Estuche Regalo Premium Temprano',
                    }],
                ),
                WebProduct(
                    name='Aceite Ecológico',
                    slug='aceite-oliva-ecologico',
                    sku='OIL-ECO',
                    price=19.90,
                    category='Aceites',
                    active=True,
                    visible_in_store=True,
                    addons=[{
                        'productSlug': 'estuche-regalo-ecologico',
                        'label': 'Añadir Estuche Regalo Premium Eco',
                    }],
                ),
                WebProduct(
                    name='Aceite Virgen Extra',
                    slug='aceite-oliva-equilibrado',
                    sku='OIL-EV',
                    price=16.90,
                    category='Aceites',
                    active=True,
                    visible_in_store=True,
                    addons=[{
                        'productSlug': 'estuche-regalo-virgen-extra',
                        'label': 'Añadir Estuche Regalo Premium Virgen Extra',
                    }],
                ),
            ]
            boxes = [
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
            ]
            db.session.add_all(oils + boxes)
            db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_each_oil_embeds_only_its_hidden_box_and_real_sku(self):
        response = self.client.get('/api/products?lang=es')
        self.assertEqual(response.status_code, 200)
        products = response.get_json()['products']

        self.assertEqual({product['slug'] for product in products}, set(ADDON_CASES))

        for product in products:
            expected_slug, expected_sku = ADDON_CASES[product['slug']]
            self.assertEqual(len(product['addons']), 1)
            addon = product['addons'][0]
            self.assertEqual(addon['productSlug'], expected_slug)
            self.assertEqual(addon['product']['slug'], expected_slug)
            self.assertEqual(addon['product']['sku'], expected_sku)
            self.assertEqual(addon['product']['price'], 5.0)
            self.assertNotIn('variantId', addon)

    def test_hidden_boxes_and_legacy_generic_have_no_public_product_page(self):
        for slug in [
            'estuche-regalo',
            'estuche-regalo-temprano',
            'estuche-regalo-ecologico',
            'estuche-regalo-virgen-extra',
        ]:
            with self.subTest(slug=slug):
                response = self.client.get(f'/api/products/{slug}')
                self.assertEqual(response.status_code, 404)

    def test_inactive_box_is_omitted_only_from_its_oil(self):
        with self.app.app_context():
            gift_box = WebProduct.query.filter_by(slug='estuche-regalo-temprano').one()
            gift_box.active = False
            db.session.commit()

        products = {
            product['slug']: product
            for product in self.client.get('/api/products').get_json()['products']
        }
        self.assertEqual(products['aceite-temprano-sin-filtrar']['addons'], [])
        self.assertEqual(len(products['aceite-oliva-ecologico']['addons']), 1)
        self.assertEqual(len(products['aceite-oliva-equilibrado']['addons']), 1)

    def test_invalid_box_price_is_omitted_only_from_its_oil(self):
        with self.app.app_context():
            gift_box = WebProduct.query.filter_by(slug='estuche-regalo-ecologico').one()
            gift_box.price = 0
            db.session.commit()

        products = {
            product['slug']: product
            for product in self.client.get('/api/products').get_json()['products']
        }
        self.assertEqual(products['aceite-oliva-ecologico']['addons'], [])
        self.assertEqual(len(products['aceite-temprano-sin-filtrar']['addons']), 1)
        self.assertEqual(len(products['aceite-oliva-equilibrado']['addons']), 1)

    def test_unknown_box_slug_is_omitted_entirely(self):
        with self.app.app_context():
            early_oil = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').one()
            early_oil.addons = [{
                'productSlug': 'estuche-inexistente',
                'label': 'Complemento inválido',
            }]
            db.session.commit()

        response = self.client.get('/api/products/aceite-temprano-sin-filtrar')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get('addons'), [])

    def test_admin_payload_exposes_saleability_and_store_visibility(self):
        with self.app.app_context():
            boxes = WebProduct.query.filter(
                WebProduct.slug.in_([slug for slug, _ in ADDON_CASES.values()])
            ).all()
            payloads = [box.to_admin_dict() for box in boxes]

        self.assertEqual(len(payloads), 3)
        self.assertTrue(all(payload['active'] for payload in payloads))
        self.assertTrue(all(not payload['visibleInStore'] for payload in payloads))


if __name__ == '__main__':
    unittest.main()
