"""Harvest integrity tests. All DBs are in-memory and external calls mocked."""
import unittest
import os
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot
from src.models.order import Order
from src.models.stock import StockMovement, StockReservation
from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.abandoned_cart_routes import _canonical_cart_items
from src.routes.product_routes import product_bp
from src.routes.stripe_routes import CHECKOUT_PRICING_SNAPSHOT_KEY, stripe_bp
from src.services.holded_service import (
    holded_create_invoice, holded_create_sales_order, holded_create_salesreceipt,
)
from src.services.holded_tax_service import FiscalValidationError, prepare_document_items
from src.services.klaviyo_service import klaviyo_notify_new_order, klaviyo_send_order_confirmation
from src.services.previous_harvest_draft import DRAFT_TITLE, PROPOSED_SKU, PROPOSED_SLUG, seed_previous_harvest_draft
from src.services.product_identity import harvest_product_name
from src.services.protected_catalogue import is_mixed_with_full_price_only_product
from src.services.stock_service import StockUnavailableError, available_stock_by_product_ids, reserve_checkout_stock, restock_fully_refunded_order

CURRENT_TITLE = 'Aceite de oliva virgen extra Temprano 2026/27 sin filtrar'
MASTERS = [
    {'id': 'erp-current', 'sku': 'MIKVET500', 'name': 'Aceite Temprano', 'stock': 44, 'taxes': ['s_iva_4']},
    {'id': 'erp-old', 'sku': PROPOSED_SKU, 'name': 'Aceite Temprano', 'stock': 1200, 'taxes': ['s_iva_4']},
]
CUSTOMER = {
    'name': 'Cliente Fixture', 'email': 'fixture@example.test', 'phone': '+34600000000',
    'address': 'Calle Fixture 1', 'city': 'Lleida', 'postal_code': '25001', 'country': 'España',
}


class TempranoHarvestIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(stripe_bp)
        self.app.register_blueprint(product_bp, url_prefix='/api')
        with self.app.app_context():
            db.create_all()
            current = WebProduct(name=CURRENT_TITLE, slug='aceite-temprano-sin-filtrar', sku='MIKVET500', price=19.90, category='Aceites', stock=44, active=True, visible_in_store=True)
            db.session.add(current)
            old, _, _ = seed_previous_harvest_draft()
            db.session.commit()
            self.current_id, self.old_id = current.id, old.id
        self.client = self.app.test_client()
        for target, kwargs in [
            ('src.services.holded_service.holded_get_products', {'return_value': deepcopy(MASTERS)}),
            ('src.routes.stripe_routes.stripe.checkout.Session.create', {'return_value': SimpleNamespace(id='cs_fixture_harvest', url='https://checkout.stripe.test/fixture')}),
            ('src.routes.stripe_routes.stripe.Coupon.create', {}),
            ('src.routes.stripe_routes.dispatch_order_confirmation', {'return_value': (True, None)}),
            ('src.routes.stripe_routes.dispatch_order_notification', {'return_value': (True, None)}),
            ('src.routes.stripe_routes.dispatch_order_delivery_alert', {}),
            ('src.services.email_dispatcher.dispatch_post_purchase_event', {}),
            ('src.services.meta_conversions_api.dispatch_meta_purchase_event', {'return_value': {'sent': False, 'reason': 'fixture'}}),
        ]:
            patcher = patch(target, **kwargs)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            if target.endswith('holded_get_products'):
                self.holded = mock
            elif target.endswith('Session.create'):
                self.create_session = mock
            elif target.endswith('Coupon.create'):
                self.create_coupon = mock
            elif target.endswith('dispatch_order_confirmation'):
                self.confirmation = mock
            elif target.endswith('dispatch_order_notification'):
                self.notification = mock

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def activate_fixture(self):
        # This is test-only future saleability, not publication of the real draft.
        with self.app.app_context():
            db.session.get(WebProduct, self.old_id).active = True
            db.session.commit()

    def items(self, old_quantity=6):
        return [
            {'id': self.old_id, 'slug': PROPOSED_SLUG, 'sku': 'MIKVET500', 'name': 'Browser says CURRENT 2026/27', 'quantity': old_quantity, 'price': 9.95},
            {'id': self.current_id, 'slug': 'aceite-temprano-sin-filtrar', 'sku': PROPOSED_SKU, 'name': 'Browser says OLD 2025/26', 'quantity': 2, 'price': 19.90},
        ]

    def checkout(self, items=None, **extra):
        return self.client.post('/api/stripe/create-checkout-session', json={'items': items or self.items(), 'customer_info': CUSTOMER, **extra})

    def complete(self):
        params = self.create_session.call_args.kwargs
        lines = []
        for line in params['line_items']:
            price = line['price_data']
            metadata = dict(price['product_data']['metadata'])
            metadata.update(sku='WRONG-LATER-SKU', slug='wrong-later-slug', product_id='99999', physical_quantity='999')
            lines.append(SimpleNamespace(amount_total=price['unit_amount'] * line['quantity'], quantity=line['quantity'], description='Changed Stripe description', price=SimpleNamespace(product=SimpleNamespace(metadata=metadata))))
        event = {'type': 'checkout.session.completed', 'data': {'object': {
            'id': 'cs_fixture_harvest', 'mode': 'payment', 'amount_total': sum(line.amount_total for line in lines),
            'payment_intent': 'pi_fixture_harvest', 'customer_email': CUSTOMER['email'],
            'customer_details': {'email': CUSTOMER['email']}, 'metadata': params['metadata'],
        }}}
        with patch('src.routes.stripe_routes.stripe.Webhook.construct_event', return_value=event), patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items', return_value=SimpleNamespace(data=lines)):
            response = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 'fixture'})
            self.assertEqual(response.status_code, 200, response.get_json())
            retry = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 'fixture'})
            self.assertTrue(retry.get_json()['duplicate'])

    def test_private_draft_remains_unpublished_and_unpurchasable(self):
        self.assertEqual(self.client.get('/api/products/' + PROPOSED_SLUG).status_code, 404)
        response = self.checkout()
        self.assertEqual(response.status_code, 409)
        self.create_session.assert_not_called()
        self.holded.assert_not_called()
        with self.app.app_context():
            old = db.session.get(WebProduct, self.old_id)
            self.assertFalse(old.active)
            self.assertFalse(old.visible_in_store)
            self.assertEqual((old.sku, old.slug, old.stock), (PROPOSED_SKU, PROPOSED_SLUG, 1200))
            self.assertEqual(db.session.get(WebProduct, self.current_id).stock, 44)

    def test_exact_harvest_exception_does_not_expand_other_mixed_policy(self):
        for skus in (['MIKVET500', PROPOSED_SKU], [PROPOSED_SKU, 'MIKVET500', 'MIKVET500']):
            self.assertFalse(is_mixed_with_full_price_only_product(skus))
        for skus in (['MIKVET500', 'MIKOTHER'], ['MIKVE5LP', PROPOSED_SKU], ['MIKVET500', PROPOSED_SKU, 'MIKOTHER'], ['MIKVET500', 'MIKVET5002526D1']):
            self.assertTrue(is_mixed_with_full_price_only_product(skus))

    def test_both_pack_sizes_charge_per_line_without_global_discount(self):
        self.activate_fixture()
        for quantity, old_cents, total in [(6, 5970, '99.50'), (12, 11940, '159.20')]:
            with self.subTest(quantity=quantity):
                response = self.checkout(self.items(quantity), discount_amount=999)
                self.assertEqual(response.status_code, 200, response.get_json())
                params = self.create_session.call_args.kwargs
                old, current = params['line_items']
                self.assertEqual((old['quantity'], old['price_data']['unit_amount']), (1, old_cents))
                self.assertEqual((current['quantity'], current['price_data']['unit_amount']), (2, 1990))
                self.assertIn('2025/26', old['price_data']['product_data']['name'])
                self.assertIn('2026/27', current['price_data']['product_data']['name'])
                self.assertNotIn('Browser', str(params['line_items']))
                self.assertEqual(params['metadata']['total'], total)
                self.assertEqual(params['metadata']['discount_amount'], '0.00')
                self.assertNotIn('discounts', params)
        self.create_coupon.assert_not_called()

    def test_coupon_is_rejected_in_both_orders_before_external_checkout(self):
        self.activate_fixture()
        for items in (self.items(), self.items()[::-1]):
            response = self.checkout(items, discount_code='ANY-CODE')
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
        self.create_coupon.assert_not_called()
        self.create_session.assert_not_called()
        self.holded.assert_not_called()

    def test_unrelated_volume_tier_cannot_indirectly_discount_current_harvest(self):
        self.activate_fixture()
        with self.app.app_context():
            old = db.session.get(WebProduct, self.old_id)
            old.tiered_discount = [{'minQuantity': 6, 'discount': 10}]
            old.price = 9.95
            db.session.commit()
        items = self.items()
        items[0]['price'] = 8.955
        response = self.checkout(items)
        self.assertEqual(response.status_code, 422, response.get_json())
        self.assertEqual(response.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
        self.create_coupon.assert_not_called()
        self.create_session.assert_not_called()

    def test_paid_order_receipt_emails_and_holded_keep_saved_harvest_identity(self):
        self.activate_fixture()
        response = self.checkout()
        self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.app_context():
            pending = CheckoutTaxSnapshot.query.one().rules[CHECKOUT_PRICING_SNAPSHOT_KEY]
            self.assertEqual([pending[str(i)]['id'] for i in range(2)], [self.old_id, self.current_id])
            self.assertEqual(available_stock_by_product_ids([self.old_id, self.current_id]), {self.old_id: 1194, self.current_id: 42})
            self.assertEqual(db.session.get(WebProduct, self.old_id).stock, 1200)
            # Names changed after checkout must not leak to confirmation/order.
            db.session.get(WebProduct, self.old_id).name = 'Catalogue later changed old'
            db.session.get(WebProduct, self.current_id).name = 'Catalogue later changed current'
            db.session.commit()
        self.complete()
        with self.app.app_context():
            order = Order.query.one()
            self.assertEqual([item['id'] for item in order.items], [self.old_id, self.current_id])
            self.assertEqual([item['sku'] for item in order.items], [PROPOSED_SKU, 'MIKVET500'])
            self.assertEqual([item['quantity'] for item in order.items], [6, 2])
            self.assertEqual([line['amount_display'] for line in order.receipt_snapshot['lines']], ['59,70 €', '39,80 €'])
            self.assertEqual(order.items[0]['name'], DRAFT_TITLE)
            self.assertEqual(order.items[1]['name'], CURRENT_TITLE)
            self.assertEqual(order.receipt_snapshot['totals']['total_display'], '99,50 €')
            self.assertEqual(db.session.get(WebProduct, self.old_id).stock, 1194)
            self.assertEqual(db.session.get(WebProduct, self.current_id).stock, 42)
            self.assertEqual({r.product_id: r.quantity for r in StockReservation.query.filter_by(status='consumed')}, {self.old_id: 6, self.current_id: 2})
            self.assertEqual({m.product_id: m.quantity_delta for m in StockMovement.query.filter_by(reason='payment_capture')}, {self.old_id: -6, self.current_id: -2})
            restock_fully_refunded_order(order.id, 'fixture-refund')
            restock_fully_refunded_order(order.id, 'fixture-refund')
            db.session.commit()
            self.assertEqual(db.session.get(WebProduct, self.old_id).stock, 1200)
            self.assertEqual(db.session.get(WebProduct, self.current_id).stock, 44)
        order_data = self.confirmation.call_args.args[0]
        self.assertEqual(order_data['receipt']['lines'], self.notification.call_args.args[0]['receipt']['lines'])
        self.assertNotIn('Changed', str(order_data['items']))
        with patch('src.services.klaviyo_service.send_klaviyo_event', return_value=True) as event:
            for sender in (klaviyo_send_order_confirmation, klaviyo_notify_new_order):
                sender(order_data)
                properties = event.call_args.kwargs['properties']
                self.assertEqual(properties['Receipt']['lines'], order_data['receipt']['lines'])
                self.assertIn('2025/26', properties['ItemsHtml'])
                self.assertIn('2026/27', properties['ItemsHtml'])
        prepared = prepare_document_items(order_data['items'], MASTERS)
        self.assertEqual([(item['sku'], item['units']) for item in prepared], [(PROPOSED_SKU, 6), ('MIKVET500', 2)])
        with patch('src.services.holded_service.requests.post') as post:
            post.return_value = SimpleNamespace(status_code=201, json=lambda: {'id': 'fixture-document'})
            for creator in (holded_create_sales_order, holded_create_salesreceipt, holded_create_invoice):
                self.assertTrue(creator('fixture-contact', prepared)[0])
                payload = post.call_args.kwargs['json']['items']
                self.assertEqual([line['sku'] for line in payload], [PROPOSED_SKU, 'MIKVET500'])
                self.assertEqual(payload[0]['name'], DRAFT_TITLE)
                self.assertEqual(payload[1]['name'], CURRENT_TITLE)

    def test_old_stock_never_borrows_current_stock(self):
        self.activate_fixture()
        with self.app.app_context():
            old = db.session.get(WebProduct, self.old_id)
            old.stock = 5
            db.session.commit()
        response = self.checkout()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'OUT_OF_STOCK')
        self.create_session.assert_not_called()
        with self.app.app_context():
            self.assertEqual(db.session.get(WebProduct, self.current_id).stock, 44)
            self.assertEqual(StockReservation.query.count(), 0)
            reserve_checkout_stock([{'id': self.current_id, 'name': CURRENT_TITLE, 'quantity': 44}], 'current-only')
            db.session.commit()
            with self.assertRaises(StockUnavailableError):
                reserve_checkout_stock([{'id': self.old_id, 'name': DRAFT_TITLE, 'quantity': 6}], 'old-only')

    def test_missing_old_holded_master_never_aliases_to_current(self):
        self.activate_fixture()
        self.holded.return_value = MASTERS[:1]
        response = self.checkout()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()['error'], 'TAX_CONFIGURATION_INVALID')
        self.assertIn(PROPOSED_SKU, response.get_json()['message'])
        self.create_session.assert_not_called()
        with self.assertRaisesRegex(FiscalValidationError, PROPOSED_SKU):
            prepare_document_items([{'sku': PROPOSED_SKU, 'name': 'Aceite Temprano', 'quantity': 6, 'price': 9.95}], MASTERS[:1])

    def test_names_and_recovery_identity_apply_only_to_exact_harvest_skus(self):
        self.assertEqual(harvest_product_name('MIKVET500', "MIKEL'S VE 500ML TEMPRANO"), CURRENT_TITLE)
        self.assertEqual(harvest_product_name(PROPOSED_SKU, 'Temprano 2026-27'), DRAFT_TITLE)
        self.assertEqual(harvest_product_name('MIKVET500R', 'Temprano'), 'Temprano')
        with self.app.app_context():
            lines = _canonical_cart_items(self.items())
        self.assertEqual([item['sku'] for item in lines], [PROPOSED_SKU, 'MIKVET500'])
        self.assertIn('2025/26', lines[0]['name'])
        self.assertIn('2026/27', lines[1]['name'])

    def test_panel_maps_holded_stock_and_web_id_only_by_exact_sku(self):
        with patch.dict(os.environ, {'SECRET_KEY': 'fixture-session', 'JWT_SECRET': 'fixture-jwt', 'ADMIN_SECRET_KEY': 'fixture-admin'}):
            from src.routes import admin_panel_routes
        with patch.object(admin_panel_routes, 'holded_get_products', return_value=MASTERS), self.app.test_request_context('/products'):
            result = admin_panel_routes.get_products.__wrapped__().get_json()
        by_sku = {item['sku']: item for item in result['products']}
        self.assertEqual((by_sku[PROPOSED_SKU]['id'], by_sku[PROPOSED_SKU]['holded_id'], by_sku[PROPOSED_SKU]['stock']), (self.old_id, 'erp-old', 1200))
        self.assertEqual((by_sku['MIKVET500']['id'], by_sku['MIKVET500']['holded_id'], by_sku['MIKVET500']['stock']), (self.current_id, 'erp-current', 44))
        self.assertFalse(by_sku[PROPOSED_SKU]['active'])
        self.assertFalse(by_sku[PROPOSED_SKU]['visibleInStore'])
        with patch.object(admin_panel_routes, 'holded_get_products', return_value=MASTERS[:1]), self.app.test_request_context('/products'):
            result = admin_panel_routes.get_products.__wrapped__().get_json()
        by_sku = {item['sku']: item for item in result['products']}
        self.assertEqual(by_sku[PROPOSED_SKU]['source'], 'web_only')
        self.assertIsNone(by_sku[PROPOSED_SKU]['holded_id'])
        self.assertEqual(by_sku[PROPOSED_SKU]['stock'], 1200)
        self.assertEqual(by_sku['MIKVET500']['holded_id'], 'erp-current')

    def test_legacy_missing_identity_never_guesses_current_from_shared_name(self):
        with self.app.app_context():
            db.session.get(WebProduct, self.old_id).name = 'Shared legacy Temprano'
            db.session.get(WebProduct, self.current_id).name = 'Shared legacy Temprano'
            db.session.add(CheckoutTaxSnapshot(checkout_token='legacy-token', rules={'MIKVET500': {'kind': 'single', 'rate': '0.04'}}, expires_at=datetime.utcnow() + timedelta(minutes=30)))
            db.session.commit()
        base = {'id': 'cs_legacy_fixture', 'mode': 'payment', 'amount_total': 1990, 'payment_intent': 'pi_legacy_fixture', 'metadata': {'order_number': 'MKL-LEGACY-FIXTURE', 'stock_checkout_token': 'legacy-token'}}
        for metadata in ({}, {'slug': 'missing-old-harvest-slug'}):
            line = SimpleNamespace(amount_total=1990, quantity=1, description='Shared legacy Temprano', price=SimpleNamespace(product=SimpleNamespace(metadata=metadata)))
            with patch('src.routes.stripe_routes.stripe.Webhook.construct_event', return_value={'type': 'checkout.session.completed', 'data': {'object': base}}), patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items', return_value=SimpleNamespace(data=[line])):
                response = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 'fixture'})
            self.assertEqual(response.status_code, 500)
        with self.app.app_context():
            self.assertEqual(Order.query.count(), 0)
            self.assertEqual(db.session.get(WebProduct, self.current_id).stock, 44)
        self.confirmation.assert_not_called()
        self.notification.assert_not_called()


if __name__ == '__main__':
    unittest.main()
