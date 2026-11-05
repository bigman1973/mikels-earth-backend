"""Offline security contract for the two owner-confirmed collaborator coupons.

Active BF fixtures exercise future saleability only; no catalogue is published.
All ERP/Stripe calls are mocks; strict Stripe semantics and network guards apply.
"""
import itertools
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.models.user import db
from src.models.web_product import WebProduct
from src.models.coupon import Coupon
from src.models.stock import StockReservation, StockMovement
from src.models.order import Order
from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot
from src.routes.coupon_routes import coupon_bp
from src.routes.stripe_routes import stripe_bp
from src.services.coupon_policy import (
    cart_blocks_coupon, coupon_allows_promotion, PROMOTION_COUPON_MESSAGE,
)
from src.services.protected_catalogue import is_mixed_with_full_price_only_product

COLLABORATORS = (('IRVIANCESTRAL', 286, 87), ('dr.gemmavalls', 281, 7))
PROTECTED = ('MIKVET500', 'MIKVE5LP', 'MIKPARA450', 'MIKPACKF', 'MIKPACKTP')
# Six distinct fixed-price BF pack containers. Their component units do not
# inherit the parent container's permission (or vice versa).
BF_PACKS = ('MIKVET500BF3', 'MIKVET500BF6', 'MIKBIO19BF6',
            'MIKPARA450BF3', 'MIKPARA450BF6', 'MIKVE5LPBF3')
DENIED_CODES = ('PUBLIC10', 'FIXED5', 'MIKELS-WELCOME', 'MIKELSFRIENDS', 'MIKELSFAMILY',
                'ME2025', 'BIENVENIDA10', 'COLLAB10', 'RETURN10', 'UNKNOWN', 'STRIPE-ONLY')


class CollaboratorPromotionCouponTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(stripe_bp)
        self.app.register_blueprint(coupon_bp, url_prefix='/api/coupon')
        self.customer = dict(name='Fixture', email='fixture@example.invalid', phone='fixture-phone',
                             address='Fixture', city='Fixture', postal_code='25001', country='España')
        self.ids = {}
        with self.app.app_context():
            db.create_all()
            for sku in (*BF_PACKS, *PROTECTED, 'REGULAR', 'HIDDENPROMO', 'HIDDENREGULAR', 'VOLUME', 'DRAFT'):
                product = WebProduct(name=sku, sku=sku, slug=sku.lower(), price=10, category='Fixture',
                    stock=1000, active=sku != 'DRAFT', visible_in_store=not sku.startswith('HIDDEN'),
                    in_promotion=sku in BF_PACKS or sku in ('HIDDENPROMO', 'DRAFT'),
                    subscription_available=True,
                    includes=['6 component units'] if sku in BF_PACKS else None,
                    volume_discount={'minQuantity': 3, 'discount': 10} if sku == 'VOLUME' else None)
                db.session.add(product)
                db.session.flush()
                self.ids[sku] = product.id
            for code, coupon_id, uses in COLLABORATORS:
                db.session.add(Coupon(id=coupon_id, code=code, discount_type='percentage', discount_value=10,
                    current_uses=uses, created_at=datetime(2026, 6, 26), active=True, valid_in_promotion=True))
            for code in DENIED_CODES:
                if code in ('UNKNOWN', 'STRIPE-ONLY'):
                    continue
                db.session.add(Coupon(code=code, id=283 if code == 'MIKELSFRIENDS' else None,
                    description='IRVIANCESTRAL colaborador promoción valid_in_promotion=true',
                    discount_type='fixed' if code == 'FIXED5' else 'percentage',
                    discount_value=5 if code == 'FIXED5' else 10, active=True))
            db.session.commit()
            self.before = self.snapshot()
        self.client = self.app.test_client()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        targets = {
            'session': 'src.routes.stripe_routes.stripe.checkout.Session.create',
            'coupon': 'src.routes.stripe_routes.stripe.Coupon.create',
            'price': 'src.routes.stripe_routes.stripe.Price.create',
            'promotion': 'src.routes.stripe_routes.stripe.PromotionCode.list',
            'reserve': 'src.services.stock_service.reserve_checkout_stock',
            'bind': 'src.services.stock_service.bind_reservation_to_session',
            'release': 'src.services.stock_service.release_checkout_reservation',
            'erp': 'src.services.holded_service.holded_get_products',
            'tax': 'src.services.order_tax_snapshot.build_tax_rule_snapshot',
            'network': 'requests.sessions.Session.request',
            'notify': 'src.routes.stripe_routes.dispatch_order_notification',
            'confirm': 'src.routes.stripe_routes.dispatch_order_confirmation',
        }
        self.effects = {key: self.stack.enter_context(patch(target)) for key, target in targets.items()}
        self.effects['session'].side_effect = self.strict_session
        self.effects['coupon'].side_effect = self.strict_coupon
        self.effects['price'].return_value = SimpleNamespace(id='price_fixture')
        self.effects['erp'].return_value = [{'sku': 'REGULAR'}]
        self.effects['tax'].return_value = {'REGULAR': {'kind': 'single', 'rate': '0.04'}}
        self.effects['network'].side_effect = AssertionError('No external network permitted')

    def strict_session(self, **params):
        self.assertFalse('discounts' in params and 'allow_promotion_codes' in params,
                         'Stripe discounts and allow_promotion_codes are mutually exclusive')
        self.assertIsNot(params.get('allow_promotion_codes'), True)
        self.assertNotIn('valid_in_promotion', params.get('metadata', {}))
        return SimpleNamespace(id=f'cs_fixture_{self.effects["session"].call_count}', url='https://stripe.invalid/fixture')

    def strict_coupon(self, **params):
        self.assertEqual(set(params), {'amount_off', 'currency', 'duration', 'name'})
        self.assertEqual(params['currency'], 'eur')
        self.assertEqual(params['duration'], 'once')
        self.assertIs(type(params['amount_off']), int)
        self.assertGreater(params['amount_off'], 0)
        return SimpleNamespace(id='coupon_fixture')

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def snapshot(self):
        return {
            'products': [{column.name: getattr(p, column.name) for column in WebProduct.__table__.columns}
                         for p in WebProduct.query.order_by(WebProduct.id)],
            'coupons': [(c.to_dict(), c.valid_in_promotion) for c in Coupon.query.order_by(Coupon.id)],
            'reservations': StockReservation.query.count(), 'movements': StockMovement.query.count(),
            'orders': Order.query.count(), 'tax_snapshots': CheckoutTaxSnapshot.query.count(),
        }

    def item(self, product_sku, **extra):
        return {'id': self.ids[product_sku], 'slug': product_sku.lower(), 'sku': product_sku,
                'price': 10, 'quantity': 1, **extra}

    def checkout(self, items, code='IRVIANCESTRAL', **extra):
        return self.client.post('/api/stripe/create-checkout-session', json={
            'items': items, 'customer_info': self.customer, 'discount_code': code, **extra})

    def subscription(self, item, code='IRVIANCESTRAL', **extra):
        return self.client.post('/api/stripe/create-subscription-checkout', json={
            'item': {**item, 'subscription_frequency': 'monthly'},
            'customer_info': self.customer, 'discount_code': code, **extra})

    def preview(self, code, items=None, **extra):
        payload = {'code': code, 'email': self.customer['email'], **extra}
        if items is not None:
            payload['items'] = items
        return self.client.post('/api/coupon/validate', json=payload)

    def use(self, code, items=None, **extra):
        payload = {'code': code, 'email': self.customer['email'], **extra}
        if items is not None:
            payload['items'] = items
        return self.client.post('/api/coupon/use', json=payload)

    def assert_no_effects(self):
        for key, mock in self.effects.items():
            with self.subTest(effect=key):
                mock.assert_not_called()
        with self.app.app_context():
            self.assertEqual(self.snapshot(), self.before)

    def assert_veto(self, response, status=422):
        self.assertEqual(response.status_code, status, response.get_json())
        self.assertEqual(response.get_json().get('error'), 'PROMOTION_NOT_AVAILABLE')
        self.assertEqual(response.get_json().get('message'), PROMOTION_COUPON_MESSAGE)
        self.assert_no_effects()

    def modify_coupon(self, code, **values):
        with self.app.app_context():
            coupon = Coupon.query.filter_by(code=code).one()
            for name, value in values.items():
                setattr(coupon, name, value)
            db.session.commit()
            self.before = self.snapshot()

    def reset_effects(self):
        for mock in self.effects.values():
            mock.reset_mock()

    def test_model_boolean_not_null_and_both_client_and_server_defaults_false(self):
        with self.app.app_context():
            column = Coupon.__table__.c.valid_in_promotion
            self.assertFalse(column.nullable)
            self.assertIs(column.default.arg, False)
            self.assertEqual(str(column.server_default.arg), 'false')
            coupon = Coupon(code='DEFAULT', discount_value=10)
            db.session.add(coupon)
            db.session.flush()
            self.assertIs(coupon.valid_in_promotion, False)
            db.session.execute(text("INSERT INTO coupons (code, discount_type, discount_value) VALUES ('RAW', 'percentage', 10)"))
            self.assertIs(Coupon.query.filter_by(code='RAW').one().valid_in_promotion, False)
            with self.assertRaises(IntegrityError):
                with db.session.begin_nested():
                    db.session.execute(text("INSERT INTO coupons (code, discount_type, discount_value, valid_in_promotion) VALUES ('NULL', 'percentage', 10, NULL)"))

    def test_helper_rejects_unpersisted_models_and_client_objects(self):
        with self.app.app_context():
            for value in (None, {}, {'code': 'IRVIANCESTRAL', 'valid_in_promotion': True},
                          SimpleNamespace(code='IRVIANCESTRAL', valid_in_promotion=True),
                          Coupon(id=286, code='IRVIANCESTRAL', active=True, used=False,
                                 valid_in_promotion=True, current_uses=0, min_order_amount=0)):
                self.assertIs(coupon_allows_promotion(value), False)
            for code, _, _ in COLLABORATORS:
                self.assertIs(coupon_allows_promotion(Coupon.query.filter_by(code=code).one()), True)
        self.assert_no_effects()

    def test_exact_stored_allowlist_not_matching_descriptions_or_nearby_codes(self):
        with self.app.app_context():
            for index, code in enumerate(('IRVIANCESTRAL2', 'IRVIANCESTRAL ', 'irviancestral',
                                          'DR.GEMMAVALLS', 'dr.gemmavalls2', ' dr.gemmavalls')):
                coupon = Coupon(code=code, active=True, valid_in_promotion=True,
                                description='Confirmed collaborator', discount_value=10)
                db.session.add(coupon)
                db.session.flush()
                with self.subTest(code=code):
                    self.assertFalse(coupon_allows_promotion(coupon))

    def test_case_insensitive_lookup_returns_stored_code_and_only_safe_public_fields(self):
        for code, _, _ in COLLABORATORS:
            for variant in (code, code.lower(), code.upper(), f' {code.swapcase()} '):
                with self.subTest(code=variant):
                    response = self.preview(variant)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.get_json()['valid'])
                    self.assertEqual(response.get_json()['coupon'], {
                        'code': code, 'discount_percentage': 10, 'discount_type': 'percentage',
                        'valid_in_promotion': True})
        self.assert_no_effects()

    def test_no_raw_flag_added_to_broader_to_dict_serialization(self):
        with self.app.app_context():
            for coupon in Coupon.query.all():
                self.assertNotIn('valid_in_promotion', coupon.to_dict())

    def test_every_bf_pack_both_collaborators_allowed_and_amount_server_calculated(self):
        for (code, _, _), sku in itertools.product(COLLABORATORS, BF_PACKS):
            with self.subTest(code=code, sku=sku):
                response = self.checkout([self.item(sku, in_promotion=False)], f' {code.lower()} ',
                    discount_amount=99999, valid_in_promotion=False)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 100)
                params = self.effects['session'].call_args.kwargs
                self.assertNotIn('allow_promotion_codes', params)
                self.assertEqual(params['discounts'], [{'coupon': 'coupon_fixture'}])
                self.assertEqual(params['metadata']['coupon_discount_amount'], '1.00')
                self.assertEqual(params['metadata']['total'], '9.00')
                line = params['line_items'][0]
                self.assertEqual(line['quantity'], 1)
                self.assertEqual(line['price_data']['unit_amount'], 1000)
                self.assertEqual(line['price_data']['product_data']['metadata']['sku'], sku)
                with self.app.app_context():
                    coupon = Coupon.query.filter_by(code=code).one()
                    self.assertEqual(coupon.current_uses, 87 if code == 'IRVIANCESTRAL' else 7)
                    self.assertFalse(coupon.used)

    def test_six_bf_pack_previews_both_collaborators_allowed(self):
        for (code, _, _), sku in itertools.product(COLLABORATORS, BF_PACKS):
            response = self.preview(code, [self.item(sku)])
            self.assertTrue(response.get_json()['valid'])
            self.assertIs(response.get_json()['coupon']['valid_in_promotion'], True)
        self.assert_no_effects()

    def test_six_bf_packs_ordinary_historical_friends_unknown_codes_veto(self):
        for code, sku in itertools.product(DENIED_CODES, BF_PACKS):
            with self.subTest(code=code, sku=sku):
                self.assert_veto(self.checkout([self.item(sku)], code))
                self.assert_veto(self.preview(code, [self.item(sku)]), 200)
                self.assert_veto(self.use(code, [self.item(sku)]))
                self.assert_veto(self.subscription(self.item(sku), code))

    def test_nonallowlisted_malicious_persisted_true_never_grants_capability(self):
        for code in DENIED_CODES:
            if code not in ('UNKNOWN', 'STRIPE-ONLY'):
                self.modify_coupon(code, valid_in_promotion=True)
                response = self.preview(code)
                self.assertTrue(response.get_json()['valid'])
                self.assertIs(response.get_json()['coupon']['valid_in_promotion'], False)
                self.assert_veto(self.checkout([self.item(BF_PACKS[0])], code))
                self.assert_veto(self.subscription(self.item(BF_PACKS[0]), code))
                self.assert_veto(self.preview(code, [self.item(BF_PACKS[0])]), 200)
                self.assert_veto(self.use(code, [self.item(BF_PACKS[0])]))

    def test_both_allowlisted_false_flags_fail_closed_even_with_client_true(self):
        for code, _, _ in COLLABORATORS:
            self.modify_coupon(code, valid_in_promotion=False)
            flags = {'valid_in_promotion': True, 'validInPromotion': True,
                     'coupon': {'code': code, 'valid_in_promotion': True},
                     'metadata': {'valid_in_promotion': True, 'collaborator': True}}
            self.assertIs(self.preview(code, **flags).get_json()['coupon']['valid_in_promotion'], False)
            self.assert_veto(self.checkout([self.item(BF_PACKS[0], **flags)], code, **flags))
            self.assert_veto(self.subscription(self.item(BF_PACKS[0], **flags), code, **flags))
            self.assert_veto(self.preview(code, [self.item(BF_PACKS[0])], **flags), 200)
            self.assert_veto(self.use(code, [self.item(BF_PACKS[0])], **flags))

    def test_invalid_expired_inactive_used_exhausted_collaborators_veto_all_flows(self):
        states = ({'active': False}, {'used': True},
                  {'expires_at': datetime.utcnow() - timedelta(seconds=1)},
                  {'max_uses': 100, 'current_uses': 100})
        for (code, _, uses), state in itertools.product(COLLABORATORS, states):
            self.modify_coupon(code, active=True, used=False, expires_at=None,
                               max_uses=None, current_uses=uses, **{})
            self.modify_coupon(code, **state)
            with self.subTest(code=code, state=state):
                self.assert_veto(self.checkout([self.item(BF_PACKS[0])], code))
                self.assert_veto(self.subscription(self.item(BF_PACKS[0]), code))
                self.assert_veto(self.preview(code, [self.item(BF_PACKS[0])]), 200)
                self.assert_veto(self.use(code, [self.item(BF_PACKS[0])]))
                self.assertFalse(self.preview(code).get_json()['valid'])

    def test_all_five_protected_skus_veto_both_collaborators_every_identity_and_route(self):
        for (code, _, _), sku in itertools.product(COLLABORATORS, PROTECTED):
            ordinary = self.item('REGULAR')
            attempts = (
                self.item(sku), {'id': self.ids[sku]}, {'slug': sku.lower()},
                {'sku': f' {sku.lower()} '},
                self.item(sku, slug='regular', sku='REGULAR'),
                {**ordinary, 'slug': sku.lower()}, {**ordinary, 'sku': sku},
                {**ordinary, 'id': self.ids[sku]},
                {'id': 999999, 'slug': sku.lower(), 'sku': 'REGULAR'},
            )
            for attempt in attempts:
                with self.subTest(code=code, sku=sku, identity=attempt):
                    self.assert_veto(self.checkout([attempt], code))
                    self.assert_veto(self.preview(code, [attempt]), 200)
                    self.assert_veto(self.use(code, [attempt]))
                    self.assert_veto(self.subscription(attempt, code))

    def test_protected_hidden_addons_and_all_mixed_line_orders_full_veto(self):
        for sku in PROTECTED:
            with self.app.app_context():
                product = WebProduct.query.get(self.ids[sku])
                product.visible_in_store = False
                product.in_promotion = True
                db.session.commit()
                self.before = self.snapshot()
            for code, _, _ in COLLABORATORS:
                lines = [self.item(BF_PACKS[0]), self.item('REGULAR'),
                         self.item(sku, isAddon=True, inPromotion=False, valid_in_promotion=True)]
                for ordered in itertools.permutations(lines):
                    self.assert_veto(self.checkout(list(ordered), code))
                    self.assert_veto(self.preview(code, list(ordered)), 200)
                    self.assert_veto(self.use(code, list(ordered)))

    def test_unresolved_raw_protected_sku_cannot_be_hidden_by_ordinary_id(self):
        with self.app.app_context():
            for sku in PROTECTED:
                db.session.delete(WebProduct.query.get(self.ids[sku]))
            db.session.commit()
            self.before = self.snapshot()
        for (code, _, _), sku in itertools.product(COLLABORATORS, PROTECTED):
            attempt = {**self.item('REGULAR'), 'sku': f' {sku.lower()} '}
            self.assert_veto(self.checkout([attempt], code))
            self.assert_veto(self.preview(code, [attempt]), 200)
            self.assert_veto(self.use(code, [attempt]))
            self.assert_veto(self.subscription(attempt, code))

    def test_generic_promotion_hidden_addon_only_relaxed_for_confirmed_codes(self):
        lines = [self.item('REGULAR'), self.item('HIDDENPROMO', isAddon=True, inPromotion=False)]
        for code in DENIED_CODES:
            self.assert_veto(self.checkout(lines, code))
        for code, _, _ in COLLABORATORS:
            response = self.checkout(lines, code)
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 200)

    def test_browser_promotion_flags_do_not_invent_generic_promotion(self):
        response = self.checkout([self.item('REGULAR', in_promotion=True, inPromotion=True)], 'PUBLIC10',
                                  valid_in_promotion=True)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 100)

    def test_allowlisted_product_identity_forgery_still_cannot_mask_protected_product(self):
        for sku in PROTECTED:
            attempt = self.item(BF_PACKS[0], slug=sku.lower(), sku='REGULAR')
            for code, _, _ in COLLABORATORS:
                self.assert_veto(self.checkout([attempt], code))
                self.assert_veto(self.preview(code, attempt), 200)

    def test_context_free_preview_is_not_trusted_after_flag_is_removed(self):
        self.assertTrue(self.preview('IRVIANCESTRAL').get_json()['coupon']['valid_in_promotion'])
        self.modify_coupon('IRVIANCESTRAL', valid_in_promotion=False)
        self.assert_veto(self.checkout([self.item(BF_PACKS[0])], 'IRVIANCESTRAL',
                         coupon={'code': 'IRVIANCESTRAL', 'valid_in_promotion': True}))

    def test_use_consumes_once_only_when_server_policy_allows(self):
        for code, _, uses in COLLABORATORS:
            response = self.use(code.swapcase(), [self.item(BF_PACKS[0])], valid_in_promotion=False)
            self.assertEqual(response.status_code, 200, response.get_json())
            with self.app.app_context():
                coupon = Coupon.query.filter_by(code=code).one()
                self.assertEqual(coupon.current_uses, uses + 1)
                self.assertTrue(coupon.active)
                self.assertFalse(coupon.used)
                self.assertTrue(coupon.valid_in_promotion)
        for mock in self.effects.values():
            mock.assert_not_called()

    def test_email_owner_restriction_preserved_all_flows_without_side_effects(self):
        for code, _, _ in COLLABORATORS:
            self.modify_coupon(code, email='other@example.invalid')
            result = self.checkout([self.item(BF_PACKS[0])], code)
            self.assertEqual(result.status_code, 422)
            self.assertEqual(result.get_json()['error'], 'COUPON_INVALID')
            self.assertIn('email', result.get_json()['message'])
            self.assertFalse(self.preview(code, [self.item(BF_PACKS[0])]).get_json()['valid'])
            self.assertEqual(self.use(code, [self.item(BF_PACKS[0])]).status_code, 400)
            self.assertEqual(self.subscription(self.item(BF_PACKS[0]), code).get_json()['error'], 'COUPON_INVALID')
            self.assert_no_effects()

    def test_existing_minimum_order_check_cannot_be_bypassed_by_collaborator_flag(self):
        for code, _, _ in COLLABORATORS:
            self.modify_coupon(code, min_order_amount=50)
            result = self.checkout([self.item(BF_PACKS[0])], code, discount_amount=0,
                                   order_amount=999999, subtotal=999999, valid_in_promotion=True)
            self.assertEqual(result.status_code, 422)
            self.assertEqual(result.get_json()['error'], 'COUPON_INVALID')
            self.assertIn('50', result.get_json()['message'])
            self.assertEqual(self.subscription(self.item(BF_PACKS[0]), code).get_json()['error'], 'COUPON_INVALID')
            self.assertEqual(self.subscription(self.item(BF_PACKS[0], price=999999), code).get_json()['error'],
                             'COUPON_INVALID')
            # The legacy context-free preview validates code/email, not an order minimum.
            self.assertTrue(self.preview(code).get_json()['valid'])
            self.assert_no_effects()

    def test_ordinary_invalid_unknown_codes_denied_before_erp_or_stripe(self):
        for code in ('UNKNOWN', 'STRIPE-ONLY'):
            self.assertEqual(self.checkout([self.item('REGULAR')], code).get_json()['error'], 'COUPON_INVALID')
            self.assertEqual(self.subscription(self.item('REGULAR'), code).get_json()['error'], 'COUPON_INVALID')
            self.assert_no_effects()
        for state in ({'active': False}, {'used': True}, {'max_uses': 3, 'current_uses': 3},
                      {'expires_at': datetime.utcnow() - timedelta(days=1)}):
            self.modify_coupon('IRVIANCESTRAL', active=True, used=False, max_uses=None,
                               current_uses=87, expires_at=None)
            self.modify_coupon('IRVIANCESTRAL', **state)
            self.assertEqual(self.checkout([self.item('REGULAR')]).get_json()['error'], 'COUPON_INVALID')
            self.assertEqual(self.subscription(self.item('REGULAR')).get_json()['error'], 'COUPON_INVALID')
            self.assert_no_effects()

    def test_collaborator_permission_does_not_activate_private_catalogue_drafts(self):
        response = self.checkout([self.item('DRAFT')])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'PRODUCT_UNAVAILABLE')
        self.assert_no_effects()

    def test_inherited_mixed_cart_gate_is_not_narrowed(self):
        with self.app.app_context():
            products = [WebProduct.query.get(self.ids[sku]) for sku in ('MIKVE5LP', 'REGULAR')]
            self.assertTrue(is_mixed_with_full_price_only_product(products))
        result = self.checkout([self.item('MIKVE5LP'), self.item('REGULAR')], None)
        self.assertEqual(result.status_code, 409)
        self.assert_no_effects()

    def test_welcome_and_legacy_creation_helpers_never_opt_in(self):
        with self.app.app_context():
            coupon = Coupon.create_welcome_coupon('welcome@example.invalid')
            self.assertIs(coupon.valid_in_promotion, False)
            self.assertEqual(coupon.discount_value, 10)
            self.assertEqual(coupon.max_uses, 1)
            self.assertEqual(coupon.max_uses_per_customer, 1)
            self.assertLess(abs((coupon.expires_at - datetime.utcnow()).total_seconds() - 30 * 86400), 5)
            db.session.flush()
            self.assertFalse(coupon_allows_promotion(coupon))
            legacy = Coupon.create_coupon(email='legacy@example.invalid')
            self.assertIs(legacy.valid_in_promotion, False)
            self.assertFalse(coupon_allows_promotion(legacy))

    def test_stripe_payload_metadata_or_promotion_ids_never_open_code_entry(self):
        overrides = {'allow_promotion_codes': True, 'discounts': [{'coupon': 'forged'}],
                     'coupon': 'forged', 'promotion_code': 'forged',
                     'valid_in_promotion': True, 'metadata': {'valid_in_promotion': True}}
        for code in (None, '', '   '):
            result = self.checkout([self.item(BF_PACKS[0])], code, **overrides)
            self.assertEqual(result.status_code, 200)
            params = self.effects['session'].call_args.kwargs
            self.assertIs(params['allow_promotion_codes'], False)
            self.assertNotIn('discounts', params)
            self.assertEqual(params['metadata']['coupon_discount_amount'], '0.00')
        self.effects['coupon'].assert_not_called()
        self.effects['promotion'].assert_not_called()

    def test_subscription_both_collaborators_share_policy_and_strict_stripe_contract(self):
        for code, _, _ in COLLABORATORS:
            response = self.subscription(self.item(BF_PACKS[0]), f' {code.swapcase()} ',
                allow_promotion_codes=True, valid_in_promotion=False, discount_amount=9999,
                discounts=[{'coupon': 'forged'}])
            self.assertEqual(response.status_code, 200, response.get_json())
            params = self.effects['session'].call_args.kwargs
            self.assertEqual(params['mode'], 'subscription')
            self.assertIs(params['allow_promotion_codes'], False)
            self.assertNotIn('discounts', params)
            self.assertNotIn('coupon_discount_amount', params['metadata'])
            self.effects['coupon'].assert_not_called()
            self.effects['promotion'].assert_not_called()

    def test_ordinary_friends_coupon_retains_normal_nonpromotion_validity(self):
        for code in ('MIKELSFRIENDS', 'FIXED5', 'PUBLIC10'):
            response = self.checkout([self.item('REGULAR')], code)
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 500 if code == 'FIXED5' else 100)
            self.assertIs(self.preview(code).get_json()['coupon']['valid_in_promotion'], False)

    def test_collaborator_volume_discount_preserves_existing_stacking_and_rounding(self):
        result = self.checkout([self.item(BF_PACKS[0]), self.item('VOLUME', quantity=3, price=9)])
        self.assertEqual(result.status_code, 200, result.get_json())
        params = self.effects['session'].call_args.kwargs
        self.assertEqual(params['metadata']['volume_discount_amount'], '3.00')
        self.assertEqual(params['metadata']['coupon_discount_amount'], '3.70')
        self.assertEqual(params['metadata']['total'], '33.30')
        self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 670)

    def test_eligibility_checks_leave_confirmed_coupon_identity_value_date_uses_untouched(self):
        for code, coupon_id, uses in COLLABORATORS:
            self.preview(code, [self.item(BF_PACKS[0])])
            with self.app.app_context():
                coupon = Coupon.query.filter_by(code=code).one()
                self.assertEqual(coupon.id, coupon_id)
                self.assertEqual(coupon.discount_value, 10)
                self.assertEqual(coupon.current_uses, uses)
                self.assertEqual(coupon.created_at, datetime(2026, 6, 26))
                self.assertEqual(coupon.min_order_amount, 0)
                self.assertIsNone(coupon.expires_at)
        self.assert_no_effects()


if __name__ == '__main__':
    unittest.main()
