import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.models.order import Order  # noqa: F401
from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot  # noqa: F401
from src.models.stock import StockReservation, StockMovement  # noqa: F401
from src.routes.product_routes import product_bp
from src.routes.stripe_routes import stripe_bp


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
        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                WebProduct(
                    name='Temprano 2026/27',
                    slug='aceite-temprano-sin-filtrar',
                    sku='MIKVET500',
                    price=19.90,
                    category='Aceites',
                    stock=30,
                    active=True,
                    visible_in_store=True,
                    subscription_available=True,
                    subscription_discount=10,
                    subscription_frequencies=[{'value': 'monthly', 'discount': 10}],
                    tiered_discount=[
                        {'actualQuantity': 1, 'label': '1 botella', 'price': 19.90},
                        {'actualQuantity': 3, 'label': 'Pack de 3', 'price': 56.70},
                    ],
                    addons=[{'productSlug': 'estuche-regalo-temprano'}],
                ),
                WebProduct(
                    name='Producto compatible',
                    slug='producto-compatible',
                    sku='MIKOTHER1',
                    price=10.00,
                    category='Packs',
                    stock=30,
                    active=True,
                    visible_in_store=True,
                ),
                WebProduct(
                    name='Pack de receta Holded',
                    slug='pack-recipe-holded',
                    sku='MIKPACKF',
                    price=50.00,
                    category='Packs',
                    stock=30,
                    active=True,
                    visible_in_store=True,
                ),
                WebProduct(
                    name='Borrador oculto',
                    slug='borrador-privado',
                    sku='MIKDRAFT1',
                    price=119.40,
                    category='Aceites',
                    stock=1200,
                    active=False,
                    visible_in_store=False,
                ),
                WebProduct(
                    name='Agotado',
                    slug='agotado-privado',
                    sku='MIKOUT1',
                    price=12.00,
                    category='Packs',
                    stock=0,
                    active=True,
                    visible_in_store=True,
                ),
            ])
            db.session.commit()
        self.client = self.app.test_client()
        self.customer = {
            'email': 'cliente@example.com',
            'name': 'Cliente de prueba',
            'phone': '+34600000000',
            'address': 'Calle de prueba 1',
            'city': 'Lleida',
            'postal_code': '25001',
            'country': 'España',
        }
        self.holded_products = patch(
            'src.services.holded_service.holded_get_products',
            return_value=[{'sku': 'MIKVET500'}],
        )
        self.tax_rules = patch(
            'src.services.order_tax_snapshot.build_tax_rule_snapshot',
            return_value={'MIKVET500': {'kind': 'single', 'rate': '0.04'}},
        )
        self.holded_products.start()
        self.tax_rules.start()

    def tearDown(self):
        self.holded_products.stop()
        self.tax_rules.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _item(self, slug, price, quantity=1):
        with self.app.app_context():
            product = WebProduct.query.filter_by(slug=slug).one()
            return {
                'id': product.id,
                'slug': slug,
                'name': product.name,
                'price': price,
                'quantity': quantity,
            }

    def _checkout(self, items, **extra):
        return self.client.post('/api/stripe/create-checkout-session', json={
            'items': items,
            'customer_info': self.customer,
            **extra,
        })

    def test_protected_sku_public_serializer_omits_all_promotions_and_addons(self):
        response = self.client.get('/api/products/aceite-temprano-sin-filtrar')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['price'], 19.90)
        self.assertFalse(payload['subscriptionAvailable'])
        for key in (
            'tieredDiscount', 'volumeDiscount', 'subscriptionDiscount',
            'subscriptionFrequencies', 'addons', 'stock', 'reservationStockTotal',
        ):
            self.assertNotIn(key, payload)

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_protected_sku_uses_only_its_master_price_not_persisted_tiers(self, create_session):
        create_session.return_value = SimpleNamespace(
            id='cs_full_price', url='https://checkout.stripe.test/full-price',
        )
        response = self._checkout([
            self._item('aceite-temprano-sin-filtrar', 19.90, quantity=3),
        ])

        self.assertEqual(response.status_code, 200)
        params = create_session.call_args.kwargs
        self.assertEqual(params['line_items'][0]['price_data']['unit_amount'], 1990)
        self.assertEqual(params['line_items'][0]['quantity'], 3)
        self.assertEqual(params['metadata']['subtotal'], '59.70')
        self.assertEqual(params['metadata']['volume_discount_amount'], '0.00')
        self.assertEqual(params['metadata']['discount_amount'], '0.00')
        self.assertEqual(params['metadata']['total'], '59.70')
        self.assertNotIn('discounts', params)

    @patch('src.routes.stripe_routes.stripe.Coupon.create')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_protected_sku_rejects_coupon_without_opening_stripe(self, create_session, create_coupon):
        response = self._checkout(
            [self._item('aceite-temprano-sin-filtrar', 19.90)],
            discount_code='BLACKFRIDAY10',
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
        create_session.assert_not_called()
        create_coupon.assert_not_called()

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_protected_sku_rejects_mixed_cart_without_opening_stripe(self, create_session):
        response = self._checkout([
            self._item('aceite-temprano-sin-filtrar', 19.90),
            self._item('producto-compatible', 10.00),
        ])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'PRODUCT_UNAVAILABLE')
        create_session.assert_not_called()

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_confirmed_holded_recipe_pack_sells_normally_but_rejects_promotion(self, create_session):
        create_session.return_value = SimpleNamespace(
            id='cs_pack_full_price', url='https://checkout.stripe.test/pack-full-price',
        )
        normal_sale = self._checkout([self._item('pack-recipe-holded', 50.00)])

        self.assertEqual(normal_sale.status_code, 200)
        self.assertEqual(create_session.call_args.kwargs['metadata']['total'], '50.00')

        create_session.reset_mock()
        promotion_attempt = self._checkout(
            [self._item('pack-recipe-holded', 50.00)],
            discount_code='BLACKFRIDAY10',
        )
        self.assertEqual(promotion_attempt.status_code, 422)
        self.assertEqual(promotion_attempt.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
        create_session.assert_not_called()

    @patch('src.routes.stripe_routes.stripe.Price.create')
    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_protected_sku_rejects_subscription(self, create_session, create_price):
        response = self.client.post('/api/stripe/create-subscription-checkout', json={
            'item': {
                **self._item('aceite-temprano-sin-filtrar', 19.90),
                'subscription_frequency': 'monthly',
                'sku': 'MIKVET500',
            },
            'customer_info': self.customer,
        })

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()['error'], 'PROMOTION_NOT_AVAILABLE')
        create_price.assert_not_called()
        create_session.assert_not_called()

    @patch('src.routes.stripe_routes.stripe.checkout.Session.create')
    def test_drafts_hidden_products_and_zero_stock_fail_closed_before_pricing(self, create_session):
        for slug, price in (('borrador-privado', 119.40), ('agotado-privado', 12.00)):
            with self.subTest(slug=slug):
                response = self._checkout([self._item(slug, price)])
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()['error'], 'PRODUCT_UNAVAILABLE')
        create_session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
