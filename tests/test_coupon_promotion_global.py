"""Offline regression tests: real persisted promotion + ANY code vetoes checkout."""
import itertools
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from src.models.user import db
from src.models.web_product import WebProduct
from src.models.coupon import Coupon
from src.models.stock import StockReservation, StockMovement
from src.models.order import Order
from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot
from src.routes.coupon_routes import coupon_bp
from src.routes.stripe_routes import stripe_bp
from src.services.coupon_policy import cart_blocks_coupon, PROMOTION_COUPON_MESSAGE

MESSAGE = 'Este código no se puede aplicar porque el pedido incluye productos en promoción.'
CODES = ('PUBLIC10', 'FIXED5', 'MIKELS-WELCOME', 'COLLAB10', 'RETURN10',
         'UNPROVEN10', 'STRIPE-ONLY', 'UNKNOWN-CODE', 'BADTYPE', 'USED10', 'EXPIRED10', 'INACTIVE10')


class GlobalCouponPromotionTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(stripe_bp)
        self.app.register_blueprint(coupon_bp, url_prefix='/api/coupon')
        self.customer = dict(name='Fixture', email='fixture@example.invalid', phone='test-phone',
                             address='Fixture', city='Fixture', postal_code='25001', country='España')
        with self.app.app_context():
            db.create_all()
            self.ids = {}
            for slug, sku, promo, visible, volume in (
                ('regular', 'TESTREG', False, True, None),
                ('promoted', 'TESTPROMO', True, True, None),
                ('hidden-addon', 'TESTADDON', True, False, None),
                ('volume', 'TESTVOLUME', False, True, {'minQuantity': 3, 'discount': 10}),
                ('legacy-temprano', 'MIKVET500', False, True, None),
                ('hidden-regular', 'TESTHIDDEN', False, False, None),
                ('promoted-volume', 'TESTPROMOVOLUME', True, True, {'minQuantity': 3, 'discount': 10}),
            ):
                product = WebProduct(name=slug, slug=slug, sku=sku, price=10, category='Fixture',
                                     stock=100, active=True, visible_in_store=visible,
                                     in_promotion=promo, volume_discount=volume, subscription_available=True)
                db.session.add(product)
                db.session.flush()
                self.ids[slug] = product.id
            for code in CODES:
                if code in ('STRIPE-ONLY', 'UNKNOWN-CODE'):
                    continue
                db.session.add(Coupon(code=code, description={
                    'MIKELS-WELCOME': 'Cupón de bienvenida newsletter',
                    'COLLAB10': 'Colaborador', 'RETURN10': 'Próxima compra',
                }.get(code), discount_type='fixed' if code == 'FIXED5' else 'unknown' if code == 'BADTYPE' else 'percentage',
                    discount_value=5 if code == 'FIXED5' else 10,
                    active=code != 'INACTIVE10', used=code == 'USED10',
                    current_uses=1 if code == 'USED10' else 0,
                    email=self.customer['email'] if code in ('MIKELS-WELCOME', 'RETURN10') else None,
                    expires_at=datetime.utcnow() - timedelta(days=1) if code == 'EXPIRED10' else None))
            db.session.commit()
            self.before = self.snapshot()
        self.client = self.app.test_client()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.effects = {}
        targets = {
            'stripe_session': 'src.routes.stripe_routes.stripe.checkout.Session.create',
            'stripe_coupon': 'src.routes.stripe_routes.stripe.Coupon.create',
            'stripe_price': 'src.routes.stripe_routes.stripe.Price.create',
            'stripe_promotion': 'src.routes.stripe_routes.stripe.PromotionCode.list',
            'reserve': 'src.services.stock_service.reserve_checkout_stock',
            'bind': 'src.services.stock_service.bind_reservation_to_session',
            'release': 'src.services.stock_service.release_checkout_reservation',
            'erp': 'src.services.holded_service.holded_get_products',
            'tax': 'src.services.order_tax_snapshot.build_tax_rule_snapshot',
            'requests': 'requests.sessions.Session.request',
            'klaviyo': 'src.services.klaviyo_service.requests',
            'notify': 'src.routes.stripe_routes.dispatch_order_notification',
            'confirm': 'src.routes.stripe_routes.dispatch_order_confirmation',
        }
        for key, target in targets.items():
            self.effects[key] = self.stack.enter_context(patch(target))
        self.effects['stripe_session'].return_value = SimpleNamespace(id='cs_offline', url='https://stripe.invalid/offline')
        self.effects['stripe_coupon'].return_value = SimpleNamespace(id='coupon_offline')
        self.effects['stripe_price'].return_value = SimpleNamespace(id='price_offline')
        self.effects['erp'].return_value = [{'sku': 'TESTREG'}]
        self.effects['tax'].return_value = {'TESTREG': {'kind': 'single', 'rate': '0.04'}}
        self.effects['requests'].side_effect = AssertionError('Network forbidden in tests')

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def snapshot(self):
        return {
            'products': [(p.id, p.sku, p.price, p.stock, p.in_promotion, p.active,
                          p.visible_in_store, p.volume_discount) for p in WebProduct.query.order_by(WebProduct.id)],
            'coupons': [(c.id, c.code, c.discount_type, c.discount_value, c.expires_at,
                         c.min_order_amount, c.current_uses, c.used, c.active) for c in Coupon.query.order_by(Coupon.id)],
            'reservations': StockReservation.query.count(), 'movements': StockMovement.query.count(),
            'orders': Order.query.count(), 'snapshots': CheckoutTaxSnapshot.query.count(),
        }

    def item(self, product_slug, **extra):
        return {'id': self.ids[product_slug], 'slug': product_slug, 'price': 10, 'quantity': 1, **extra}

    def checkout(self, items, code='PUBLIC10', **extra):
        return self.client.post('/api/stripe/create-checkout-session', json={
            'items': items, 'customer_info': self.customer, 'discount_code': code, **extra})

    def assert_veto(self, response, status=422):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.get_json().get('error'), 'PROMOTION_NOT_AVAILABLE')
        self.assertEqual(response.get_json().get('message'), MESSAGE)
        self.assert_no_effects()

    def assert_no_effects(self):
        for key, mock in self.effects.items():
            with self.subTest(effect=key):
                mock.assert_not_called()
        with self.app.app_context():
            self.assertEqual(self.snapshot(), self.before)

    def test_literal_message_contract(self):
        self.assertEqual(PROMOTION_COUPON_MESSAGE, MESSAGE)

    def test_every_origin_type_and_all_three_line_orders_are_vetoed(self):
        lines = [self.item('regular'), self.item('promoted'), self.item('hidden-regular')]
        for code, ordered in itertools.product(CODES, itertools.permutations(lines)):
            with self.subTest(code=code, order=[v['slug'] for v in ordered]):
                self.assert_veto(self.checkout(list(ordered), code))

    def test_single_promoted_line_vetoes_even_zero_requested_discount(self):
        for code in CODES:
            with self.subTest(code=code):
                self.assert_veto(self.checkout([self.item('promoted')], code, discount_amount=0))

    def test_hidden_promoted_addon_vetoes_the_entire_order_in_both_orders(self):
        for code, order in itertools.product(CODES, ('first', 'last')):
            lines = [self.item('regular'), self.item('hidden-addon', isAddon=True, in_promotion=False)]
            if order == 'first':
                lines.reverse()
            with self.subTest(code=code, order=order):
                self.assert_veto(self.checkout(lines, code))

    def test_browser_flags_and_display_metadata_cannot_disable_db_promotion(self):
        for value in (False, None, 0, '', 'false'):
            with self.subTest(value=value):
                line = self.item('promoted', in_promotion=value, inPromotion=value, name='Regular', sku='TESTREG')
                self.assert_veto(self.checkout([line], discount_amount=0, in_promotion=False, inPromotion=False))

    def test_each_db_identity_is_checked_before_browser_canonicalization(self):
        combinations = (
            self.item('promoted', slug='regular', sku='TESTREG'),
            self.item('regular', slug='promoted', sku='TESTREG'),
            self.item('regular', sku='TESTPROMO'),
            {'id': self.ids['promoted'], 'price': 10, 'quantity': 1},
            {'slug': 'promoted', 'price': 10, 'quantity': 1},
            {'id': 999999, 'slug': 'promoted', 'price': 10, 'quantity': 1},
            {'id': self.ids['regular'], 'sku': ' testpromo ', 'price': 10, 'quantity': 1},
        )
        for line in combinations:
            with self.subTest(line_identity=tuple(line.keys())):
                self.assert_veto(self.checkout([line]))

    def test_bad_browser_prices_or_quantity_do_not_allow_effects_before_veto(self):
        for price, qty in ((0, 1), (9999, 1), (10, 0), (10, -1), (10, 'bad')):
            self.assert_veto(self.checkout([self.item('regular'), self.item('promoted', price=price, quantity=qty)]))

    def test_any_nonempty_code_case_and_whitespace_gets_same_veto(self):
        for code in ('public10', ' public10 ', ' UNKNOWN ', '0', 123, {'coupon': 'fake'}):
            with self.subTest(code_type=type(code).__name__):
                self.assert_veto(self.checkout([self.item('promoted')], code))

    def test_legacy_mikvet500_remains_vetoed_without_db_promotion(self):
        for code in CODES:
            self.assert_veto(self.checkout([self.item('legacy-temprano')], code))

    def test_legacy_mixed_cart_code_is_vetoed_before_mixed_gate(self):
        for lines in itertools.permutations([self.item('regular'), self.item('legacy-temprano')]):
            self.assert_veto(self.checkout(list(lines)))

    def test_preview_and_use_veto_all_origins_without_consuming_coupon(self):
        for code, route in itertools.product(CODES, ('validate', 'use')):
            payload = {'code': code, 'email': self.customer['email'], 'items': [self.item('regular'), self.item('promoted')]}
            with self.subTest(code=code, route=route):
                self.assert_veto(self.client.post('/api/coupon/' + route, json=payload), 200 if route == 'validate' else 422)

    def test_preview_checks_id_slug_sku_and_dict_context_independently(self):
        for context in ({'id': self.ids['promoted']}, {'slug': 'promoted'}, {'sku': 'TESTPROMO'},
                        self.item('regular', slug='promoted'), self.item('regular', sku='TESTPROMO')):
            self.assert_veto(self.client.post('/api/coupon/validate', json={'code': 'PUBLIC10', 'items': context}), 200)

    def test_browser_true_flags_cannot_invent_generic_promotion(self):
        line = self.item('regular', in_promotion=True, inPromotion=True)
        self.assertEqual(self.checkout([line]).status_code, 200)
        self.assertEqual(self.effects['stripe_coupon'].call_args.kwargs['amount_off'], 100)
        self.assertFalse(self.effects['stripe_session'].call_args.kwargs['allow_promotion_codes'])

    def test_context_free_preview_cannot_approve_a_subsequent_promoted_sale(self):
        result = self.client.post('/api/coupon/validate', json={'code': 'PUBLIC10'})
        self.assertTrue(result.get_json()['valid'])
        self.assert_no_effects()
        self.assert_veto(self.checkout([self.item('promoted')]))

    def test_no_code_or_whitespace_can_buy_promoted_product_without_coupon(self):
        for code in (None, '', '   '):
            with self.subTest(code=code):
                self.assertEqual(self.checkout([self.item('promoted')], code).status_code, 200)
                self.assertFalse(self.effects['stripe_session'].call_args.kwargs['allow_promotion_codes'])
                self.effects['stripe_coupon'].assert_not_called()

    def test_volume_discount_is_preserved_with_promotion_when_no_code(self):
        response = self.checkout([self.item('promoted'), self.item('volume', price=9, quantity=3)], None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.effects['stripe_coupon'].call_args.kwargs['amount_off'], 300)
        metadata = self.effects['stripe_session'].call_args.kwargs['metadata']
        self.assertEqual(metadata['volume_discount_amount'], '3.00')
        self.assertEqual(metadata['coupon_discount_amount'], '0.00')
        self.assertEqual(metadata['total'], '37.00')

    def test_own_volume_discount_is_preserved_on_promoted_product_without_code(self):
        result = self.checkout([self.item('promoted-volume', price=9, quantity=3)], '')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.effects['stripe_coupon'].call_args.kwargs['amount_off'], 300)

    def test_volume_plus_code_remains_valid_when_no_product_is_promoted(self):
        result = self.checkout([self.item('regular'), self.item('volume', price=9, quantity=3)], 'PUBLIC10')
        self.assertEqual(result.status_code, 200)
        params = self.effects['stripe_session'].call_args.kwargs
        self.assertEqual(params['metadata']['total'], '33.30')
        self.assertEqual(self.effects['stripe_coupon'].call_args.kwargs['amount_off'], 670)
        self.assertFalse(params['allow_promotion_codes'])

    def test_fixed_coupon_remains_valid_when_cart_has_no_promotion(self):
        self.assertEqual(self.checkout([self.item('regular')], 'FIXED5').status_code, 200)
        self.assertEqual(self.effects['stripe_coupon'].call_args.kwargs['amount_off'], 500)

    def test_unknown_code_on_ordinary_cart_is_invalid_not_silently_applied(self):
        response = self.checkout([self.item('regular')], 'STRIPE-ONLY')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()['error'], 'COUPON_INVALID')
        for key in ('stripe_session', 'stripe_coupon', 'reserve'):
            self.effects[key].assert_not_called()

    def test_subscription_code_veto_precedes_price_creation_and_other_effects(self):
        for code in CODES:
            result = self.client.post('/api/stripe/create-subscription-checkout', json={
                'item': self.item('promoted', subscription_frequency='monthly'),
                'customer_info': self.customer, 'discount_code': code})
            self.assert_veto(result)

    def test_subscription_stripe_promotion_input_remains_closed(self):
        response = self.client.post('/api/stripe/create-subscription-checkout', json={
            'item': self.item('regular', subscription_frequency='monthly'), 'customer_info': self.customer,
            'allow_promotion_codes': True, 'discounts': [{'coupon': 'payload'}]})
        self.assertEqual(response.status_code, 200)
        params = self.effects['stripe_session'].call_args.kwargs
        self.assertFalse(params['allow_promotion_codes'])
        self.assertNotIn('discounts', params)

    def test_stripe_payload_overrides_cannot_open_promotion_input(self):
        response = self.checkout([self.item('regular')], None, allow_promotion_codes=True,
                                 discounts=[{'coupon': 'payload'}], coupon='payload', promotion_code='payload',
                                 discount_amount=100000)
        self.assertEqual(response.status_code, 200)
        params = self.effects['stripe_session'].call_args.kwargs
        self.assertFalse(params['allow_promotion_codes'])
        self.assertNotIn('discounts', params)
        self.assertEqual(params['metadata']['discount_amount'], '0.00')
        self.effects['stripe_coupon'].assert_not_called()

    def test_missing_or_noncart_context_never_trusts_browser_promotion_flags(self):
        with self.app.app_context():
            for context in (None, 'invalid', [], [None, 'invalid'], {'inPromotion': True}, {'in_promotion': True}):
                self.assertFalse(cart_blocks_coupon(context))

    def test_database_promotion_change_is_observed_on_next_request(self):
        with self.app.app_context():
            WebProduct.query.get(self.ids['regular']).in_promotion = True
            db.session.commit()
            self.before = self.snapshot()
        self.assert_veto(self.checkout([self.item('regular')]))


if __name__ == '__main__':
    unittest.main()
