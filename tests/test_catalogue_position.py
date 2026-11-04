"""Grid order/admin position tests: SQLite memory only, network forbidden."""
import os
import unittest
from unittest.mock import patch
from flask import Flask

os.environ.setdefault('SECRET_KEY', 'local-grid-fixture')
os.environ.setdefault('JWT_SECRET', 'local-grid-fixture-key-at-least-32-characters')
os.environ.setdefault('ADMIN_SECRET_KEY', 'local-grid-fixture')

from src.models.user import db
from src.models.web_product import WebProduct
from src.models.admin_user import AdminUser
from src.routes.auth_routes import generate_admin_token
from src.routes.product_routes import product_bp
from src.routes.admin_panel_routes import admin_panel_bp
from src.services.campaign_packs import CAMPAIGN_PACKS
from src.services.catalogue_position import catalogue_position, MAX_POSITION

ORDER = ['BF26-TEMP6', 'BF26-ECO6', 'BF26-PARA3', 'BF26-TEMP12', 'BF26-PARA6', 'BF26-5L3', 'MIKVET500']


class CataloguePositionTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(product_bp, url_prefix='/api')
        self.app.register_blueprint(admin_panel_bp, url_prefix='/api/admin')
        with self.app.app_context():
            db.create_all()
            # Deliberately reverse IDs against the requested position order.
            for pack in reversed(CAMPAIGN_PACKS):
                db.session.add(WebProduct(name=pack.name, sku=pack.sku, slug=pack.slug, price=float(pack.price), category='Packs', stock=21, active=False, visible_in_store=False, in_promotion=True, display_order=ORDER.index(pack.sku) + 1))
            db.session.add_all([
                WebProduct(name='Cosecha 2026/27', sku='MIKVET500', slug='aceite-temprano-sin-filtrar', price=19.90, category='Aceites', stock=11, display_order=7),
                WebProduct(name='Other explicit', sku='OTHER-100', slug='other-100', price=10, category='Packs', stock=13, display_order=100),
                WebProduct(name='Legacy zero', sku='LEGACY-ZERO', slug='legacy-zero', price=10, category='Packs', stock=13, display_order=0),
                WebProduct(name='Legacy negative', sku='LEGACY-NEGATIVE', slug='legacy-negative', price=10, category='Packs', stock=13, display_order=-1),
                WebProduct(name='Legacy null', sku='LEGACY-NULL', slug='legacy-null', price=10, category='Packs', stock=13, display_order=0),
            ])
            admin = AdminUser(microsoft_id='local-grid-admin', email='grid@example.test', name='Local Grid', role='admin')
            viewer = AdminUser(microsoft_id='local-grid-viewer', email='viewer@example.test', name='Local Viewer', role='viewer')
            db.session.add_all([admin, viewer])
            db.session.commit()
            WebProduct.query.filter_by(sku='LEGACY-NULL').one().display_order = None
            db.session.commit()
            self.headers = {'Authorization': 'Bearer ' + generate_admin_token(admin)}
            self.viewer_headers = {'Authorization': 'Bearer ' + generate_admin_token(viewer)}
            self.product_id = WebProduct.query.filter_by(sku='BF26-TEMP6').one().id
        self.client = self.app.test_client()
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden in grid fixtures'))
        network.start()
        self.addCleanup(network.stop)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def product(self):
        return db.session.get(WebProduct, self.product_id)

    def test_existing_column_reused_without_new_position_schema(self):
        self.assertIn('display_order', WebProduct.__table__.c)
        self.assertTrue(WebProduct.__table__.c.display_order.nullable)
        for name in ['sort_order', 'position']:
            self.assertNotIn(name, WebProduct.__table__.c)
        for value in [None, 0, -1, True, '1', 1.5, MAX_POSITION + 1]:
            self.assertIsNone(catalogue_position(value))
        self.assertEqual(catalogue_position(7), 7)

    def test_inactive_positioned_packs_remain_unlisted_and_direct_404(self):
        listed = self.client.get('/api/products').get_json()['products']
        self.assertFalse(any(p['sku'].startswith('BF26-') for p in listed))
        for pack in CAMPAIGN_PACKS:
            self.assertEqual(self.client.get('/api/products/' + pack.slug).status_code, 404)
        with self.app.app_context():
            self.assertFalse(self.product().active)
            self.assertFalse(self.product().visible_in_store)

    def test_exact_requested_order_only_after_fixture_activation_and_stable_rest(self):
        with self.app.app_context():
            for p in WebProduct.query.filter(WebProduct.sku.in_(ORDER)).all():
                p.active = True
                p.visible_in_store = True
            db.session.commit()
        expected = ORDER + ['OTHER-100', 'LEGACY-ZERO', 'LEGACY-NEGATIVE', 'LEGACY-NULL']
        for language in ['es', 'en']:
            response = self.client.get('/api/products?lang=' + language)
            self.assertEqual(response.status_code, 200)
            products = response.get_json()['products']
            self.assertEqual([p['sku'] for p in products], expected)
            self.assertEqual([p['displayOrder'] for p in products[:7]], list(range(1, 8)))
            self.assertEqual([p['displayOrder'] for p in products[-3:]], [None, None, None])
            for p in products:
                for private in ['stock', 'reservationStockTotal', 'shippingCost', 'preparationCost']:
                    self.assertNotIn(private, p)
            self.assertFalse(products[6]['inPromotion'])
            self.assertTrue(all(p['inPromotion'] for p in products[:6]))

    def test_duplicate_positive_positions_use_id_tie_break(self):
        with self.app.app_context():
            a = WebProduct.query.filter_by(sku='LEGACY-ZERO').one()
            b = WebProduct.query.filter_by(sku='LEGACY-NEGATIVE').one()
            a.display_order = b.display_order = 100
            db.session.commit()
        tail = [p['sku'] for p in self.client.get('/api/products').get_json()['products']]
        self.assertEqual(tail, ['MIKVET500', 'OTHER-100', 'LEGACY-ZERO', 'LEGACY-NEGATIVE', 'LEGACY-NULL'])

    def test_position_only_put_and_null_clear_never_activate_or_touch_price_stock(self):
        for value in [2, None, MAX_POSITION]:
            response = self.client.put(f'/api/admin/web-products/{self.product_id}', json={'displayOrder': value}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()['product']['displayOrder'], value)
            with self.app.app_context():
                p = self.product()
                self.assertEqual(p.display_order, value)
                self.assertFalse(p.active)
                self.assertFalse(p.visible_in_store)
                self.assertTrue(p.in_promotion)
                self.assertEqual(p.price, 59.70)
                self.assertEqual(p.stock, 21)
            self.assertEqual(self.client.get('/api/products/' + CAMPAIGN_PACKS[0].slug).status_code, 404)

    def test_omitted_position_remains_unchanged_and_invalid_payload_is_atomic(self):
        response = self.client.put(f'/api/admin/web-products/{self.product_id}', json={'description': 'Local fixture'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        for bad in [0, -1, 1.2, True, False, '2', '', {}, [], MAX_POSITION + 1]:
            response = self.client.put(f'/api/admin/web-products/{self.product_id}', json={'displayOrder': bad, 'name': 'Must not mutate'}, headers=self.headers)
            self.assertEqual(response.status_code, 400, str(bad))
            with self.app.app_context():
                self.assertEqual(self.product().display_order, 1)
                self.assertEqual(self.product().name, CAMPAIGN_PACKS[0].name)

    def test_position_edit_requires_admin_and_listing_returns_private_position(self):
        path = f'/api/admin/web-products/{self.product_id}'
        self.assertEqual(self.client.put(path, json={'displayOrder': 5}).status_code, 401)
        self.assertEqual(self.client.put(path, json={'displayOrder': 5}, headers=self.viewer_headers).status_code, 403)
        result = self.client.get(path, headers=self.headers).get_json()
        self.assertEqual(result['displayOrder'], 1)
        self.assertEqual(result['stock'], 21)

    def test_create_validates_position_and_safe_unranked_default(self):
        fixture = {'name': 'Local', 'slug': 'local-create', 'sku': 'LOCAL-CREATE', 'category': 'Packs', 'price': 10, 'active': False, 'visibleInStore': False}
        for value in [0, -2, True, '4', 4.1]:
            result = self.client.post('/api/admin/web-products', json={**fixture, 'displayOrder': value}, headers=self.headers)
            self.assertEqual(result.status_code, 400)
        for index, value in enumerate([None, 4]):
            result = self.client.post('/api/admin/web-products', json={**fixture, 'slug': f'create-{index}', 'sku': f'CREATE-{index}', 'displayOrder': value}, headers=self.headers)
            self.assertEqual(result.status_code, 201)
            self.assertEqual(result.get_json()['product']['displayOrder'], value)
        result = self.client.post('/api/admin/web-products', json=fixture, headers=self.headers)
        self.assertEqual(result.status_code, 201)
        self.assertIsNone(result.get_json()['product']['displayOrder'])

    def test_merged_admin_catalogue_supplies_position_for_editor_fallback(self):
        with patch('src.routes.admin_panel_routes.holded_get_products', return_value=[]):
            result = self.client.get('/api/admin/products', headers=self.headers)
        self.assertEqual(result.status_code, 200)
        item = next(p for p in result.get_json()['products'] if p['sku'] == 'BF26-TEMP6')
        self.assertEqual(item['displayOrder'], 1)
        self.assertFalse(item['active'])
        self.assertFalse(item['visibleInStore'])
