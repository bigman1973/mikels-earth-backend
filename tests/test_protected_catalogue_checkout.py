import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.models.coupon import Coupon
from src.models.order import Order  # noqa: F401
from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot  # noqa: F401
from src.models.stock import StockReservation, StockMovement  # noqa: F401
from src.routes.coupon_routes import coupon_bp
from src.routes.product_routes import product_bp
from src.routes.stripe_routes import stripe_bp
from src.services.checkout_pricing import calculate_checkout_line_price, volume_discount_percent
from src.services.money import eur_to_cents
from src.services.protected_catalogue import (
    FULL_PRICE_ONLY_SKUS,
    PROTECTED_COMPONENT_PACK_SKUS,
    is_full_price_only,
    is_mixed_with_full_price_only_product,
    is_promotion_protected,
)


UNIT_CASES = (
    ('MIKVET500', 'aceite-temprano-sin-filtrar', Decimal('19.90')),
    ('MIKVE5LP', 'aceite-5l-caja-3', Decimal('43.00')),
    ('MIKPARA450', 'paraguayo-almibar', Decimal('17.15')),
)
PACK_CASES = (
    ('MIKPACKF', 'pack-recipe-holded', Decimal('50.00')),
    ('MIKPACKTP', 'pack-temprano-premium', Decimal('19.00')),
)
PROTECTED_CASES = UNIT_CASES + PACK_CASES
# Each real draft stores the price of one container. Its internal units are
# descriptive includes, not mandatory cart quantities or promotional tiers.
# Isolated active fixtures model future saleability, not real publication.
NEW_PACK_CASES = (
    ('MIKBIO19BF6', 'bio-bf-6', 6, Decimal('71.40'), 'MIKBIO19'),
    ('MIKPARA450BF3', 'para-bf-3', 3, Decimal('30.90'), 'MIKPARA450'),
    ('MIKPARA450BF6', 'para-bf-6', 6, Decimal('61.70'), 'MIKPARA450'),
    ('MIKVE5LPBF3', '5l-bf-3', 3, Decimal('96.75'), 'MIKVE5LP'),
)
PROMOTION_FIELDS = (
    'tieredDiscount', 'volumeDiscount', 'subscriptionDiscount',
    'subscriptionFrequencies', 'subscriptionTerms', 'addons', 'originalPrice',
)


class ProtectedCatalogueCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(product_bp, url_prefix='/api')
        self.app.register_blueprint(stripe_bp)
        self.app.register_blueprint(coupon_bp, url_prefix='/api/coupon')
        with self.app.app_context():
            db.create_all()
            products = [
                WebProduct(
                    name=sku,
                    slug=slug,
                    sku=sku,
                    price=float(price),
                    original_price=float(price + Decimal('10.00')),
                    category='Packs' if sku in PROTECTED_COMPONENT_PACK_SKUS else 'Unitarios',
                    stock=1000,
                    active=True,
                    visible_in_store=True,
                    subscription_available=True,
                    subscription_discount=10,
                    subscription_frequencies=[{'value': 'monthly', 'discount': 10}],
                    subscription_terms={'duration': '12 meses'},
                    volume_discount={'minQuantity': 1, 'discount': 25},
                    # Even a stale one-unit fixed tier must not change the price.
                    tiered_discount=[
                        {'actualQuantity': 1, 'label': '1 unidad', 'price': 1.00},
                        {'actualQuantity': 3, 'label': 'Pack de 3', 'price': 2.00},
                    ],
                    addons=[{'productSlug': 'estuche-regalo-temprano'}],
                )
                for sku, slug, price in PROTECTED_CASES
            ]
            products.extend([
                WebProduct(
                    name='Producto compatible', slug='producto-compatible',
                    sku='MIKOTHER1', price=10.00, category='Packs', stock=1000,
                    active=True, visible_in_store=True,
                    addons=[{'productSlug': slug} for _, slug, _ in PROTECTED_CASES]
                    + [{'productSlug': 'estuche-regalo-temprano'}],
                ),
                WebProduct(
                    name='Estuche', slug='estuche-regalo-temprano',
                    sku='MIKESTTEM', price=5.00, category='Packs', stock=1000,
                    active=True, visible_in_store=False,
                ),
                WebProduct(
                    name='Producto con tier', slug='producto-con-tier',
                    sku='MIKOTHER2', price=10.00, category='Packs', stock=1000,
                    active=True, visible_in_store=True,
                    tiered_discount=[{'minQuantity': 3, 'discount': 10}],
                ),
                WebProduct(
                    name='Temprano anterior', slug='temprano-2025-26',
                    sku='MIKVET5002526', price=17.15, category='Aceites', stock=1000,
                    active=True, visible_in_store=True,
                    tiered_discount=[{'minQuantity': 3, 'discount': 10}],
                ),
                WebProduct(
                    name='Borrador oculto', slug='borrador-privado',
                    sku='MIKDRAFT1', price=119.40, category='Aceites', stock=1200,
                    active=False, visible_in_store=False,
                ),
                WebProduct(
                    name='Agotado', slug='agotado-privado', sku='MIKOUT1',
                    price=12.00, category='Packs', stock=0,
                    active=True, visible_in_store=True,
                ),
            ])
            products.extend(
                WebProduct(
                    name=sku, slug=slug, sku=sku, price=float(total),
                    category='Packs', stock=1000, active=True, visible_in_store=True,
                    includes=[f'{quantity} unidades de {component}'],
                    tiered_discount=None,
                )
                for sku, slug, quantity, total, component in NEW_PACK_CASES
            )
            db.session.add_all(products)
            db.session.add_all([
                Coupon(code='BLACKFRIDAY10', discount_type='percentage', discount_value=10, active=True),
                Coupon(code='BLACKFRIDAY5', discount_type='fixed', discount_value=5, active=True),
            ])
            db.session.commit()
        self.client = self.app.test_client()
        self.customer = {
            'email': 'cliente@example.com', 'name': 'Cliente de prueba',
            'phone': '+34600000000', 'address': 'Calle de prueba 1',
            'city': 'Lleida', 'postal_code': '25001', 'country': 'España',
        }
        self.holded_products = self._patch(
            'src.services.holded_service.holded_get_products',
            return_value=[{'sku': 'MIKVET500'}],
        )
        self.tax_rules = self._patch(
            'src.services.order_tax_snapshot.build_tax_rule_snapshot',
            return_value={'MIKVET500': {'kind': 'single', 'rate': '0.04'}},
        )
        self.create_session = self._patch(
            'src.routes.stripe_routes.stripe.checkout.Session.create',
            side_effect=lambda **kwargs: SimpleNamespace(
                id=f'cs_local_{self.create_session.call_count}', url='https://checkout.stripe.test/local',
            ),
        )
        self.create_coupon = self._patch('src.routes.stripe_routes.stripe.Coupon.create',
                                         return_value=SimpleNamespace(id='coupon_local'))
        self.create_price = self._patch('src.routes.stripe_routes.stripe.Price.create')
        # Any unexpected requests-based external call fails the test instead of
        # accessing Stripe/Holded or any other external service.
        self._patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden'))

    def _patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _item(self, slug, price, quantity=1):
        with self.app.app_context():
            product = WebProduct.query.filter_by(slug=slug).one()
            return {
                'id': product.id, 'slug': slug, 'name': product.name,
                'price': float(price), 'quantity': quantity,
            }

    def _checkout(self, items, **extra):
        return self.client.post('/api/stripe/create-checkout-session', json={
            'items': items, 'customer_info': self.customer, **extra,
        })

    def test_exact_sku_policy_and_allowed_containers(self):
        self.assertEqual(FULL_PRICE_ONLY_SKUS, {'MIKVET500', 'MIKVE5LP', 'MIKPARA450'})
        self.assertEqual(PROTECTED_COMPONENT_PACK_SKUS, {'MIKPACKF', 'MIKPACKTP'})
        for sku, _, _ in PROTECTED_CASES:
            for identifier in (sku, f' {sku.lower()} ', {'sku': sku}, SimpleNamespace(sku=sku)):
                with self.subTest(sku=sku, identifier=identifier):
                    self.assertTrue(is_promotion_protected(identifier))
                    self.assertEqual(is_full_price_only(identifier), sku in FULL_PRICE_ONLY_SKUS)
        for sku in ['MIKVET5002526', 'MIKVET500R'] + [case[0] for case in NEW_PACK_CASES]:
            with self.subTest(sku=sku):
                self.assertFalse(is_promotion_protected(sku))

    def test_public_detail_list_and_english_omit_protected_promotions_and_use_master_price(self):
        for lang in ('es', 'en'):
            catalogue = {
                item['sku']: item
                for item in self.client.get(f'/api/products?lang={lang}').get_json()['products']
            }
            for sku, slug, price in PROTECTED_CASES:
                response = self.client.get(f'/api/products/{slug}?lang={lang}')
                self.assertEqual(response.status_code, 200)
                for payload in (response.get_json(), catalogue[sku]):
                    with self.subTest(sku=sku, lang=lang):
                        self.assertEqual(payload['price'], float(price))
                        self.assertTrue(payload['available'])
                        self.assertFalse(payload['subscriptionAvailable'])
                        for key in PROMOTION_FIELDS + ('stock', 'reservationStockTotal'):
                            self.assertNotIn(key, payload)

    def test_model_serializer_preserves_master_price_and_omits_all_promotions(self):
        with self.app.app_context():
            for sku, slug, price in PROTECTED_CASES:
                with self.subTest(sku=sku):
                    product = WebProduct.query.filter_by(slug=slug).one()
                    payload = product.to_frontend_dict()
                    self.assertEqual(payload['price'], float(price))
                    self.assertFalse(payload['subscriptionAvailable'])
                    for key in PROMOTION_FIELDS + ('stock', 'reservationStockTotal'):
                        self.assertNotIn(key, payload)

    def test_addons_cannot_introduce_protected_units_or_component_packs(self):
        response = self.client.get('/api/products/producto-compatible')
        self.assertEqual(response.status_code, 200)
        addons = response.get_json()['addons']
        self.assertEqual([addon['product']['sku'] for addon in addons], ['MIKESTTEM'])
        self.assertEqual(addons[0]['product']['price'], 5.00)
        self.assertNotIn('addons', addons[0]['product'])

    def test_all_protected_items_ignore_percentage_legacy_bundle_and_fixed_tiers(self):
        tier_shapes = (
            ([{'minQuantity': 1, 'discount': 25}], None),
            (None, {'minQuantity': 1, 'discount': 25}),
            ([{'minQuantity': 3, 'bundleQuantity': 3, 'paidQuantity': 2, 'discount': 33}], None),
            ([{'actualQuantity': 3, 'price': 2.00}], None),
        )
        for sku, _, price in PROTECTED_CASES:
            for tiers, volume in tier_shapes:
                with self.subTest(sku=sku, tiers=tiers, volume=volume):
                    product = SimpleNamespace(sku=sku, price=price, tiered_discount=tiers, volume_discount=volume)
                    calculated = calculate_checkout_line_price(product, 3)
                    self.assertEqual(calculated.expected_line_total, price * Decimal('3'))
                    self.assertEqual(calculated.volume_discount_amount, Decimal('0.00'))
                    self.assertEqual(calculated.tier_discount_percent, Decimal('0'))
                    self.assertFalse(calculated.fixed_pack)
                    self.assertIsNone(calculated.bundle_quantity)
                    self.assertEqual(volume_discount_percent(product, 3), Decimal('0'))

    def test_all_protected_items_sell_normally_at_master_price_for_all_quantities(self):
        for sku, slug, price in PROTECTED_CASES:
            for quantity in (1, 3, 6, 12):
                with self.subTest(sku=sku, quantity=quantity):
                    response = self._checkout([self._item(slug, price, quantity)])
                    self.assertEqual(response.status_code, 200, response.get_json())
                    params = self.create_session.call_args.kwargs
                    line = params['line_items'][0]
                    self.assertEqual(line['price_data']['unit_amount'], eur_to_cents(price))
                    self.assertEqual(line['quantity'], quantity)
                    total = format(price * Decimal(quantity), '.2f')
                    self.assertEqual(params['metadata']['subtotal'], total)
                    self.assertEqual(params['metadata']['total'], total)
                    self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                    self.assertEqual(params['metadata']['discount_amount'], '0.00')
                    self.assertNotIn('discounts', params)
        self.create_coupon.assert_not_called()

    def test_protected_items_reject_stale_promotional_prices_without_leaking_tiers(self):
        for sku, slug, _ in PROTECTED_CASES:
            with self.subTest(sku=sku):
                response = self._checkout([self._item(slug, 1.00, quantity=3)])
                self.assertEqual(response.status_code, 409)
                payload = response.get_json()
                self.assertEqual(payload['error'], 'PRICE_MISMATCH')
                self.assertIsNone(payload['price_updates'][0]['tiered_discount'])
                self.assertIsNone(payload['price_updates'][0]['volume_discount'])
        self.create_session.assert_not_called()
        self.holded_products.assert_not_called()

    def test_all_protected_items_accept_valid_codes_at_master_price_and_invalid_codes_fail_early(self):
        for sku, slug, price in PROTECTED_CASES:
            for code in ('BLACKFRIDAY10', 'BLACKFRIDAY5', 'UNKNOWN', ' blackfriday10 '):
                with self.subTest(sku=sku, code=code):
                    self.create_session.reset_mock()
                    self.create_coupon.reset_mock()
                    self.holded_products.reset_mock()
                    response = self._checkout([self._item(slug, price)], discount_code=code)
                    if code == 'UNKNOWN':
                        self.assertEqual(response.status_code, 422)
                        self.assertEqual(response.get_json()['error'], 'COUPON_INVALID')
                        self.create_session.assert_not_called()
                        self.create_coupon.assert_not_called()
                        self.holded_products.assert_not_called()
                        continue
                    self.assertEqual(response.status_code, 200, response.get_json())
                    discount = Decimal('5.00') if code == 'BLACKFRIDAY5' else (price * Decimal('0.10')).quantize(Decimal('0.01'))
                    params = self.create_session.call_args.kwargs
                    self.assertEqual(params['line_items'][0]['price_data']['unit_amount'], eur_to_cents(price))
                    self.assertEqual(params['metadata']['subtotal'], format(price, '.2f'))
                    self.assertEqual(params['metadata']['coupon_discount_amount'], format(discount, '.2f'))
                    self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                    self.assertEqual(params['metadata']['total'], format(price - discount, '.2f'))
                    self.assertEqual(self.create_coupon.call_args.kwargs['amount_off'], eur_to_cents(discount))

    def test_forged_discount_amount_without_code_is_ignored_not_applied(self):
        for sku, slug, price in PROTECTED_CASES:
            with self.subTest(sku=sku):
                response = self._checkout([self._item(slug, price)], discount_amount=999)
                self.assertEqual(response.status_code, 200)
                params = self.create_session.call_args.kwargs
                self.assertEqual(params['metadata']['discount_amount'], '0.00')
                self.assertEqual(params['metadata']['total'], format(price, '.2f'))
                self.assertNotIn('discounts', params)
        self.create_coupon.assert_not_called()

    def test_existing_gate_rejects_protected_unit_with_nonprotected_cart_item(self):
        partners = [('producto-compatible', Decimal('10.00'))]
        partners += [(case[1], case[2]) for case in PACK_CASES]
        partners += [(case[1], case[3]) for case in NEW_PACK_CASES]
        for sku, slug, price in UNIT_CASES:
            for partner_slug, partner_price in partners:
                for reverse in (False, True):
                    with self.subTest(sku=sku, partner=partner_slug, reverse=reverse):
                        items = [self._item(slug, price), self._item(partner_slug, partner_price)]
                        response = self._checkout(items[::-1] if reverse else items)
                        self.assertEqual(response.status_code, 409)
                        self.assertEqual(response.get_json()['error'], 'PRODUCT_UNAVAILABLE')
        self.create_session.assert_not_called()

    def test_repeated_lines_of_same_protected_unit_are_not_a_mixed_pack(self):
        for sku, slug, price in UNIT_CASES:
            with self.subTest(sku=sku):
                self.assertFalse(is_mixed_with_full_price_only_product([sku, sku]))
                response = self._checkout([self._item(slug, price), self._item(slug, price, 2)])
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(self.create_session.call_args.kwargs['metadata']['total'], format(price * 3, '.2f'))

    def test_component_pack_may_share_normal_cart_without_any_discounts(self):
        for sku, slug, price in PACK_CASES:
            with self.subTest(sku=sku):
                response = self._checkout([self._item(slug, price), self._item('producto-compatible', 10.00)])
                self.assertEqual(response.status_code, 200)
                params = self.create_session.call_args.kwargs
                self.assertEqual(params['metadata']['total'], format(price + Decimal('10.00'), '.2f'))
                self.assertNotIn('discounts', params)

    def test_unrelated_volume_discount_cannot_indirectly_discount_component_pack(self):
        for sku, slug, price in PACK_CASES:
            with self.subTest(sku=sku):
                response = self._checkout([
                    self._item(slug, price), self._item('producto-con-tier', 9.00, 3),
                ])
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
        self.create_session.assert_not_called()
        self.create_coupon.assert_not_called()
        self.holded_products.assert_not_called()

    def test_subscription_blocks_each_protected_identifier_and_identity_spoofing(self):
        ordinary = self._item('producto-compatible', 10.00)
        for sku, slug, price in PROTECTED_CASES:
            canonical = self._item(slug, price)
            attempts = (
                canonical,
                {'id': canonical['id']},
                {'slug': slug},
                {'sku': f' {sku.lower()} '},
                dict(canonical, sku='MIKOTHER1'),
                dict(ordinary, slug=slug),
                dict(ordinary, sku=sku),
                {'id': 999999, 'slug': slug, 'sku': 'MIKOTHER1'},
            )
            for attempt in attempts:
                with self.subTest(sku=sku, attempt=attempt):
                    response = self.client.post('/api/stripe/create-subscription-checkout', json={
                        'item': {**attempt, 'subscription_frequency': 'monthly', 'price': 1, 'quantity': 1},
                        'customer_info': self.customer,
                    })
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
        self.create_price.assert_not_called()
        self.create_session.assert_not_called()

    def test_subscription_missing_or_unresolved_identity_fails_closed(self):
        for item in ({'name': 'Temprano'}, {'slug': 'no-existe'}, {'id': 999999}, {'sku': 'SKU-UNKNOWN'}):
            with self.subTest(item=item):
                response = self.client.post('/api/stripe/create-subscription-checkout', json={
                    'item': {**item, 'subscription_frequency': 'monthly', 'price': 1, 'quantity': 1},
                    'customer_info': self.customer,
                })
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()['error'], 'PRODUCT_NOT_FOUND')
        self.create_price.assert_not_called()
        self.create_session.assert_not_called()

    def test_coupon_preview_and_use_accept_all_ordinary_protected_identity_contexts(self):
        ordinary = self._item('producto-compatible', 10.00)
        use_counts = {'BLACKFRIDAY10': 0, 'BLACKFRIDAY5': 0}
        for sku, slug, price in PROTECTED_CASES:
            canonical = self._item(slug, price)
            for item in (
                {'sku': f' {sku.lower()} '}, {'id': canonical['id']}, {'slug': slug},
                dict(canonical, sku='MIKOTHER1'), dict(ordinary, slug=slug),
            ):
                for code in ('BLACKFRIDAY10', 'BLACKFRIDAY5'):
                    with self.subTest(sku=sku, item=item, code=code):
                        payload = {'code': code, 'email': self.customer['email'], 'items': [item]}
                        preview = self.client.post('/api/coupon/validate', json=payload)
                        self.assertEqual(preview.status_code, 200)
                        self.assertTrue(preview.get_json()['valid'])
                        self.assertFalse(preview.get_json()['coupon']['valid_in_promotion'])
                        used = self.client.post('/api/coupon/use', json=payload)
                        self.assertEqual(used.status_code, 200)
                        self.assertTrue(used.get_json()['success'])
                        use_counts[code] += 1
        with self.app.app_context():
            for coupon in Coupon.query.all():
                self.assertFalse(coupon.used)
                self.assertEqual(coupon.current_uses, use_counts[coupon.code])
                self.assertFalse(coupon.valid_in_promotion)
        self.create_session.assert_not_called()
        self.create_coupon.assert_not_called()
        self.holded_products.assert_not_called()

    def test_context_free_coupon_preview_remains_valid_but_is_not_checkout_approval(self):
        response = self.client.post('/api/coupon/validate', json={'code': 'BLACKFRIDAY10'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['valid'])
        self.assertNotIn('email', response.get_json()['coupon'])

    def test_new_distinct_fixed_pack_skus_preserve_internal_quantities_and_price(self):
        for sku, slug, quantity, total, component in NEW_PACK_CASES:
            with self.subTest(sku=sku, component=component):
                self.assertFalse(is_full_price_only(sku))
                self.assertFalse(is_promotion_protected(sku))
                detail = self.client.get(f'/api/products/{slug}').get_json()
                self.assertEqual(detail['includes'], [f'{quantity} unidades de {component}'])
                self.assertEqual(detail['price'], float(total))
                self.assertNotIn('tieredDiscount', detail)
                response = self._checkout([self._item(slug, total, 1)])
                self.assertEqual(response.status_code, 200, response.get_json())
                params = self.create_session.call_args.kwargs
                line = params['line_items'][0]
                self.assertEqual(line['price_data']['unit_amount'], eur_to_cents(total))
                self.assertEqual(line['quantity'], 1)
                self.assertEqual(line['price_data']['product_data']['metadata']['sku'], sku)
                self.assertEqual(line['price_data']['product_data']['metadata']['physical_quantity'], '1')
                self.assertEqual(params['metadata']['total'], format(total, '.2f'))
                self.assertEqual(params['metadata']['discount_amount'], '0.00')
                self.assertNotIn('discounts', params)
        self.create_coupon.assert_not_called()

    def test_normal_cart_of_protected_units_does_not_add_new_mixed_restrictions(self):
        response = self._checkout([self._item(slug, price) for _, slug, price in UNIT_CASES])
        self.assertEqual(response.status_code, 200, response.get_json())
        expected = sum((case[2] for case in UNIT_CASES), Decimal('0.00'))
        params = self.create_session.call_args.kwargs
        self.assertEqual(params['metadata']['total'], format(expected, '.2f'))
        self.assertNotIn('discounts', params)

    def test_new_draft_containers_remain_unpurchasable_and_unpublished(self):
        for _, slug, _, _, _ in NEW_PACK_CASES:
            with self.app.app_context():
                product = WebProduct.query.filter_by(slug=slug).one()
                product.active = False
                product.visible_in_store = False
                db.session.commit()
        for sku, slug, _, total, _ in NEW_PACK_CASES:
            with self.subTest(sku=sku):
                self.assertEqual(self.client.get(f'/api/products/{slug}').status_code, 404)
                response = self._checkout([self._item(slug, total, 1)])
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()['error'], 'PRODUCT_UNAVAILABLE')
        self.create_session.assert_not_called()
        self.holded_products.assert_not_called()

    def test_fixed_container_quantity_means_packs_and_never_mutates_originals(self):
        for sku, slug, _, total, _ in NEW_PACK_CASES:
            with self.subTest(sku=sku):
                response = self._checkout([self._item(slug, total, 2)])
                self.assertEqual(response.status_code, 200, response.get_json())
                params = self.create_session.call_args.kwargs
                self.assertEqual(params['line_items'][0]['quantity'], 2)
                self.assertEqual(params['line_items'][0]['price_data']['unit_amount'], eur_to_cents(total))
                self.assertEqual(params['metadata']['total'], format(total * 2, '.2f'))
        with self.app.app_context():
            for sku, slug, price in PROTECTED_CASES:
                with self.subTest(original=sku):
                    product = WebProduct.query.filter_by(slug=slug).one()
                    self.assertEqual(Decimal(str(product.price)), price)
                    self.assertTrue(product.active)
                    self.assertTrue(product.visible_in_store)
                    self.assertEqual(product.volume_discount, {'minQuantity': 1, 'discount': 25})
                    self.assertEqual(product.tiered_discount[0]['price'], 1.00)

    def test_new_fixed_pack_sku_can_share_cart_with_nonprotected_items(self):
        sku, slug, quantity, total, _ = NEW_PACK_CASES[2]
        response = self._checkout([
            self._item(slug, total, 1), self._item('producto-compatible', 10.00),
        ])
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.create_session.call_args.kwargs['metadata']['total'], format(total + Decimal('10.00'), '.2f'))

    def test_previous_harvest_sku_retains_existing_pricing(self):
        response = self._checkout([self._item('temprano-2025-26', Decimal('15.435'), 3)])
        self.assertEqual(response.status_code, 200, response.get_json())
        params = self.create_session.call_args.kwargs
        self.assertEqual(params['metadata']['volume_discount_amount'], '5.14')
        self.assertEqual(params['metadata']['total'], '46.31')
        self.create_coupon.assert_called_once()
        detail = self.client.get('/api/products/temprano-2025-26').get_json()
        self.assertIn('tieredDiscount', detail)

    def test_drafts_and_zero_stock_fail_closed_before_pricing(self):
        for slug, price in (('borrador-privado', 119.40), ('agotado-privado', 12.00)):
            with self.subTest(slug=slug):
                response = self._checkout([self._item(slug, price)])
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()['error'], 'PRODUCT_UNAVAILABLE')
        self.create_session.assert_not_called()
        self.holded_products.assert_not_called()


if __name__ == '__main__':
    unittest.main()
