import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from src.models.user import db
from src.models.order import Order
from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot
from src.models.coupon import Coupon
from src.models.web_product import WebProduct
# Imported so SQLAlchemy creates the support tables touched by the checkout
# route while this focused suite exercises a real session creation path.
from src.models.stock import StockReservation, StockMovement  # noqa: F401
from src.models.abandoned_cart import AbandonedCart  # noqa: F401
from src.routes.coupon_routes import coupon_bp
from src.routes.stripe_routes import stripe_bp
from src.services.money import cents_to_eur, eur_to_cents


CATALOGUE_PRICES = (
    ('aceite-temprano-sin-filtrar', '19.90'),
    ('paraguayo-almibar', '17.15'),
    ('nectarina-almibar', '17.15'),
    ('aceite-oliva-ecologico', '19.90'),
    ('pack-mermelada-aceites', '19.90'),
    ('mermelada-paraguayo', '19.90'),
    ('pack-fruta-premium', '40.30'),
    ('aceite-5l-caja-3', '43.00'),
    ('pack-navidad-completo', '94.20'),
)


class CheckoutMoneyIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(stripe_bp)
        self.app.register_blueprint(coupon_bp, url_prefix='/api/coupon')

        with self.app.app_context():
            db.create_all()
            for index, (slug, price) in enumerate(CATALOGUE_PRICES, start=1):
                db.session.add(WebProduct(
                    name=f'Producto {index}',
                    slug=slug,
                    sku=f'MIKTEST{index:02d}',
                    price=float(price),
                    category='Prueba',
                    stock=999,
                    active=True,
                    visible_in_store=True,
                    tiered_discount=(
                        [
                            {'actualQuantity': 1, 'label': '1 botella 500 ml', 'price': 19.90},
                            {'actualQuantity': 3, 'label': 'Pack de 3', 'featured': True, 'price': 56.70},
                            {'actualQuantity': 6, 'label': 'Pack de 6', 'price': 107.40},
                            {'actualQuantity': 12, 'label': 'Caja de 12', 'price': 202.80},
                        ]
                        if index == 1 else None
                    ),
                ))
            db.session.add(Coupon(
                code='PRUEBA10',
                description='Cupón de prueba general',
                discount_type='percentage',
                discount_value=10,
                active=True,
            ))
            db.session.commit()

        self.client = self.app.test_client()
        self.holded_products = patch(
            'src.services.holded_service.holded_get_products',
            return_value=[{'sku': 'MIKTEST01'}],
        )
        self.tax_rules = patch(
            'src.services.order_tax_snapshot.build_tax_rule_snapshot',
            return_value={'MIKTEST01': {'kind': 'single', 'rate': '0.04'}},
        )
        self.holded_products.start()
        self.tax_rules.start()
        self.customer = {
            'email': 'cliente@example.com',
            'name': 'Cliente de prueba',
            'phone': '+34600000000',
            'address': 'Calle de prueba 1',
            'city': 'Lleida',
            'postal_code': '25001',
            'country': 'España',
        }

    def tearDown(self):
        self.holded_products.stop()
        self.tax_rules.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _checkout_item(self, index, slug, price, quantity=1):
        return {
            'id': index,
            'slug': slug,
            'name': f'Precio mostrado {price}',
            'price': float(price),
            'quantity': quantity,
        }

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_checkout_rejects_missing_delivery_email_or_phone(self, create_session):
        for field in ('email', 'phone'):
            with self.subTest(field=field):
                customer = dict(self.customer)
                customer[field] = '   '
                response = self.client.post('/api/stripe/create-checkout-session', json={
                    'items': [self._checkout_item(1, 'aceite-temprano-sin-filtrar', '19.90')],
                    'customer_info': customer,
                })

                self.assertEqual(response.status_code, 400)
                payload = response.get_json()
                self.assertEqual(payload['error'], 'MISSING_REQUIRED_CUSTOMER_INFO')
                self.assertEqual(payload['missing_fields'], [field])

        create_session.assert_not_called()

    def test_coupon_validation_accepts_a_valid_coupon_without_disclosing_owner_email(self):
        with self.app.app_context():
            db.session.add(Coupon(
                code='MIKELS-PRIVATE',
                description='Cupón de bienvenida newsletter',
                email='suscriptora@example.com',
                discount_type='percentage',
                discount_value=10,
                active=True,
            ))
            db.session.commit()

        response = self.client.post('/api/coupon/validate', json={'code': 'MIKELS-PRIVATE'})

        self.assertEqual(response.status_code, 200)
        coupon = response.get_json()['coupon']
        self.assertNotIn('eligible_for_temprano_reservation', coupon)
        self.assertNotIn('email', coupon)

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_all_live_catalogue_prices_are_sent_to_stripe_as_exact_cents(self, create_session):
        create_session.return_value = SimpleNamespace(id='cs_test_catalogue', url='https://checkout.stripe.test/catalogue')

        items = [
            self._checkout_item(index, slug, price)
            for index, (slug, price) in enumerate(CATALOGUE_PRICES, start=1)
        ]
        response = self.client.post('/api/stripe/create-checkout-session', json={
            'items': items,
            'customer_info': self.customer,
        })

        self.assertEqual(response.status_code, 200)
        stripe_items = create_session.call_args.kwargs['line_items']
        cents = [entry['price_data']['unit_amount'] for entry in stripe_items]
        expected = [eur_to_cents(price) for _, price in CATALOGUE_PRICES]
        self.assertEqual(cents, expected)
        self.assertEqual(cents_to_eur(cents[0]), Decimal('19.90'))
        self.assertEqual(cents_to_eur(cents[3]), Decimal('19.90'))
        self.assertEqual(cents_to_eur(cents[6]), Decimal('40.30'))
        self.assertNotIn('shipping_address_collection', create_session.call_args.kwargs)
        self.assertNotIn('phone_number_collection', create_session.call_args.kwargs)
        self.assertEqual(create_session.call_args.kwargs['metadata']['shipping_country_code'], 'ES')
        self.assertEqual(create_session.call_args.kwargs['metadata']['meta_marketing_consent'], 'false')

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_checkout_rejects_unserved_or_excluded_destinations_before_stripe(self, create_session):
        cases = (
            ('Francia', '75001', 'DESTINATION_NOT_SERVED'),
            ('España', '35001', 'DESTINATION_NOT_SERVED'),
            ('España', '38001', 'DESTINATION_NOT_SERVED'),
            ('España', '51001', 'DESTINATION_NOT_SERVED'),
            ('España', '52001', 'DESTINATION_NOT_SERVED'),
            ('España', '07001', 'BALEARES_MINIMUM_ORDER'),
        )
        for country, postal_code, expected_error in cases:
            with self.subTest(country=country, postal_code=postal_code):
                customer = dict(self.customer, country=country, postal_code=postal_code)
                response = self.client.post('/api/stripe/create-checkout-session', json={
                    'items': [self._checkout_item(1, 'aceite-temprano-sin-filtrar', '19.90')],
                    'customer_info': customer,
                })
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.get_json()['error'], expected_error)

        create_session.assert_not_called()

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_checkout_accepts_portugal_and_baleares_from_the_minimum_order(self, create_session):
        cases = (
            ('Portugal', '4000-001', 1, 'cs_test_portugal', 'PT'),
            ('España', '07001', 6, 'cs_test_baleares', 'ES'),
        )
        for country, postal_code, quantity, session_id, country_code in cases:
            with self.subTest(country=country, postal_code=postal_code):
                create_session.reset_mock()
                create_session.return_value = SimpleNamespace(
                    id=session_id,
                    url=f'https://checkout.stripe.test/{session_id}',
                )
                customer = dict(self.customer, country=country, postal_code=postal_code)
                browser_unit_price = '19.90' if quantity == 1 else '17.90'
                response = self.client.post('/api/stripe/create-checkout-session', json={
                    'items': [self._checkout_item(1, 'aceite-temprano-sin-filtrar', browser_unit_price, quantity=quantity)],
                    'customer_info': customer,
                })
                self.assertEqual(response.status_code, 200)
                params = create_session.call_args.kwargs
                self.assertEqual(params['metadata']['shipping_country_code'], country_code)
                self.assertNotIn('shipping_address_collection', params)

    @patch('src.routes.stripe_routes.stripe.Coupon.create')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_multi_line_discount_preserves_exact_charge_and_metadata(self, create_session, create_coupon):
        create_session.return_value = SimpleNamespace(id='cs_test_discount', url='https://checkout.stripe.test/discount')
        create_coupon.return_value = SimpleNamespace(id='coupon_test_10')
        response = self.client.post('/api/stripe/create-checkout-session', json={
            'items': [
                self._checkout_item(1, 'aceite-temprano-sin-filtrar', '19.90', quantity=1),
                self._checkout_item(4, 'aceite-oliva-ecologico', '19.90', quantity=1),
                self._checkout_item(7, 'pack-fruta-premium', '40.30', quantity=1),
            ],
            'customer_info': self.customer,
            'discount_code': 'PRUEBA10',
            # The server must ignore this browser-provided amount and derive
            # the valid code's 10% discount from its own subtotal.
            'discount_amount': 0,
        })

        self.assertEqual(response.status_code, 200)
        params = create_session.call_args.kwargs
        self.assertEqual(
            [entry['price_data']['unit_amount'] for entry in params['line_items']],
            [1990, 1990, 4030],
        )
        self.assertEqual(create_coupon.call_args.kwargs['amount_off'], 801)
        self.assertEqual(params['metadata']['subtotal'], '80.10')
        self.assertEqual(params['metadata']['coupon_discount_amount'], '8.01')
        self.assertEqual(params['metadata']['discount_amount'], '8.01')
        self.assertEqual(params['metadata']['total'], '72.09')

    @patch('src.routes.stripe_routes.stripe.Coupon.create')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_each_ordinary_pack_creates_a_checkout_at_its_published_total(
        self,
        create_session,
        create_coupon,
    ):
        """The server charges each normal fixed pack as one exact line."""
        cases = (
            (1, Decimal('19.90')),
            (3, Decimal('56.70')),
            (6, Decimal('107.40')),
            (12, Decimal('202.80')),
        )
        for quantity, expected_total in cases:
            with self.subTest(quantity=quantity):
                create_session.reset_mock()
                create_coupon.reset_mock()
                create_session.return_value = SimpleNamespace(
                    id=f'cs_test_pack_{quantity}',
                    url=f'https://checkout.stripe.test/pack-{quantity}',
                )
                cart_unit_price = (expected_total / Decimal(quantity)).quantize(Decimal('0.0001'))
                response = self.client.post('/api/stripe/create-checkout-session', json={
                    'items': [self._checkout_item(
                        1,
                        'aceite-temprano-sin-filtrar',
                        cart_unit_price,
                        quantity=quantity,
                    )],
                    'customer_info': self.customer,
                })

                self.assertEqual(response.status_code, 200)
                params = create_session.call_args.kwargs
                self.assertEqual(
                    params['line_items'][0]['price_data']['unit_amount'],
                    eur_to_cents(expected_total),
                )
                self.assertEqual(params['line_items'][0]['quantity'], 1)
                self.assertEqual(params['metadata']['subtotal'], format(expected_total, '.2f'))
                self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                self.assertEqual(params['metadata']['discount_amount'], '0.00')
                self.assertEqual(params['metadata']['total'], format(expected_total, '.2f'))
                charged_total = Decimal(params['line_items'][0]['price_data']['unit_amount']) / Decimal('100')
                self.assertEqual(charged_total, expected_total)
                create_coupon.assert_not_called()

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_ordinary_pack_rejects_a_price_that_does_not_match_its_total(self, create_session):
        create_session.return_value = SimpleNamespace(id='cs_should_not_exist', url='https://checkout.stripe.test/nope')
        response = self.client.post('/api/stripe/create-checkout-session', json={
            'items': [self._checkout_item(1, 'aceite-temprano-sin-filtrar', '19.90', quantity=12)],
            'customer_info': self.customer,
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'PRICE_MISMATCH')
        create_session.assert_not_called()

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_temprano_uses_the_master_price_for_every_quantity(self, create_session):
        packs = (
            (1, Decimal('19.90'), Decimal('19.90')),
            (3, Decimal('59.70'), Decimal('19.90')),
            (6, Decimal('119.40'), Decimal('19.90')),
            (12, Decimal('238.80'), Decimal('19.90')),
        )
        with self.app.app_context():
            temprano = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').first()
            temprano.sku = 'MIKVET500'
            temprano.tiered_discount = [
                {'actualQuantity': 1, 'price': 19.90},
                {'actualQuantity': 3, 'price': 56.70},
                {'actualQuantity': 6, 'price': 107.40},
                {'actualQuantity': 12, 'price': 202.80},
            ]
            db.session.commit()

        for quantity, expected_total, browser_unit_price in packs:
            with self.subTest(quantity=quantity):
                create_session.reset_mock()
                create_session.return_value = SimpleNamespace(
                    id=f'cs_test_pack_{quantity}', url=f'https://checkout.stripe.test/pack-{quantity}',
                )
                response = self.client.post('/api/stripe/create-checkout-session', json={
                    'items': [self._checkout_item(1, 'aceite-temprano-sin-filtrar', browser_unit_price, quantity=quantity)],
                    'customer_info': self.customer,
                })
                self.assertEqual(response.status_code, 200)
                line = create_session.call_args.kwargs['line_items'][0]
                self.assertEqual(line['quantity'], quantity)
                self.assertEqual(line['price_data']['unit_amount'], eur_to_cents(Decimal('19.90')))
                metadata = line['price_data']['product_data']['metadata']
                self.assertEqual(metadata['physical_quantity'], str(quantity))
                self.assertEqual(create_session.call_args.kwargs['metadata']['total'], format(expected_total, '.2f'))

    @patch('src.routes.stripe_routes.stripe.Coupon.create')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_temprano_accepts_normal_valid_coupons_at_19_90_without_quantity_discount(self, create_session, create_coupon):
        create_coupon.return_value = SimpleNamespace(id='coupon_temprano')
        with self.app.app_context():
            temprano = WebProduct.query.filter_by(slug='aceite-temprano-sin-filtrar').first()
            temprano.sku = 'MIKVET500'
            temprano.tiered_discount = [
                {'actualQuantity': 1, 'price': 19.90},
                {'actualQuantity': 3, 'price': 56.70},
                {'actualQuantity': 6, 'price': 107.40},
                {'actualQuantity': 12, 'price': 202.80},
            ]
            db.session.add(Coupon(
                code='MIKELS-SUBSCRIBER',
                description='Cupón de bienvenida newsletter',
                email=self.customer['email'],
                discount_type='percentage',
                discount_value=10,
                max_uses=1,
                active=True,
            ))
            db.session.add(Coupon(
                code='IRVIANCESTRAL',
                description='Cupón colaborador',
                discount_type='percentage',
                discount_value=10,
                active=True,
            ))
            db.session.add(Coupon(
                code='VUELVE10-CUSTOMER',
                description='Cupón de próxima compra',
                email=self.customer['email'],
                discount_type='percentage',
                discount_value=10,
                active=True,
            ))
            db.session.add(Coupon(
                code='MIKELS-ALREADY-USED',
                description='Cupón de bienvenida ya utilizado',
                email=self.customer['email'],
                discount_type='percentage',
                discount_value=10,
                max_uses=1,
                current_uses=1,
                active=False,
                used=True,
            ))
            db.session.commit()

        for code in ('mikels-subscriber', 'IRVIANCESTRAL', 'VUELVE10-CUSTOMER', 'PRUEBA10'):
            for quantity in (1, 3, 6, 12):
                with self.subTest(code=code, quantity=quantity):
                    create_session.return_value = SimpleNamespace(id=f'cs_temprano_{code}_{quantity}', url='https://stripe.test/local')
                    result = self.client.post('/api/stripe/create-checkout-session', json={
                        'items': [self._checkout_item(1, 'aceite-temprano-sin-filtrar', '19.90', quantity=quantity)],
                        'customer_info': self.customer, 'discount_code': code, 'discount_amount': 999999,
                    })
                    self.assertEqual(result.status_code, 200, result.get_json())
                    params = create_session.call_args.kwargs
                    self.assertEqual(params['line_items'][0]['price_data']['unit_amount'], 1990)
                    self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
                    self.assertEqual(params['metadata']['coupon_discount_amount'], format(Decimal('1.99') * quantity, '.2f'))
                    self.assertEqual(params['metadata']['total'], format(Decimal('17.91') * quantity, '.2f'))
                    self.assertEqual(create_coupon.call_args.kwargs['amount_off'], 199 * quantity)
        create_session.reset_mock()
        create_coupon.reset_mock()
        used_coupon = self.client.post('/api/stripe/create-checkout-session', json={
            'items': [self._checkout_item(1, 'aceite-temprano-sin-filtrar', '19.90')],
            'customer_info': self.customer, 'discount_code': 'MIKELS-ALREADY-USED',
        })
        self.assertEqual(used_coupon.status_code, 422)
        self.assertEqual(used_coupon.get_json()['error'], 'COUPON_INVALID')
        create_session.assert_not_called()
        create_coupon.assert_not_called()

    def test_exact_cent_helpers_never_truncate_binary_float_values(self):
        self.assertEqual(eur_to_cents(17.15), 1715)
        self.assertEqual(eur_to_cents(19.90), 1990)
        self.assertEqual(eur_to_cents(40.30), 4030)
        self.assertEqual(cents_to_eur(1715), Decimal('17.15'))

    @patch('src.services.email_dispatcher.dispatch_post_purchase_event')
    @patch('src.routes.stripe_routes.dispatch_order_confirmation')
    @patch('src.routes.stripe_routes.dispatch_order_notification')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items')
    @patch('src.routes.stripe_routes.stripe.Webhook.construct_event')
    def test_webhook_persists_the_exact_stripe_charge_for_panel_and_holded_source(
        self,
        construct_event,
        list_line_items,
        _dispatch_notification,
        _dispatch_confirmation,
        _dispatch_post_purchase,
    ):
        construct_event.return_value = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'id': 'cs_test_exact_charge',
                'mode': 'payment',
                'amount_total': 1715,
                'payment_intent': 'pi_test_exact_charge',
                'customer_email': 'cliente@example.com',
                'customer_details': {'email': 'cliente@example.com', 'phone': '+34600000000'},
                'shipping_details': {'name': 'Cliente de prueba', 'address': {
                    'line1': 'Calle de prueba 1', 'city': 'Lleida', 'postal_code': '25001', 'country': 'ES',
                }},
                'metadata': {
                    'order_number': 'MKL-TEST-EXACT-CHARGE',
                    'customer_name': 'Cliente de prueba',
                    'subtotal': '17.15',
                    'discount_amount': '0.00',
                    'discount_code': '',
                    'needs_invoice': 'False',
                    'stock_checkout_token': 'tax-test-exact-charge',
                },
            }},
        }
        list_line_items.return_value = SimpleNamespace(data=[SimpleNamespace(
            amount_total=1715,
            quantity=1,
            description='Precio mostrado 17.15',
            price=SimpleNamespace(product=SimpleNamespace(metadata={
                'sku': 'MIKTEST01',
                'slug': 'aceite-temprano-sin-filtrar',
                'checkout_line_key': '0',
            })),
        )])

        with self.app.app_context():
            db.session.add(CheckoutTaxSnapshot(
                checkout_token='tax-test-exact-charge',
                rules={
                    'MIKTEST01': {'kind': 'single', 'rate': '0.04'},
                    '__checkout_pricing__': {
                        '0': {'receipt_line_total': '17.15'},
                    },
                },
                expires_at=datetime.utcnow() + timedelta(minutes=30),
            ))
            db.session.commit()

        response = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 'test-signature'})
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            order = Order.query.filter_by(order_number='MKL-TEST-EXACT-CHARGE').one()
            # These fields are the exact values later exposed by the panel and
            # used as the source amounts for the validated Holded document path.
            self.assertEqual(Decimal(str(order.subtotal)), Decimal('17.15'))
            self.assertEqual(Decimal(str(order.total)), Decimal('17.15'))
            self.assertEqual(Decimal(str(order.to_dict()['total'])), Decimal('17.15'))
            self.assertEqual(Decimal(str(order.items[0]['price'])), Decimal('17.15'))
            self.assertEqual(Decimal(str(order.items[0]['gross_total'])), Decimal('17.15'))
            self.assertEqual(Decimal(str(order.items[0]['receipt_line_total'])), Decimal('17.15'))
            self.assertEqual(Decimal(str(order.discount_amount)), Decimal('0.00'))

    @patch('src.services.email_dispatcher.dispatch_post_purchase_event')
    @patch('src.routes.stripe_routes.dispatch_order_confirmation')
    @patch('src.routes.stripe_routes.dispatch_order_notification')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items')
    @patch('src.routes.stripe_routes.stripe.Webhook.construct_event')
    def test_webhook_preserves_volume_tier_receipt_and_exact_charge(
        self,
        construct_event,
        list_line_items,
        _dispatch_notification,
        _dispatch_confirmation,
        _dispatch_post_purchase,
    ):
        construct_event.return_value = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'id': 'cs_test_volume_tier',
                'mode': 'payment',
                'amount_total': 21890,
                'payment_intent': 'pi_test_volume_tier',
                'customer_email': 'cliente@example.com',
                'customer_details': {'email': 'cliente@example.com', 'phone': '+34600000000'},
                'shipping_details': {'name': 'Cliente de prueba', 'address': {
                    'line1': 'Calle de prueba 1', 'city': 'Lleida', 'postal_code': '25001', 'country': 'ES',
                }},
                'metadata': {
                    'order_number': 'MKL-TEST-VOLUME-TIER',
                    'customer_name': 'Cliente de prueba',
                    'subtotal': '238.80',
                    'discount_amount': '19.90',
                    'discount_code': '',
                    'needs_invoice': 'False',
                    'stock_checkout_token': 'tax-test-volume-tier',
                },
            }},
        }
        list_line_items.return_value = SimpleNamespace(data=[SimpleNamespace(
            amount_total=21890,
            quantity=12,
            description='Aceite temprano sin filtrar',
            price=SimpleNamespace(product=SimpleNamespace(metadata={
                'sku': 'MIKTEST01',
                'slug': 'aceite-temprano-sin-filtrar',
                'checkout_line_key': '0',
            })),
        )])

        with self.app.app_context():
            db.session.add(CheckoutTaxSnapshot(
                checkout_token='tax-test-volume-tier',
                rules={
                    'MIKTEST01': {'kind': 'single', 'rate': '0.04'},
                    '__checkout_pricing__': {
                        '0': {'receipt_line_total': '238.80', 'reservation_only': True},
                    },
                },
                expires_at=datetime.utcnow() + timedelta(minutes=30),
            ))
            db.session.commit()

        response = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 'test-signature'})
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            order = Order.query.filter_by(order_number='MKL-TEST-VOLUME-TIER').one()
            self.assertEqual(Decimal(str(order.subtotal)), Decimal('238.80'))
            self.assertEqual(Decimal(str(order.total)), Decimal('218.90'))
            self.assertEqual(Decimal(str(order.items[0]['gross_total'])), Decimal('218.90'))
            self.assertEqual(Decimal(str(order.items[0]['receipt_line_total'])), Decimal('238.80'))
            self.assertTrue(order.items[0]['reservation_only'])
            self.assertEqual(Decimal(str(order.discount_amount)), Decimal('19.90'))
            self.assertEqual(order.receipt_snapshot['heading'], 'Reserva confirmada')
            self.assertEqual(order.receipt_snapshot['lines'][0]['amount_display'], '238,80 €')
            self.assertEqual(order.receipt_snapshot['totals']['discount_display'], '19,90 €')
            self.assertEqual(order.receipt_snapshot['totals']['total_display'], '218,90 €')


if __name__ == '__main__':
    unittest.main()
