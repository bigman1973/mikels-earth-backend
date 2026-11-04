"""No production env, real database or external provider is used by these tests."""
import copy
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask

from src.models.user import db
from src.models.coupon import Coupon
from src.models.newsletter_subscriber import NewsletterSubscriber  # noqa: F401
from src.models.order import Order  # noqa: F401
from src.models.web_product import WebProduct
from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot  # noqa: F401
from src.models.stock import StockReservation, StockMovement  # noqa: F401
from src.models.abandoned_cart import AbandonedCart  # noqa: F401
from src.routes.coupon_routes import coupon_bp
from src.routes.stripe_routes import stripe_bp
from src.services.welcome_coupon_minimum import (
    WELCOME_DESCRIPTION, audit_welcome_minimum, classify_welcome_minimum,
)


class WelcomeCouponMinimumTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(coupon_bp, url_prefix='/api/coupon')
        self.app.register_blueprint(stripe_bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.coupon = Coupon.create_welcome_coupon('owner@example.com')
        self.product = WebProduct(name='Boundary fixture', slug='local-boundary',
                                  sku='LOCALBOUNDARY1', price=49, stock=1000,
                                  category='Prueba', active=True, visible_in_store=True)
        db.session.add(self.product)
        db.session.commit()
        self.client = self.app.test_client()
        self.customer = {'email': 'owner@example.com', 'name': 'Local fixture',
                         'phone': '+34600000000', 'address': 'Local fixture 1',
                         'city': 'Lleida', 'postal_code': '25001', 'country': 'España'}
        self.mock('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden'))
        self.mock('src.services.holded_service.holded_get_products',
                  return_value=[{'sku': 'LOCALBOUNDARY1'}])
        self.mock('src.services.order_tax_snapshot.build_tax_rule_snapshot',
                  return_value={'LOCALBOUNDARY1': {'kind': 'single', 'rate': '0.04'}})
        self.session = self.mock('src.routes.stripe_routes.stripe.checkout.Session.create',
                                 side_effect=lambda **kw: SimpleNamespace(
                                     id=f'cs_local_min_{self.session.call_count}',
                                     url='https://checkout.stripe.test/local-minimum'))
        self.stripe_coupon = self.mock('src.routes.stripe_routes.stripe.Coupon.create',
                                      return_value=SimpleNamespace(id='local_minimum'))

    def mock(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def preview(self, **extra):
        return self.client.post('/api/coupon/validate', json={'code': self.coupon.code, **extra})

    def checkout(self, price='49.00', **extra):
        # Only the isolated SQLite fixture price is changed, never catalogue data.
        self.product.price = float(price)
        self.product.volume_discount = None
        db.session.commit()
        return self.client.post('/api/stripe/create-checkout-session', json={
            'items': [{'id': self.product.id, 'price': price, 'quantity': 1}],
            'customer_info': self.customer, 'discount_code': self.coupon.code, **extra,
        })

    def test_future_welcome_minimum_preserves_owner_unique_code_discount_expiry_and_single_use(self):
        self.assertEqual(self.coupon.min_order_amount, 49)
        self.assertEqual(self.coupon.description, WELCOME_DESCRIPTION)
        self.assertEqual(self.coupon.discount_value, 10)
        self.assertEqual(self.coupon.discount_type, 'percentage')
        self.assertEqual(self.coupon.email, 'owner@example.com')
        self.assertEqual(self.coupon.max_uses, 1)
        self.assertEqual(self.coupon.max_uses_per_customer, 1)
        self.assertTrue(self.coupon.active)
        self.assertFalse(self.coupon.used)
        self.assertEqual(self.coupon.current_uses, 0)
        self.assertGreater(self.coupon.expires_at, datetime.utcnow() + timedelta(days=29))
        self.assertLess(self.coupon.expires_at, datetime.utcnow() + timedelta(days=31))
        other = Coupon.create_welcome_coupon('other@example.com')
        db.session.flush()
        self.assertNotEqual(self.coupon.code, other.code)

    def test_creator_leaves_transaction_commit_with_caller(self):
        with patch.object(db.session, 'commit') as commit:
            coupon = Coupon.create_welcome_coupon('transaction@example.com')
            self.assertEqual(coupon.min_order_amount, 49)
            commit.assert_not_called()

    def test_generic_legacy_creator_keeps_custom_discount_and_does_not_gain_welcome_minimum(self):
        generic = Coupon.create_coupon('generic@example.com', discount_percent=17)
        self.assertEqual(generic.discount_value, 17)
        self.assertEqual(generic.min_order_amount, 0)
        self.assertIsNone(generic.description)

    def test_public_collaborator_and_fixed_coupons_do_not_inherit_welcome_minimum(self):
        for code, description, discount_type in [
            ('PUBLIC-LOCAL', 'Cupón público', 'percentage'),
            ('COLLAB-LOCAL', 'Cupón colaborador', 'percentage'),
            ('FIXED-LOCAL', 'Cupón manual fijo', 'fixed'),
        ]:
            with self.subTest(discount_type=discount_type, description=description):
                coupon = Coupon(code=code, description=description,
                                discount_type=discount_type, discount_value=10, active=True)
                db.session.add(coupon)
                db.session.flush()
                self.assertEqual(coupon.min_order_amount, 0)
                self.assertTrue(coupon.is_valid(Decimal('10.00'))[0])

    def test_model_boundaries_48_99_false_49_true_49_01_true(self):
        for value, expected in [('48.99', False), ('49.00', True), ('49.01', True)]:
            for amount in (value, float(value), Decimal(value)):
                with self.subTest(amount=amount):
                    self.assertEqual(self.coupon.is_valid(amount, 'owner@example.com')[0], expected)

    def test_model_distinguishes_omitted_subtotal_from_explicit_zero(self):
        self.assertTrue(self.coupon.is_valid(customer_email='owner@example.com')[0])
        for amount in (0, '0.00', Decimal('0')):
            self.assertFalse(self.coupon.is_valid(amount, 'owner@example.com')[0])

    def test_null_minimum_is_zero_and_invalid_nonfinite_negative_amounts_fail_closed(self):
        self.coupon.min_order_amount = None
        self.assertTrue(self.coupon.is_valid(0)[0])
        for amount in ('NaN', 'Infinity', 'garbage', -1, True):
            with self.subTest(amount=amount):
                self.assertFalse(self.coupon.is_valid(amount)[0])

    def test_owner_used_expired_inactive_and_exhausted_rules_unchanged(self):
        self.assertFalse(self.coupon.is_valid(49, 'other@example.com')[0])
        for field, value, message in [
            ('used', True, 'utilizado'),
            ('expires_at', datetime.utcnow() - timedelta(seconds=1), 'caducado'),
            ('active', False, 'desactivado'),
            ('current_uses', 1, 'agotado'),
        ]:
            old = getattr(self.coupon, field)
            setattr(self.coupon, field, value)
            with self.subTest(field=field):
                valid, result = self.coupon.is_valid(49, 'owner@example.com')
                self.assertFalse(valid)
                self.assertIn(message, result)
            setattr(self.coupon, field, old)

    def test_validating_minimum_never_mutates_coupon_usage_or_state(self):
        before = self.coupon.to_dict()
        for amount in (0, '48.99', '49.00', '49.01'):
            self.coupon.is_valid(amount, 'owner@example.com')
        self.assertEqual(self.coupon.to_dict(), before)

    def test_route_allows_code_only_preview_and_exposes_minimum_not_owner(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result['valid'])
        self.assertEqual(result['coupon']['min_order_amount'], 49)
        self.assertNotIn('email', result['coupon'])

    def test_route_explicit_subtotal_boundaries_and_zero(self):
        for amount, expected in [(0, False), ('48.99', False), ('49.00', True), ('49.01', True)]:
            with self.subTest(amount=amount):
                response = self.preview(order_amount=amount, email='owner@example.com')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()['valid'], expected)

    def test_route_explicit_null_invalid_or_negative_is_not_omitted(self):
        for amount in (None, 'bad', 'NaN', 'Infinity', -1, True, {}, []):
            with self.subTest(amount=amount):
                response = self.preview(order_amount=amount)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['error'], 'INVALID_ORDER_AMOUNT')

    def test_checkout_canonical_boundaries_even_when_browser_forges_subtotal(self):
        for amount, status in [('48.99', 422), ('49.00', 200), ('49.01', 200)]:
            self.session.reset_mock()
            self.stripe_coupon.reset_mock()
            with self.subTest(amount=amount):
                response = self.checkout(amount, order_amount=9999, subtotal=9999, discount_amount=0)
                self.assertEqual(response.status_code, status, response.get_json())
                if status == 422:
                    self.assertEqual(response.get_json()['error'], 'COUPON_INVALID')
                    self.assertIn('49.00', response.get_json()['message'])
                    self.session.assert_not_called()
                    self.stripe_coupon.assert_not_called()
                else:
                    params = self.session.call_args.kwargs
                    self.assertEqual(params['metadata']['subtotal'], amount)
                    self.assertEqual(params['metadata']['coupon_discount_amount'], '4.90')
                    self.assertNotIn('allow_promotion_codes', params)
                    self.assertEqual(self.stripe_coupon.call_args.kwargs['amount_off'], 490)

    def test_checkout_passes_decimal_canonical_subtotal_to_validation(self):
        original = Coupon.is_valid
        amounts = []

        def recording(instance, *args, **kwargs):
            amounts.append(kwargs['order_amount'])
            return original(instance, *args, **kwargs)

        with patch.object(Coupon, 'is_valid', recording):
            response = self.checkout('49.00')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(amounts, [Decimal('49.00')])
        self.assertIsInstance(amounts[0], Decimal)

    def test_checkout_minimum_uses_subtotal_after_server_quantity_discount(self):
        self.product.price = 50
        self.product.volume_discount = {'minQuantity': 1, 'discount': 10}
        db.session.commit()
        response = self.client.post('/api/stripe/create-checkout-session', json={
            'items': [{'id': self.product.id, 'price': '45.00', 'quantity': 1}],
            'customer_info': self.customer, 'discount_code': self.coupon.code,
            'order_amount': 50,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()['error'], 'COUPON_INVALID')
        self.session.assert_not_called()

    def test_checkout_without_coupon_is_not_subject_to_welcome_minimum(self):
        response = self.checkout('48.99', discount_code='')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn('allow_promotion_codes', self.session.call_args.kwargs)

    def test_checkout_wrong_owner_used_and_expired_are_rejected_above_minimum(self):
        for field, value in [('email', 'other@example.com'), ('used', True),
                             ('expires_at', datetime.utcnow() - timedelta(seconds=1))]:
            old = getattr(self.coupon, field)
            setattr(self.coupon, field, value)
            db.session.commit()
            with self.subTest(field=field):
                response = self.checkout('49.01')
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.get_json()['error'], 'COUPON_INVALID')
                self.session.assert_not_called()
            setattr(self.coupon, field, old)
            db.session.commit()

    def test_admin_preview_honours_minimum_without_relaxing_admin_auth(self):
        from src.routes import admin_panel_routes
        for amount, expected in [('48.99', False), ('49.00', True), ('49.01', True)]:
            with self.subTest(amount=amount), self.app.test_request_context('/fixture', method='POST', json={
                'code': self.coupon.code, 'email': 'owner@example.com', 'order_amount': amount,
            }):
                # Invoke only the handler behind the auth decorator in a local
                # fixture, without changing the production auth implementation.
                response = admin_panel_routes.admin_validate_coupon.__wrapped__()
                self.assertEqual(response.get_json()['valid'], expected)


class WelcomeMinimumSelectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 21, 0, 0)
        self.row = {
            'id': 1, 'code': 'MIKELS-FIXTURE1', 'email': 'ow.ner+welcome@gmail.com',
            'description': WELCOME_DESCRIPTION, 'discount_type': 'percentage',
            'discount_value': 10, 'min_order_amount': None, 'max_uses': 1,
            'max_uses_per_customer': 1, 'current_uses': 0, 'used': False,
            'used_at': None, 'active': True, 'expires_at': self.now + timedelta(days=20),
            'link_count': 1, 'subscriber_email_key': 'owner@gmail.com',
            'subscriber_email': 'owner@gmail.com',
        }

    def classify(self, **overrides):
        return classify_welcome_minimum({**self.row, **overrides}, now=self.now)

    def test_exact_source_fk_and_canonical_identity_are_required(self):
        self.assertEqual(self.classify(), 'eligible_raise_minimum')
        for overrides in [
            {'description': None}, {'description': 'Cupón antiguo bienvenida'},
            {'description': 'Cupón colaborador'}, {'link_count': 0}, {'link_count': 2},
            {'subscriber_email_key': 'different@gmail.com'},
            {'subscriber_email': 'different@gmail.com'}, {'email': None},
            {'email': ''}, {'code': 'PUBLIC10'}, {'code': 'VUELVE10-FIXTURE'},
            {'discount_value': 20}, {'discount_type': 'fixed'},
        ]:
            with self.subTest(overrides=overrides):
                self.assertTrue(self.classify(**overrides).startswith('skip_'))

    def test_used_expired_inactive_unknown_and_non_single_use_are_skipped(self):
        for overrides in [
            {'active': False}, {'active': None}, {'used': True}, {'used': None},
            {'used_at': self.now}, {'current_uses': 1}, {'current_uses': None},
            {'expires_at': self.now}, {'expires_at': self.now - timedelta(seconds=1)},
            {'expires_at': None}, {'max_uses': None}, {'max_uses': 2},
            {'max_uses_per_customer': 2},
        ]:
            with self.subTest(overrides=overrides):
                self.assertTrue(self.classify(**overrides).startswith('skip_'))

    def test_null_lower_equal_and_higher_minima_idempotent_no_lowering(self):
        for amount in (None, 0, 10, 48.99, Decimal('48.999')):
            with self.subTest(amount=amount):
                self.assertEqual(self.classify(min_order_amount=amount), 'eligible_raise_minimum')
        for amount in (49, 49.01, 59, Decimal('49.0001')):
            with self.subTest(amount=amount):
                self.assertEqual(self.classify(min_order_amount=amount), 'keep_minimum_at_or_above_49')
        for amount in ('NaN', 'Infinity', 'broken'):
            self.assertEqual(self.classify(min_order_amount=amount), 'skip_invalid_minimum')

    def test_audit_reports_only_counts_opaque_hash_minimum_without_mutation(self):
        rows = [self.row, {**self.row, 'id': 2, 'min_order_amount': 59},
                {**self.row, 'id': 3, 'description': None}]
        original = copy.deepcopy(rows)
        result, ids = audit_welcome_minimum(rows, now=self.now)
        self.assertEqual(rows, original)
        self.assertEqual(ids, [1])
        self.assertEqual(result['counts']['audited'], 3)
        self.assertEqual(result['counts']['eligible_raise_minimum'], 1)
        self.assertEqual(result['counts']['keep_minimum_at_or_above_49'], 1)
        self.assertEqual(result['counts']['skip_unproven_metadata'], 1)
        self.assertEqual(len(result['fingerprint']), 64)
        self.assertEqual(set(result), {'counts', 'fingerprint', 'minimum'})
        self.assertNotIn(self.row['email'], str(result))
        self.assertNotIn(self.row['code'], str(result))
        self.assertEqual(audit_welcome_minimum(list(reversed(rows)), now=self.now)[0], result)

    def test_fingerprint_changes_on_audited_minimum_or_eligibility_not_personal_values(self):
        initial, _ = audit_welcome_minimum([self.row], now=self.now)
        changed_minimum, _ = audit_welcome_minimum([{**self.row, 'min_order_amount': 10}], now=self.now)
        changed_state, _ = audit_welcome_minimum([{**self.row, 'used': True}], now=self.now)
        same_evidence, _ = audit_welcome_minimum([{**self.row, 'code': 'MIKELS-DIFFERENT'}], now=self.now)
        self.assertNotEqual(initial['fingerprint'], changed_minimum['fingerprint'])
        self.assertNotEqual(initial['fingerprint'], changed_state['fingerprint'])
        self.assertEqual(initial['fingerprint'], same_evidence['fingerprint'])


if __name__ == '__main__':
    unittest.main()
