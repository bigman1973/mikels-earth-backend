"""BF26 preparation: SQLite-only fixtures, no real activation or external call.

Every active state and synthetic pack stock below exists only in an in-memory
fixture. Jordi's actual Holded masters/recipes have NOT been created/confirmed.
"""
import csv
import io
import json
import os
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

os.environ.setdefault('SECRET_KEY', 'test-only-bf26')
os.environ.setdefault('JWT_SECRET', 'test-only-bf26')
os.environ.setdefault('ADMIN_SECRET_KEY', 'test-only-bf26')

from src.models.user import db
from src.models.web_product import WebProduct
from src.models.coupon import Coupon
from src.models.order import Order
from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot
from src.models.stock import StockMovement, StockReservation
from src.routes import admin_panel_routes
from src.routes.product_routes import product_bp
from src.routes.coupon_routes import coupon_bp
from src.routes.meta_catalog_routes import meta_catalog_bp
from src.routes.stripe_routes import CHECKOUT_PRICING_SNAPSHOT_KEY, stripe_bp
from src.routes.abandoned_cart_routes import _canonical_cart_items
from src.services.campaign_packs import CAMPAIGN_PACKS, campaign_pack_for
from src.services.cart_promotions import PROMOTION_COUPON_MESSAGE
from src.services.checkout_pricing import calculate_checkout_line_price, volume_discount_percent
from src.services.holded_tax_service import (
    FiscalValidationError, FISCAL_EXPANSION_PACK_SKUS, build_holded_master_index,
    prepare_document_items, resolve_tax_profile,
)
from src.services.order_tax_snapshot import build_tax_rule_snapshot
from src.services.product_identity import harvest_product_name
from src.services.protected_catalogue import (
    FULL_PRICE_ONLY_SKUS, PROTECTED_COMPONENT_PACK_SKUS,
    is_mixed_with_full_price_only_product, is_promotion_protected,
)
from src.services.klaviyo_service import klaviyo_notify_new_order, klaviyo_send_order_confirmation


# Deliberately explicit, independently asserted approved commercial values.
APPROVED = (
    ('BF26-TEMP6', '59.70', 'MIKVET5002526', 6),
    ('BF26-TEMP12', '119.40', 'MIKVET5002526', 12),
    ('BF26-ECO6', '71.40', 'MIKBIO19', 6),
    ('BF26-PARA3', '30.90', 'MIKPARA450', 3),
    ('BF26-PARA6', '61.70', 'MIKPARA450', 6),
    ('BF26-5L3', '96.75', 'MIKVE5LP', 3),
)
CUSTOMER = {
    'name': 'Cliente Fixture BF26', 'email': 'bf26@example.test', 'phone': '+34600000000',
    'address': 'Calle Fixture 1', 'city': 'Lleida', 'postal_code': '25001', 'country': 'España',
}


def erp_fixtures():
    """Pure synthetic ERP catalogue; these IDs are NOT real Product IDs."""
    components = [
        {'id': 'fixture-old', 'sku': 'MIKVET5002526', 'name': 'OLD', 'taxes': ['s_iva_4']},
        {'id': 'fixture-eco', 'sku': 'MIKBIO19', 'name': 'ECO', 'taxes': ['s_iva_4']},
        {'id': 'fixture-para', 'sku': 'MIKPARA450', 'name': 'PARA', 'taxes': ['s_iva_10']},
        {'id': 'fixture-5l', 'sku': 'MIKVE5LP', 'name': '5L', 'taxes': ['s_iva_4']},
        {'id': 'fixture-current', 'sku': 'MIKVET500', 'name': 'CURRENT', 'taxes': ['s_iva_4']},
        {'id': 'fixture-extra', 'sku': 'MIKESTKRA', 'name': 'BOX', 'taxes': ['s_iva_21']},
        {'id': 'fixture-normal', 'sku': 'NORMAL-LOCAL', 'name': 'NORMAL', 'taxes': ['s_iva_10']},
    ]
    by_sku = {p['sku']: p for p in components}
    return components + [
        {'id': 'fixture-' + pack.sku, 'sku': pack.sku, 'name': 'Nombre ERP genérico',
         'kind': 'pack', 'taxes': by_sku[pack.component_sku]['taxes'],
         'pack_items': [{'product_selection': by_sku[pack.component_sku]['id'], 'units': pack.component_units}]}
        for pack in CAMPAIGN_PACKS
    ]


class BF26DefinitionsTests(unittest.TestCase):
    def test_six_definitions_match_approved_codes_prices_components(self):
        self.assertEqual(tuple((p.sku, str(p.price), p.component_sku, p.component_units) for p in CAMPAIGN_PACKS), APPROVED)
        self.assertEqual(len({p.slug for p in CAMPAIGN_PACKS}), 6)
        self.assertEqual(CAMPAIGN_PACKS[0].slug, 'aceite-temprano-sin-filtrar-cosecha-2025-26-pack-6')
        self.assertEqual(CAMPAIGN_PACKS[1].slug, 'aceite-temprano-sin-filtrar-cosecha-2025-26-caja-12')
        for p in CAMPAIGN_PACKS[:2]:
            self.assertIn('2025/26', p.name)
            self.assertEqual(harvest_product_name(p.sku, 'Wrong new harvest'), p.name)
        self.assertIsNone(campaign_pack_for('MIKVET5002526'))
        for sku in ['MIKPACKF', 'MIKPACKTP', 'MIKVET500R', 'MIKBIO19R', 'MIKPARA450R']:
            self.assertIsNone(campaign_pack_for(sku))

    def test_contract_and_schema_only_migration(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / 'docs/bf26-contract.json').read_text())
        self.assertEqual(tuple((p['sku'], f"{p['price']:.2f}", p['component_sku'], p['component_units']) for p in contract['packs']), APPROVED)
        self.assertEqual(contract['promotion']['coupon_rejection_message'], PROMOTION_COUPON_MESSAGE)
        self.assertFalse(contract['retirement']['automation'])
        sql = (root / 'migrations/add_in_promotion.sql').read_text()
        statements = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--')).strip()
        self.assertEqual(statements, 'ALTER TABLE web_products\n    ADD COLUMN IF NOT EXISTS in_promotion BOOLEAN NOT NULL DEFAULT FALSE;')
        column = WebProduct.__table__.c.in_promotion
        self.assertFalse(column.nullable)
        self.assertFalse(column.default.arg)
        self.assertIsNotNone(column.server_default)

    def test_legacy_barriers_remain_exact(self):
        self.assertEqual(FULL_PRICE_ONLY_SKUS, {'MIKVET500', 'MIKVE5LP', 'MIKPARA450'})
        self.assertEqual(PROTECTED_COMPONENT_PACK_SKUS, {'MIKPACKF', 'MIKPACKTP'})
        self.assertEqual(FISCAL_EXPANSION_PACK_SKUS, {'MIKPACKF', 'MIKPACKYPO'})
        self.assertTrue(is_mixed_with_full_price_only_product(['MIKVET500', 'BF26-TEMP6']))
        self.assertTrue(is_mixed_with_full_price_only_product(['MIKVE5LP', 'BF26-5L3']))
        self.assertFalse(is_mixed_with_full_price_only_product(['MIKVET500', 'MIKVET5002526']))
        self.assertFalse(any(is_promotion_protected(p.sku) for p in CAMPAIGN_PACKS))


class BF26LocalCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(product_bp, url_prefix='/api')
        self.app.register_blueprint(coupon_bp, url_prefix='/api/coupon')
        self.app.register_blueprint(stripe_bp)
        self.app.register_blueprint(meta_catalog_bp, url_prefix='/api')
        self.app.register_blueprint(admin_panel_routes.admin_panel_bp, url_prefix='/api/admin')
        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                WebProduct(name='Nombre maestro genérico', name_en='Wrong 2026/27 name',
                           sku=p.sku, slug=p.slug, price=float(p.price), category='Packs',
                           stock=1000, active=False, visible_in_store=False, in_promotion=True,
                           image='/images/fixture.webp',
                           original_price=999, subscription_available=True, subscription_discount=50,
                           subscription_frequencies=[{'value': 'monthly', 'discount': 50}],
                           subscription_terms={'duration': 'fixture'},
                           tiered_discount=[{'actualQuantity': 1, 'label': 'Bottle', 'price': 0.01},
                                            {'minQuantity': 1, 'discount': 99, 'bundleQuantity': 6, 'paidQuantity': 1}],
                           volume_discount={'minQuantity': 1, 'discount': 99},
                           addons=[{'productSlug': 'normal-local'}])
                for p in CAMPAIGN_PACKS
            ])
            db.session.add_all([
                WebProduct(name='Normal', sku='NORMAL-LOCAL', slug='normal-local', price=10, category='Packs', stock=1000, active=True, visible_in_store=True),
                WebProduct(name='Generic promo, not BF26', sku='ANY-PROMO', slug='any-promo', price=7, category='Packs', stock=1000, active=True, visible_in_store=False, in_promotion=True),
                WebProduct(name='Current protected', sku='MIKVET500', slug='aceite-temprano-sin-filtrar', price=19.90, category='Aceites', stock=1000, active=True, visible_in_store=True),
                WebProduct(name='Old component remains inactive', sku='MIKVET5002526', slug='aceite-temprano-sin-filtrar-cosecha-2025-26', price=119.40, category='Aceites', stock=0, active=False, visible_in_store=False),
                Coupon(code='LOCAL10', discount_type='percentage', discount_value=10, active=True),
                Coupon(code='LOCAL5', discount_type='fixed', discount_value=5, active=True),
            ])
            db.session.commit()
        self.client = self.app.test_client()
        self.masters = erp_fixtures()
        self.holded = self.mock('src.services.holded_service.holded_get_products', side_effect=lambda: deepcopy(self.masters))
        self.session = self.mock('src.routes.stripe_routes.stripe.checkout.Session.create', side_effect=lambda **kwargs: SimpleNamespace(id=f'cs_local_bf26_{self.session.call_count}', url='https://checkout.stripe.test/bf26'))
        self.stripe_coupon = self.mock('src.routes.stripe_routes.stripe.Coupon.create', return_value=SimpleNamespace(id='coupon_local'))
        self.stripe_price = self.mock('src.routes.stripe_routes.stripe.Price.create')
        self.mock('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden in BF26 fixtures'))

    def mock(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def activate_fixture(self, skus=None, visible=False):
        """Explicit future saleability in test SQLite ONLY, not a real activation."""
        with self.app.app_context():
            for p in WebProduct.query.filter(WebProduct.sku.in_(skus or [p.sku for p in CAMPAIGN_PACKS])):
                p.active = True
                p.visible_in_store = visible
            db.session.commit()

    def item(self, own_sku, quantity=1, **changes):
        with self.app.app_context():
            p = WebProduct.query.filter_by(sku=own_sku).one()
            result = {'id': p.id, 'sku': own_sku, 'slug': p.slug, 'name': 'Untrusted customer title', 'price': p.price, 'quantity': quantity}
        result.update(changes)
        return result

    def checkout(self, items, **changes):
        return self.client.post('/api/stripe/create-checkout-session', json={'items': items, 'customer_info': CUSTOMER, **changes})

    def assert_no_external_checkout(self):
        self.holded.assert_not_called()
        self.session.assert_not_called()
        self.stripe_coupon.assert_not_called()
        self.stripe_price.assert_not_called()
        with self.app.app_context():
            self.assertEqual(StockReservation.query.count(), 0)
            self.assertEqual(StockMovement.query.count(), 0)

    def test_drafts_inactive_404_and_cannot_checkout(self):
        listed = {p['sku'] for p in self.client.get('/api/products').get_json()['products']}
        for pack in CAMPAIGN_PACKS:
            self.assertNotIn(pack.sku, listed)
            self.assertEqual(self.client.get('/api/products/' + pack.slug).status_code, 404)
            response = self.checkout([self.item(pack.sku)])
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()['error'], 'PRODUCT_UNAVAILABLE')
        self.assert_no_external_checkout()

    def test_future_active_hidden_detail_and_visible_only_listing(self):
        self.activate_fixture()
        listing = {p['sku'] for p in self.client.get('/api/products').get_json()['products']}
        for pack in CAMPAIGN_PACKS:
            self.assertNotIn(pack.sku, listing)
            for lang in ['es', 'en']:
                response = self.client.get('/api/products/' + pack.slug + '?lang=' + lang)
                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertEqual(data['name'], pack.name)
                self.assertEqual(data['price'], float(pack.price))
                self.assertTrue(data['inPromotion'])
                self.assertFalse(data['subscriptionAvailable'])
                for key in ['originalPrice', 'tieredDiscount', 'volumeDiscount', 'addons', 'subscriptionDiscount', 'subscriptionFrequencies', 'subscriptionTerms', 'stock']:
                    self.assertNotIn(key, data)
        self.activate_fixture(visible=True)
        listed = {p['sku']: p for p in self.client.get('/api/products').get_json()['products']}
        self.assertTrue(all(listed[p.sku]['name'] == p.name for p in CAMPAIGN_PACKS))
        rows = {r['id']: r for r in csv.DictReader(io.StringIO(self.client.get('/api/meta-catalog/products.csv').get_data(as_text=True)))}
        for pack in CAMPAIGN_PACKS:
            self.assertEqual(rows[pack.sku]['title'], pack.name)
            self.assertEqual(rows[pack.sku]['price'], f'{pack.price:.2f} EUR')

    def test_all_six_quantity_one_and_two_are_complete_pack_prices(self):
        self.activate_fixture()
        for pack in CAMPAIGN_PACKS:
            for quantity in [1, 2]:
                with self.subTest(sku=pack.sku, quantity=quantity):
                    response = self.checkout([self.item(pack.sku, quantity)], discount_amount=999)
                    self.assertEqual(response.status_code, 200, response.get_json())
                    params = self.session.call_args.kwargs
                    self.assertEqual(len(params['line_items']), 1)
                    line = params['line_items'][0]
                    self.assertEqual((line['quantity'], line['price_data']['unit_amount']), (quantity, int(pack.price * 100)))
                    self.assertEqual(line['price_data']['product_data']['name'], pack.name)
                    self.assertEqual(line['price_data']['product_data']['metadata']['sku'], pack.sku)
                    self.assertEqual(params['metadata']['total'], str(pack.price * quantity))
                    self.assertEqual(params['metadata']['discount_amount'], '0.00')
                    self.assertNotIn('discounts', params)
        self.stripe_coupon.assert_not_called()
        self.stripe_price.assert_not_called()
        with self.app.app_context():
            self.assertTrue(all(p.stock == 1000 for p in WebProduct.query.filter(WebProduct.sku.in_([p.sku for p in CAMPAIGN_PACKS]))))
            self.assertEqual(StockMovement.query.count(), 0)

    def test_pricing_ignores_every_stale_tier_and_single_bottle_price(self):
        with self.app.app_context():
            for pack in CAMPAIGN_PACKS:
                product = WebProduct.query.filter_by(sku=pack.sku).one()
                for quantity in [1, 2, 3, 6, 12]:
                    pricing = calculate_checkout_line_price(product, quantity)
                    self.assertEqual(pricing.expected_line_total, pack.price * quantity)
                    self.assertEqual(pricing.volume_discount_amount, Decimal('0.00'))
                    self.assertEqual(volume_discount_percent(product, quantity), Decimal('0'))
                    self.assertFalse(pricing.fixed_pack)
        self.activate_fixture()
        for pack in CAMPAIGN_PACKS:
            response = self.checkout([self.item(pack.sku, price=float(pack.price / pack.component_units))])
            self.assertEqual(response.status_code, 409)
            update = response.get_json()['price_updates'][0]
            self.assertEqual(update['current_price'], float(pack.price))
            self.assertIsNone(update['tiered_discount'])
            self.assertIsNone(update['volume_discount'])
        self.assert_no_external_checkout()

    def test_fractional_and_invalid_pack_quantities_fail_before_erp(self):
        self.activate_fixture()
        for value in [0, -1, 1.5, None, 'bad']:
            response = self.checkout([self.item('BF26-TEMP6', quantity=value)])
            self.assertEqual(response.status_code, 422, response.get_json())
            self.assertEqual(response.get_json()['error'], 'INVALID_PACK_QUANTITY')
        self.assert_no_external_checkout()

    def test_any_coupon_with_any_promo_rejects_entire_cart_exact_message(self):
        self.activate_fixture()
        cases = [[self.item(p.sku), self.item('NORMAL-LOCAL')] for p in CAMPAIGN_PACKS]
        cases += [[self.item('NORMAL-LOCAL'), self.item('ANY-PROMO', inPromotion=False)]]
        for items in cases:
            for ordered in [items, items[::-1]]:
                for code in ['LOCAL10', 'LOCAL5', 'UNKNOWN', 'VIP', 'NEWSLETTER']:
                    response = self.checkout(ordered, discount_code=code)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.get_json()['message'], PROMOTION_COUPON_MESSAGE)
                    preview = self.client.post('/api/coupon/validate', json={'code': code, 'email': CUSTOMER['email'], 'items': ordered})
                    self.assertFalse(preview.get_json()['valid'])
                    self.assertEqual(preview.get_json()['message'], PROMOTION_COUPON_MESSAGE)
        self.assert_no_external_checkout()

    def test_nested_addons_and_all_id_sku_slug_references_are_authoritative(self):
        normal = self.item('NORMAL-LOCAL')
        promo = self.item('ANY-PROMO')
        references = [
            {'id': promo['id'], 'inPromotion': False},
            {'productId': promo['id']}, {'sku': promo['sku']}, {'slug': promo['slug']},
            {'productSlug': promo['slug']},
            {'id': normal['id'], 'productId': promo['id'], 'sku': normal['sku']},
            {'id': normal['id'], 'slug': normal['slug'], 'sku': promo['sku']},
            {'id': promo['id'], 'sku': normal['sku'], 'slug': normal['slug']},
        ]
        for reference in references:
            for items in [[reference], [dict(normal, addons=[reference])], [dict(normal, addons=[{'product': reference}])], [dict(normal, product={'addons': [reference]})]]:
                response = self.checkout(items, discount_code='LOCAL10')
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.get_json()['message'], PROMOTION_COUPON_MESSAGE)
                preview = self.client.post('/api/coupon/validate', json={'code': 'LOCAL10', 'items': items})
                self.assertEqual(preview.get_json()['message'], PROMOTION_COUPON_MESSAGE)
                use = self.client.post('/api/coupon/use', json={'code': 'LOCAL10', 'email': CUSTOMER['email'], 'items': items})
                self.assertEqual(use.status_code, 422)
                self.assertEqual(use.get_json()['message'], PROMOTION_COUPON_MESSAGE)
        self.assert_no_external_checkout()
        with self.app.app_context():
            self.assertEqual(Coupon.query.filter_by(code='LOCAL10').one().current_uses, 0)

    def test_client_promotion_flags_do_not_create_or_hide_database_promotion(self):
        with patch.object(Coupon, 'validate_coupon', return_value=(True, SimpleNamespace(code='LOCAL10', discount_value=10, discount_type='percentage'))) as validator:
            preview = self.client.post('/api/coupon/validate', json={'code': 'LOCAL10', 'items': [self.item('NORMAL-LOCAL', inPromotion=True)]})
            self.assertTrue(preview.get_json()['valid'])
            validator.assert_called_once()
        self.activate_fixture(['BF26-ECO6'])
        with self.app.app_context():
            WebProduct.query.filter_by(sku='BF26-ECO6').one().in_promotion = False
            db.session.commit()
        # Six-SKU recognition is pricing/recipe validation, NOT coupon veto.
        preview = self.client.post('/api/coupon/validate', json={'code': 'LOCAL10', 'items': [self.item('BF26-ECO6', inPromotion=True)]})
        self.assertTrue(preview.get_json()['valid'])

    def test_conflicting_pack_and_component_identity_is_rejected_not_aliased(self):
        self.activate_fixture()
        response = self.checkout([self.item('BF26-TEMP6', sku='MIKVET500')])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'PRODUCT_IDENTITY_MISMATCH')
        self.assert_no_external_checkout()

    def test_legacy_current_unit_mixed_with_bf26_still_blocked(self):
        self.activate_fixture()
        response = self.checkout([self.item('MIKVET500'), self.item('BF26-TEMP6')])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'PRODUCT_UNAVAILABLE')
        self.assert_no_external_checkout()

    def test_invalid_erp_recipe_blocks_stripe_and_stock_reservation(self):
        self.activate_fixture()
        for pack in CAMPAIGN_PACKS:
            master = next(p for p in self.masters if p['sku'] == pack.sku)
            recipe = master.pop('pack_items')
            response = self.checkout([self.item(pack.sku)])
            self.assertEqual(response.status_code, 422, response.get_json())
            self.assertEqual(response.get_json()['error'], 'TAX_CONFIGURATION_INVALID')
            master['pack_items'] = recipe
        self.session.assert_not_called()
        self.stripe_coupon.assert_not_called()
        with self.app.app_context():
            self.assertEqual(StockReservation.query.count(), 0)

    def test_admin_create_update_strict_boolean_no_activation_or_stock_change(self):
        create = admin_panel_routes.create_web_product.__wrapped__.__wrapped__
        update = admin_panel_routes.update_web_product.__wrapped__.__wrapped__
        with self.app.test_request_context('/api/admin/web-products', method='POST', json={'name': 'Fixture admin', 'slug': 'fixture-admin', 'sku': 'ADMIN-LOCAL', 'category': 'Packs', 'price': 10, 'active': False, 'visibleInStore': False}):
            response, status = create()
            self.assertEqual(status, 201)
            self.assertFalse(response.get_json()['product']['inPromotion'])
        with self.app.app_context():
            product = WebProduct.query.filter_by(sku='ADMIN-LOCAL').one()
            product_id = product.id
        for value in [True, False, True]:
            with self.app.test_request_context('/api/admin/web-products', method='PUT', json={'inPromotion': value}):
                response = update(product_id)
                data = response.get_json()['product']
                self.assertEqual(data['inPromotion'], value)
                self.assertFalse(data['active'])
                self.assertFalse(data['visibleInStore'])
                self.assertEqual(data['stock'], 0)
        for value in ['false', 'true', 0, 1, None, [], {}]:
            with self.app.test_request_context('/api/admin/web-products', method='PUT', json={'inPromotion': value, 'active': True}):
                _, status = update(product_id)
                self.assertEqual(status, 400)
            with self.app.test_request_context('/api/admin/web-products', method='POST', json={'name': 'Bad', 'slug': 'bad', 'category': 'Packs', 'price': 10, 'inPromotion': value}):
                _, status = create()
                self.assertEqual(status, 400)
        with self.app.app_context():
            product = db.session.get(WebProduct, product_id)
            self.assertTrue(product.in_promotion)
            self.assertFalse(product.active)
        self.assert_no_external_checkout()

    def test_admin_routes_still_require_authentication(self):
        self.assertEqual(self.client.post('/api/admin/web-products', json={'inPromotion': True}).status_code, 401)
        with self.app.app_context():
            product_id = WebProduct.query.filter_by(sku='NORMAL-LOCAL').one().id
        self.assertEqual(self.client.put(f'/api/admin/web-products/{product_id}', json={'inPromotion': True}).status_code, 401)

    def test_admin_catalogue_maps_all_six_exact_titles_flags_without_erp_alias(self):
        with patch.object(admin_panel_routes, 'holded_get_products', return_value=self.masters), self.app.test_request_context('/api/admin/products'):
            response = admin_panel_routes.get_products.__wrapped__()
            products = {p['sku']: p for p in response.get_json()['products']}
            web_prices = admin_panel_routes._get_web_prices()
            for pack in CAMPAIGN_PACKS:
                self.assertEqual(products[pack.sku]['name'], pack.name)
                self.assertEqual(products[pack.sku]['web_name'], pack.name)
                self.assertEqual(products[pack.sku]['holded_id'], 'fixture-' + pack.sku)
                self.assertTrue(products[pack.sku]['inPromotion'])
                self.assertFalse(products[pack.sku]['active'])
                self.assertFalse(products[pack.sku]['visibleInStore'])
                self.assertEqual(web_prices[pack.sku]['name'], pack.name)
                self.assertEqual(web_prices[pack.sku]['price'], float(pack.price))
                self.assertTrue(web_prices[pack.sku]['in_promotion'])

    def test_generic_promo_addon_public_flag_and_unselected_offer_does_not_veto(self):
        with self.app.app_context():
            normal = WebProduct.query.filter_by(sku='NORMAL-LOCAL').one()
            normal.addons = [{'productSlug': 'any-promo'}]
            db.session.commit()
        product = self.client.get('/api/products/normal-local').get_json()
        self.assertFalse(product['inPromotion'])
        self.assertTrue(product['addons'][0]['product']['inPromotion'])
        preview = self.client.post('/api/coupon/validate', json={'code': 'LOCAL10', 'items': [self.item('NORMAL-LOCAL')]})
        self.assertTrue(preview.get_json()['valid'])
        selected = self.client.post('/api/coupon/validate', json={'code': 'LOCAL10', 'items': [dict(self.item('NORMAL-LOCAL'), addons=[product['addons'][0]])]})
        self.assertFalse(selected.get_json()['valid'])
        self.assertEqual(selected.get_json()['message'], PROMOTION_COUPON_MESSAGE)
        self.assert_no_external_checkout()

    def test_paid_pack_snapshot_receipt_email_and_erp_keep_exact_pack_identity(self):
        self.activate_fixture(['BF26-TEMP6'])
        self.mock('src.routes.stripe_routes.dispatch_order_confirmation', return_value=(True, None))
        self.mock('src.routes.stripe_routes.dispatch_order_notification', return_value=(True, None))
        self.mock('src.routes.stripe_routes.dispatch_order_delivery_alert')
        self.mock('src.services.email_dispatcher.dispatch_post_purchase_event')
        self.mock('src.services.meta_conversions_api.dispatch_meta_purchase_event', return_value={'sent': False, 'reason': 'fixture'})
        response = self.checkout([self.item('BF26-TEMP6')])
        self.assertEqual(response.status_code, 200, response.get_json())
        params = self.session.call_args.kwargs
        with self.app.app_context():
            snapshot = CheckoutTaxSnapshot.query.one().rules[CHECKOUT_PRICING_SNAPSHOT_KEY]['0']
            self.assertEqual((snapshot['sku'], snapshot['physical_quantity'], snapshot['receipt_line_total']), ('BF26-TEMP6', 1, '59.70'))
            db.session.get(WebProduct, snapshot['id']).name = 'Wrong 2026/27 later name'
            db.session.commit()
            recovered = _canonical_cart_items([{'id': snapshot['id'], 'quantity': 1}])
            self.assertIn('2025/26', recovered[0]['name'])
            self.assertEqual(recovered[0]['line_total'], 59.70)
        metadata = dict(params['line_items'][0]['price_data']['product_data']['metadata'])
        metadata.update(sku='MIKVET500', product_id='9999', slug='wrong', physical_quantity='6')
        stripe_line = SimpleNamespace(amount_total=5970, quantity=1, description='Wrong Stripe later title', price=SimpleNamespace(product=SimpleNamespace(metadata=metadata)))
        event = {'type': 'checkout.session.completed', 'data': {'object': {'id': 'cs_local_bf26_1', 'mode': 'payment', 'amount_total': 5970, 'payment_intent': 'pi_local', 'customer_email': CUSTOMER['email'], 'customer_details': {'email': CUSTOMER['email']}, 'metadata': params['metadata']}}}
        with patch('src.routes.stripe_routes.stripe.Webhook.construct_event', return_value=event), patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items', return_value=SimpleNamespace(data=[stripe_line])):
            response = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 'fixture'})
            self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.app_context():
            order = Order.query.one()
            self.assertEqual((order.items[0]['sku'], order.items[0]['quantity']), ('BF26-TEMP6', 1))
            self.assertEqual(order.items[0]['name'], CAMPAIGN_PACKS[0].name)
            self.assertEqual(order.receipt_snapshot['lines'][0]['amount_display'], '59,70 €')
            self.assertIn('2025/26', str(order.receipt_snapshot['lines']))
            order_data = {'items': order.items, 'receipt': order.receipt_snapshot, 'order_number': order.order_number, 'customer_email': CUSTOMER['email'], 'customer_name': CUSTOMER['name'], 'total': order.total}
        with patch('src.services.klaviyo_service.send_klaviyo_event', return_value=True) as event_sender:
            for sender in [klaviyo_send_order_confirmation, klaviyo_notify_new_order]:
                sender(order_data)
                properties = event_sender.call_args.kwargs['properties']
                self.assertIn('2025/26', properties['ItemsHtml'])
                self.assertEqual(properties['Receipt']['lines'], order_data['receipt']['lines'])
        prepared = prepare_document_items(order_data['items'], self.masters)
        self.assertEqual([(p['sku'], p['units']) for p in prepared], [('BF26-TEMP6', 1)])
        self.assertEqual(prepared[0]['name'], CAMPAIGN_PACKS[0].name)


class BF26ERPRecipeTests(unittest.TestCase):
    def test_every_valid_pack_emits_one_own_sku_line_with_units_one(self):
        masters = erp_fixtures()
        before = deepcopy(masters)
        for pack in CAMPAIGN_PACKS:
            items = [{'sku': pack.sku, 'name': 'Generic master', 'price': float(pack.price), 'quantity': 1}]
            prepared = prepare_document_items(items, masters)
            self.assertEqual(len(prepared), 1)
            self.assertEqual((prepared[0]['sku'], prepared[0]['units'], prepared[0]['name']), (pack.sku, 1, pack.name))
            rules = build_tax_rule_snapshot(items, masters, {})
            self.assertEqual(rules[pack.sku]['kind'], 'single')
        self.assertEqual(masters, before)

    def test_all_invalid_recipe_variants_fail_closed_in_documents_and_checkout_snapshot(self):
        for pack in CAMPAIGN_PACKS:
            for invalid in ['kind_missing', 'kind_product', 'recipe_missing', 'wrong_component', 'wrong_units', 'fractional_units', 'zero_units', 'negative_units', 'nan', 'infinity', 'extra_packaging', 'duplicate_component', 'missing_component_master', 'missing_pack_master', 'malformed_recipe']:
                with self.subTest(sku=pack.sku, invalid=invalid):
                    masters = erp_fixtures()
                    master = next(m for m in masters if m['sku'] == pack.sku)
                    component = master['pack_items'][0]
                    if invalid == 'kind_missing': master.pop('kind')
                    elif invalid == 'kind_product': master['kind'] = 'product'
                    elif invalid == 'recipe_missing': master['pack_items'] = []
                    elif invalid == 'wrong_component': component['product_selection'] = 'fixture-current' if pack.component_sku != 'MIKVET500' else 'fixture-extra'
                    elif invalid == 'wrong_units': component['units'] += 1
                    elif invalid == 'fractional_units': component['units'] = 1.5
                    elif invalid == 'zero_units': component['units'] = 0
                    elif invalid == 'negative_units': component['units'] = -1
                    elif invalid == 'nan': component['units'] = 'NaN'
                    elif invalid == 'infinity': component['units'] = 'Infinity'
                    elif invalid == 'extra_packaging': master['pack_items'].append({'product_selection': 'fixture-extra', 'units': 1})
                    elif invalid == 'duplicate_component': master['pack_items'].append(deepcopy(component))
                    elif invalid == 'missing_component_master': masters = [m for m in masters if m['sku'] != pack.component_sku]
                    elif invalid == 'missing_pack_master': masters = [m for m in masters if m['sku'] != pack.sku]
                    elif invalid == 'malformed_recipe': master['pack_items'] = ['bad']
                    items = [{'sku': pack.sku, 'price': float(pack.price), 'quantity': 1}]
                    with self.assertRaises(FiscalValidationError): prepare_document_items(items, masters)
                    with self.assertRaises(FiscalValidationError): build_tax_rule_snapshot(items, masters, {})

    def test_fractional_document_pack_quantity_is_not_component_expansion(self):
        with self.assertRaises(FiscalValidationError):
            prepare_document_items([{'sku': 'BF26-TEMP6', 'price': 59.70, 'quantity': 1.5}], erp_fixtures())

    def test_packitems_api_variant_is_supported_but_exact_components_required(self):
        masters = erp_fixtures()
        master = next(m for m in masters if m['sku'] == 'BF26-TEMP6')
        master['packItems'] = [{'productSelection': 'fixture-old', 'u': '6'}]
        master.pop('pack_items')
        index = build_holded_master_index(masters)
        self.assertEqual(resolve_tax_profile(master, index).status, 'single')


class BF26ERPAdminBarrierTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()
            order = Order(order_number='MKL-BF26-FIXTURE', customer_email=CUSTOMER['email'], customer_name=CUSTOMER['name'], shipping_address=CUSTOMER['address'], shipping_city=CUSTOMER['city'], shipping_postal_code=CUSTOMER['postal_code'], items=[{'sku': 'BF26-TEMP6', 'price': 59.70, 'quantity': 1}], subtotal=59.70, total=59.70, needs_invoice=False)
            db.session.add(order)
            db.session.commit()
            self.order_id = order.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_all_six_invalid_recipes_stop_before_contact_invoice_ticket_or_sales_order(self):
        for pack in CAMPAIGN_PACKS:
            with self.app.app_context():
                order = db.session.get(Order, self.order_id)
                order.items = [{'sku': pack.sku, 'price': float(pack.price), 'quantity': 1}]
                db.session.commit()
            masters = erp_fixtures()
            next(m for m in masters if m['sku'] == pack.sku)['pack_items'] = []
            for view in [admin_panel_routes.create_order_in_holded, admin_panel_routes.create_invoice_in_holded]:
                with patch.object(admin_panel_routes, 'holded_get_products', return_value=masters), patch.object(admin_panel_routes, 'holded_get_or_create_contact') as contact, patch.object(admin_panel_routes, 'holded_create_sales_order') as sales_order, patch.object(admin_panel_routes, 'holded_create_invoice') as invoice, patch.object(admin_panel_routes, 'holded_create_salesreceipt') as ticket, patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')), self.app.test_request_context('/fixture', method='POST'):
                    response, status = view.__wrapped__.__wrapped__(self.order_id)
                    self.assertEqual(status, 422)
                    self.assertEqual(response.get_json()['fiscal_validation'], 'failed')
                    contact.assert_not_called()
                    sales_order.assert_not_called()
                    invoice.assert_not_called()
                    ticket.assert_not_called()


if __name__ == '__main__':
    unittest.main()
