"""Offline security contract for the two owner-confirmed collaborator coupons.

Active BF fixtures exercise future saleability only; no catalogue is published.
All ERP/Stripe calls are mocks; strict Stripe semantics and network guards apply.
"""
import itertools
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
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
from src.services.protected_catalogue import contains_promotion_protected_product

COLLABORATORS = (('IRVIANCESTRAL', 286, 87), ('dr.gemmavalls', 281, 7))
PROTECTED = ('MIKVET500', 'MIKVE5LP', 'MIKPARA450', 'MIKPACKF', 'MIKPACKTP')
# Six distinct fixed-price BF pack containers. Their component units do not
# inherit the parent container's permission (or vice versa).
BF_PACKS = ('BF26-TEMP6', 'BF26-TEMP12', 'BF26-ECO6',
            'BF26-PARA3', 'BF26-PARA6', 'BF26-5L3')
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
        # Actual branch BF26 containers must be tested inside their future
        # saleability window, without depending on wall-clock campaign expiry.
        self.stack.enter_context(patch('src.services.bf26_checkout_cutoff.utc_now',
                                      return_value=datetime(2026, 11, 15, tzinfo=timezone.utc)))
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
                        'min_order_amount': 0, 'valid_in_promotion': True})
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

    def test_all_five_ordinary_protected_skus_allow_both_collaborators_every_identity_except_subscription(self):
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
            for index, attempt in enumerate(attempts):
                with self.subTest(code=code, sku=sku, identity=attempt):
                    self.assertTrue(self.preview(code, [attempt]).get_json()['valid'])
                    self.assertEqual(self.use(code, [attempt]).status_code, 200)
                    result = self.checkout([{'price': 10, 'quantity': 1, **attempt}], code)
                    # Coupon policy accepts all ordinary identities, but this
                    # branch independently rejects contradictions in checkout.
                    if index >= 4:
                        self.assertEqual(result.status_code, 409)
                        self.assertEqual(result.get_json()['error'], 'PRODUCT_IDENTITY_MISMATCH')
                        for mock in self.effects.values():
                            mock.assert_not_called()
                    elif index == 3:
                        self.assertEqual(result.status_code, 409)
                        self.assertEqual(result.get_json()['error'], 'PRODUCT_NOT_FOUND')
                    else:
                        self.assertEqual(result.status_code, 200, result.get_json())
                        self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 100)
                        self.assertEqual(self.effects['session'].call_args.kwargs['metadata']['volume_discount_amount'], '0.00')
                    self.reset_effects()
                    result = self.subscription(attempt, code)
                    self.assertEqual(result.status_code, 422)
                    self.assertEqual(result.get_json()['message'], 'La suscripción no está disponible para este producto.')
                    for mock in self.effects.values():
                        mock.assert_not_called()

    def test_protected_hidden_promo_addons_allow_collaborators_when_own_mixed_gate_permits_all_orders(self):
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
                    self.assertTrue(self.preview(code, list(ordered)).get_json()['valid'])
                    self.assertEqual(self.use(code, list(ordered)).status_code, 200)
                    self.reset_effects()
                    result = self.checkout(list(ordered), code)
                    self.assertEqual(result.status_code, 200, result.get_json())
                    params = self.effects['session'].call_args.kwargs
                    self.assertEqual(params['metadata']['subtotal'], '30.00')
                    self.assertEqual(params['metadata']['coupon_discount_amount'], '3.00')
                    self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                    self.assertEqual(params['metadata']['total'], '27.00')
                    self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 300)

    def test_unresolved_raw_ordinary_protected_sku_is_not_a_coupon_veto_but_subscription_still_fails(self):
        with self.app.app_context():
            for sku in PROTECTED:
                db.session.delete(WebProduct.query.get(self.ids[sku]))
            db.session.commit()
            self.before = self.snapshot()
        for (code, _, _), sku in itertools.product(COLLABORATORS, PROTECTED):
            attempt = {**self.item('REGULAR'), 'sku': f' {sku.lower()} '}
            self.assertTrue(self.preview(code, [attempt]).get_json()['valid'])
            self.assertEqual(self.use(code, [attempt]).status_code, 200)
            self.assertEqual(self.checkout([attempt], code).status_code, 200)
            self.reset_effects()
            result = self.subscription(attempt, code)
            self.assertEqual(result.status_code, 422)
            self.assertEqual(result.get_json()['message'], 'La suscripción no está disponible para este producto.')
            for mock in self.effects.values():
                mock.assert_not_called()

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

    def test_allowlisted_conflicting_ordinary_protected_identity_has_no_coupon_veto(self):
        for sku in PROTECTED:
            attempt = self.item(BF_PACKS[0], slug=sku.lower(), sku='REGULAR')
            for code, _, _ in COLLABORATORS:
                self.assertTrue(self.preview(code, attempt).get_json()['valid'])
                result = self.checkout([attempt], code)
                self.assertEqual(result.status_code, 409, result.get_json())
                self.assertEqual(result.get_json()['error'], 'PRODUCT_IDENTITY_MISMATCH')
                self.assert_no_effects()

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
            # A generic promoted product reaches subtotal validation; actual
            # BF26 containers have a stricter independent subscription veto.
            self.assertEqual(self.subscription(self.item('HIDDENPROMO'), code).get_json()['error'], 'COUPON_INVALID')
            self.assertEqual(self.subscription(self.item('HIDDENPROMO', price=999999), code).get_json()['error'],
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

    def test_inherited_narrowed_mixed_cart_permits_coupons_but_retains_quantity_veto(self):
        with self.app.app_context():
            products = [WebProduct.query.get(self.ids[sku]) for sku in ('MIKVE5LP', 'REGULAR')]
            self.assertTrue(contains_promotion_protected_product(products))
        result = self.checkout([self.item('MIKVE5LP'), self.item('REGULAR')], None)
        self.assertEqual(result.status_code, 200, result.get_json())
        params = self.effects['session'].call_args.kwargs
        self.assertEqual(params['metadata']['total'], '20.00')
        self.assertEqual(params['metadata']['discount_amount'], '0.00')
        self.assertNotIn('discounts', params)
        self.assertIs(params['allow_promotion_codes'], False)
        self.assertEqual([line['price_data']['unit_amount'] for line in params['line_items']], [1000, 1000])
        self.reset_effects()
        with self.app.app_context():
            self.before = self.snapshot()
        for code, _, _ in COLLABORATORS:
            result = self.checkout([self.item('MIKVE5LP'), self.item('REGULAR')], code)
            self.assertEqual(result.status_code, 200, result.get_json())
            params = self.effects['session'].call_args.kwargs
            self.assertEqual(params['metadata']['coupon_discount_amount'], '2.00')
            self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
            self.assertEqual(params['metadata']['total'], '18.00')
            self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 200)
            self.reset_effects()
            with self.app.app_context():
                self.before = self.snapshot()
        result = self.checkout([self.item('MIKVE5LP'), self.item('VOLUME', quantity=3, price=9)], None)
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
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
            # BF containers are separately barred from subscriptions on this
            # branch. Exercise the approved generic promotion capability here.
            response = self.subscription(self.item('HIDDENPROMO'), f' {code.swapcase()} ',
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

    def test_actual_bf26_containers_never_become_subscriptions_with_capability(self):
        for (code, _, _), sku in itertools.product(COLLABORATORS, BF_PACKS):
            result = self.subscription(self.item(sku), code, valid_in_promotion=True)
            self.assertEqual(result.status_code, 422, result.get_json())
        self.assertEqual(result.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
        self.assert_no_effects()

    def test_selected_nested_ordinary_addons_aliases_no_longer_veto_valid_coupons(self):
        for (code, _, _), sku in itertools.product(COLLABORATORS, PROTECTED):
            references = ({'productId': self.ids[sku]}, {'productSlug': sku.lower()},
                          {'sku': f' {sku.lower()} '},
                          {'id': self.ids['REGULAR'], 'productId': self.ids[sku], 'sku': 'REGULAR'})
            for reference in references:
                for nesting in ({'addons': [reference]}, {'product': {'addons': [{'product': reference}]}}):
                    item = self.item('REGULAR', **nesting)
                    self.assertTrue(self.preview(code, [item]).get_json()['valid'])
                    self.assertEqual(self.use(code, [item]).status_code, 200)
                    result = self.checkout([item], code)
                    self.assertEqual(result.status_code, 200, result.get_json())
                    # Nested references are policy context, not newly charged
                    # cart lines; this branch still charges only the outer line.
                    params = self.effects['session'].call_args.kwargs
                    self.assertEqual(len(params['line_items']), 1)
                    self.assertEqual(params['metadata']['coupon_discount_amount'], '1.00')
                    self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                    self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 100)
                    self.reset_effects()
                    result = self.subscription(item, code)
                    self.assertEqual(result.status_code, 200, result.get_json())
                    self.assertNotIn('discounts', self.effects['session'].call_args.kwargs)
                    self.assertIs(self.effects['session'].call_args.kwargs['allow_promotion_codes'], False)
                    self.effects['coupon'].assert_not_called()
                    self.reset_effects()

    def test_legacy_alias_and_nested_promotion_use_canonical_capability(self):
        from src.services.cart_promotions import cart_contains_in_promotion

        items = [self.item('REGULAR', addons=[{'product': {'productId': self.ids['HIDDENPROMO']}}])]
        with self.app.app_context():
            for code, _, _ in COLLABORATORS:
                self.assertFalse(cart_blocks_coupon(items, discount_code=code))
                self.assertFalse(cart_contains_in_promotion(items, discount_code=code))
            for code in DENIED_CODES:
                self.assertTrue(cart_blocks_coupon(items, discount_code=code))
                self.assertTrue(cart_contains_in_promotion(items, discount_code=code))
        for code, _, _ in COLLABORATORS:
            self.assertTrue(self.preview(code, items).get_json()['valid'])
        for code in DENIED_CODES:
            self.assert_veto(self.preview(code, items), 200)
        self.assert_no_effects()

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

    def test_all_normal_coupon_origins_discount_all_five_ordinary_protected_skus(self):
        valid_codes = [code for code in DENIED_CODES if code not in ('UNKNOWN', 'STRIPE-ONLY')]
        valid_codes += [code for code, _, _ in COLLABORATORS]
        for code, sku in itertools.product(valid_codes, PROTECTED):
            with self.subTest(code=code, sku=sku):
                self.assertTrue(self.preview(code, [self.item(sku)]).get_json()['valid'])
                result = self.checkout([self.item(sku, quantity=3)], code, discount_amount=999999)
                self.assertEqual(result.status_code, 200, result.get_json())
                params = self.effects['session'].call_args.kwargs
                self.assertEqual(params['metadata']['subtotal'], '30.00')
                self.assertEqual(params['metadata']['coupon_discount_amount'], '5.00' if code == 'FIXED5' else '3.00')
                self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 500 if code == 'FIXED5' else 300)
                self.assertEqual(params['line_items'][0]['price_data']['unit_amount'], 1000)

    def test_every_bf_plus_normal_cart_ordinary_code_rejects_whole_coupon_in_all_orders(self):
        for code, sku, normal in itertools.product(DENIED_CODES, BF_PACKS, ('REGULAR', *PROTECTED)):
            for ordered in itertools.permutations([self.item(sku), self.item(normal)]):
                with self.subTest(code=code, bf=sku, normal=normal):
                    self.assert_veto(self.checkout(list(ordered), code))
                    self.assert_veto(self.preview(code, list(ordered)), 200)
                    self.assert_veto(self.use(code, list(ordered)))

    def test_bf_plus_temprano_collaborator_coupon_discounts_both_under_own_mixed_gate(self):
        for (code, _, _), sku in itertools.product(COLLABORATORS, BF_PACKS):
            for ordered in itertools.permutations([self.item(sku), self.item('MIKVET500')]):
                with self.app.app_context():
                    self.assertFalse(cart_blocks_coupon(list(ordered), discount_code=code))
                self.assertTrue(self.preview(code, list(ordered)).get_json()['valid'])
                result = self.checkout(list(ordered), code)
                self.assertEqual(result.status_code, 200, result.get_json())
                params = self.effects['session'].call_args.kwargs
                self.assertEqual(params['metadata']['subtotal'], '20.00')
                self.assertEqual(params['metadata']['coupon_discount_amount'], '2.00')
                self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                self.assertEqual(params['metadata']['total'], '18.00')
                self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 200)
                self.assertEqual([line['price_data']['unit_amount'] for line in params['line_items']], [1000, 1000])

    def test_collaborator_bf_plus_ordinary_component_pack_discounts_both_lines(self):
        for (code, _, _), sku, normal in itertools.product(COLLABORATORS, BF_PACKS, ('REGULAR', *PROTECTED[3:])):
            result = self.checkout([self.item(sku), self.item(normal)], code)
            self.assertEqual(result.status_code, 200, result.get_json())
            params = self.effects['session'].call_args.kwargs
            self.assertEqual(params['metadata']['subtotal'], '20.00')
            self.assertEqual(params['metadata']['coupon_discount_amount'], '2.00')
            self.assertEqual(params['metadata']['total'], '18.00')
            self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 200)

    def test_supported_ordinary_subscription_validates_all_coupon_origins_without_new_discount(self):
        codes = [code for code in DENIED_CODES if code not in ('UNKNOWN', 'STRIPE-ONLY')]
        codes += [code for code, _, _ in COLLABORATORS]
        for code in codes:
            result = self.subscription(self.item('REGULAR'), code, discount_amount=99999,
                                       discounts=[{'coupon': 'forged'}], allow_promotion_codes=True)
            self.assertEqual(result.status_code, 200, result.get_json())
            params = self.effects['session'].call_args.kwargs
            self.assertEqual(params['mode'], 'subscription')
            self.assertIs(params['allow_promotion_codes'], False)
            self.assertNotIn('discounts', params)
            self.assertEqual(self.effects['price'].call_args.kwargs['unit_amount'], 1000)
            self.effects['coupon'].assert_not_called()

    def test_supported_ordinary_subscription_normal_coupon_minimum_and_email_are_unchanged(self):
        for values in ({'min_order_amount': 50}, {'email': 'other@example.invalid'}):
            self.modify_coupon('PUBLIC10', min_order_amount=0, email=None)
            self.modify_coupon('PUBLIC10', **values)
            result = self.subscription(self.item('REGULAR', price=999999), 'PUBLIC10',
                                       subtotal=999999, order_amount=999999, valid_in_promotion=True)
            self.assertEqual(result.status_code, 422)
            self.assertEqual(result.get_json()['error'], 'COUPON_INVALID')
            self.assert_no_effects()

    def test_exact_six_campaign_skus_veto_without_persisted_promotion_or_with_forged_identity(self):
        with self.app.app_context():
            for sku in BF_PACKS:
                WebProduct.query.get(self.ids[sku]).in_promotion = False
            db.session.commit()
            self.before = self.snapshot()
        for sku in BF_PACKS:
            ordinary = self.item('REGULAR')
            attempts = (self.item(sku, in_promotion=False), {'id': self.ids[sku]},
                        {'slug': sku.lower()}, {'sku': f' {sku.lower()} '},
                        self.item(sku, slug='regular', sku='REGULAR'),
                        {**ordinary, 'slug': sku.lower()}, {**ordinary, 'sku': sku},
                        {'id': 999999, 'slug': sku.lower(), 'sku': 'REGULAR'})
            for attempt in attempts:
                self.assert_veto(self.checkout([attempt], 'PUBLIC10'))
                self.assert_veto(self.preview('PUBLIC10', attempt), 200)
                self.assert_veto(self.use('PUBLIC10', attempt))
                self.assert_veto(self.subscription(attempt, 'PUBLIC10'))
        for (code, _, _), sku in itertools.product(COLLABORATORS, BF_PACKS):
            self.assertTrue(self.preview(code, [self.item(sku)]).get_json()['valid'])
            result = self.checkout([self.item(sku)], code)
            self.assertEqual(result.status_code, 200, result.get_json())
            self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], 100)

    def test_unresolved_raw_campaign_sku_still_vetoes_ordinary_coupon_with_forged_id(self):
        with self.app.app_context():
            for sku in BF_PACKS:
                db.session.delete(WebProduct.query.get(self.ids[sku]))
            db.session.commit()
            self.before = self.snapshot()
        for sku in BF_PACKS:
            attempt = {**self.item('REGULAR'), 'sku': f' {sku.lower()} '}
            self.assert_veto(self.checkout([attempt], 'PUBLIC10'))
            self.assert_veto(self.preview('PUBLIC10', attempt), 200)
            self.assert_veto(self.use('PUBLIC10', attempt))
            self.assert_veto(self.subscription(attempt, 'PUBLIC10'))

    def test_nearby_bf_names_do_not_invent_coupon_campaign_exclusions(self):
        for sku in ('MIKVET500BF', 'MIKVET500BF3EXTRA', 'MIKBIO19BF3', 'MIKPACKFR'):
            with self.app.app_context():
                self.assertFalse(cart_blocks_coupon({**self.item('REGULAR'), 'sku': sku}, discount_code='PUBLIC10'))
        self.assert_no_effects()

    def test_ordinary_protected_coupon_invalid_expired_used_exhausted_and_email_checks_all_flows(self):
        states = ({'active': False}, {'used': True},
                  {'expires_at': datetime.utcnow() - timedelta(days=1)},
                  {'max_uses': 2, 'current_uses': 2}, {'email': 'other@example.invalid'})
        for sku, state in itertools.product(PROTECTED, states):
            self.modify_coupon('PUBLIC10', active=True, used=False, expires_at=None,
                               max_uses=None, current_uses=0, email=None)
            self.modify_coupon('PUBLIC10', **state)
            for result in (self.checkout([self.item(sku)], 'PUBLIC10'),
                           self.subscription(self.item(sku), 'PUBLIC10')):
                self.assertEqual(result.status_code, 422)
                self.assertEqual(result.get_json()['error'], 'COUPON_INVALID')
            self.assertFalse(self.preview('PUBLIC10', [self.item(sku)]).get_json()['valid'])
            self.assertEqual(self.use('PUBLIC10', [self.item(sku)]).status_code, 400)
            self.assert_no_effects()

    def test_ordinary_protected_coupon_minimum_uses_server_base_not_browser_amount(self):
        self.modify_coupon('PUBLIC10', min_order_amount=50)
        for sku in PROTECTED:
            result = self.checkout([self.item(sku)], 'PUBLIC10', subtotal=999999, order_amount=999999)
            self.assertEqual(result.status_code, 422)
            self.assertEqual(result.get_json()['error'], 'COUPON_INVALID')
            self.assertIn('50', result.get_json()['message'])
            self.assert_no_effects()
        # The minimum passes on the same ordinary SKU at 5 master-price units.
        result = self.checkout([self.item('MIKVET500', quantity=5)], 'PUBLIC10')
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(self.effects['session'].call_args.kwargs['metadata']['coupon_discount_amount'], '5.00')

    def test_stale_client_valid_preview_does_not_override_new_persisted_promotion(self):
        preview = self.preview('PUBLIC10', [self.item('MIKVET500')])
        self.assertTrue(preview.get_json()['valid'])
        with self.app.app_context():
            WebProduct.query.get(self.ids['MIKVET500']).in_promotion = True
            db.session.commit()
            self.before = self.snapshot()
        flags = {'coupon': preview.get_json()['coupon'], 'valid_in_promotion': True}
        self.assert_veto(self.checkout([self.item('MIKVET500', in_promotion=False)], 'PUBLIC10', **flags))
        self.assert_veto(self.preview('PUBLIC10', [self.item('MIKVET500')], **flags), 200)
        self.assert_veto(self.use('PUBLIC10', [self.item('MIKVET500')], **flags))

    def test_23_synthetic_historical_flags_remain_false_never_grant_bf_exception(self):
        # Synthetic identities exercise all 23 flags without reading/mutating live data.
        codes = [f'HISTORICAL-FIXTURE-{index:02d}' for index in range(23)]
        with self.app.app_context():
            db.session.add_all(Coupon(code=code, description='Historical collaborator', discount_value=10)
                               for code in codes)
            db.session.commit()
            self.before = self.snapshot()
        for code in codes:
            self.assertTrue(self.preview(code, [self.item('MIKVET500')]).get_json()['valid'])
            self.assert_veto(self.checkout([self.item(BF_PACKS[0])], code))
            self.assert_veto(self.preview(code, [self.item(BF_PACKS[0])]), 200)
        with self.app.app_context():
            for code in (*codes, 'MIKELSFRIENDS'):
                coupon = Coupon.query.filter_by(code=code).one()
                self.assertFalse(coupon.valid_in_promotion)
                self.assertFalse(coupon_allows_promotion(coupon))

    def test_reserved_legacy_campaign_aliases_do_not_become_ordinary_coupon_lines(self):
        aliases = ('MIKVET500BF3', 'MIKVET500BF6', 'MIKBIO19BF6',
                   'MIKPARA450BF3', 'MIKPARA450BF6', 'MIKVE5LPBF3')
        for sku in aliases:
            attempt = {**self.item('REGULAR'), 'sku': sku}
            self.assert_veto(self.preview('MIKELSFRIENDS', attempt), 200)
            self.assert_veto(self.checkout([attempt], 'PUBLIC10'))
            with self.app.app_context():
                self.assertFalse(cart_blocks_coupon(attempt, discount_code='IRVIANCESTRAL'))

    def test_nested_canonical_and_reserved_aliases_never_hide_campaign_coupon_veto(self):
        aliases = ('MIKVET500BF3', 'MIKVET500BF6', 'MIKBIO19BF6',
                   'MIKPARA450BF3', 'MIKPARA450BF6', 'MIKVE5LPBF3')
        with self.app.app_context():
            for sku in BF_PACKS:
                WebProduct.query.get(self.ids[sku]).in_promotion = False
            db.session.commit()
            self.before = self.snapshot()
        references = [{'sku': f' {sku.lower()} '} for sku in (*BF_PACKS, *aliases)]
        for sku in BF_PACKS:
            references.extend(({'productId': self.ids[sku]}, {'productSlug': sku.lower()},
                               {'id': self.ids['REGULAR'], 'sku': 'REGULAR', 'productId': self.ids[sku]},
                               {'id': self.ids['REGULAR'], 'slug': 'regular', 'productSlug': sku.lower()}))
        for reference in references:
            for nesting in ({'addons': [{'product': reference}]}, {'product': {'addons': [reference]}}):
                item = self.item('REGULAR', **nesting)
                for code in ('PUBLIC10', 'MIKELSFRIENDS'):
                    self.assert_veto(self.checkout([item], code))
                    self.assert_veto(self.preview(code, [item]), 200)
                    self.assert_veto(self.use(code, [item]))
                    self.assert_veto(self.subscription(item, code))
                for code, _, _ in COLLABORATORS:
                    self.assertTrue(self.preview(code, [item]).get_json()['valid'])
        self.assert_no_effects()

    def test_six_actual_bf_master_totals_plus_current_temprano_discount_all_lines(self):
        from src.services.campaign_packs import CAMPAIGN_PACKS
        from src.services.money import eur_to_cents

        self.assertEqual(tuple(pack.sku for pack in CAMPAIGN_PACKS), BF_PACKS)
        with self.app.app_context():
            for pack in CAMPAIGN_PACKS:
                WebProduct.query.get(self.ids[pack.sku]).price = float(pack.price)
            WebProduct.query.get(self.ids['MIKVET500']).price = 19.90
            db.session.commit()
        for (code, _, _), pack in itertools.product(COLLABORATORS, CAMPAIGN_PACKS):
            lines = [self.item(pack.sku, price=float(pack.price)), self.item('MIKVET500', price=19.90)]
            for ordered in itertools.permutations(lines):
                result = self.checkout(list(ordered), code, discount_amount=999999)
                self.assertEqual(result.status_code, 200, result.get_json())
                subtotal = pack.price + Decimal('19.90')
                discount = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                params = self.effects['session'].call_args.kwargs
                self.assertEqual(params['metadata']['subtotal'], format(subtotal, '.2f'))
                self.assertEqual(params['metadata']['coupon_discount_amount'], format(discount, '.2f'))
                self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                self.assertEqual(params['metadata']['total'], format(subtotal - discount, '.2f'))
                self.assertEqual(self.effects['coupon'].call_args.kwargs['amount_off'], eur_to_cents(discount))
                self.assertEqual({line['price_data']['product_data']['metadata']['sku']: line['price_data']['unit_amount']
                                  for line in params['line_items']},
                                 {pack.sku: eur_to_cents(pack.price), 'MIKVET500': 1990})
                self.assertEqual([line['quantity'] for line in params['line_items']], [1, 1])

    def test_normal_protected_coupon_preflight_omits_amount_then_validates_exact_decimal(self):
        self.modify_coupon('PUBLIC10', min_order_amount=49)
        amounts = []
        original = Coupon.is_valid

        def checked(coupon, order_amount=None, customer_email=None):
            amounts.append(order_amount)
            return original(coupon, order_amount=order_amount, customer_email=customer_email)

        with patch.object(Coupon, 'is_valid', checked):
            result = self.checkout([self.item('MIKVET500', quantity=5)], 'PUBLIC10', subtotal=999999)
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(amounts, [None, Decimal('50.00')])
        self.assertIsInstance(amounts[1], Decimal)
        self.assertEqual(self.effects['session'].call_args.kwargs['metadata']['coupon_discount_amount'], '5.00')


if __name__ == '__main__':
    unittest.main()
