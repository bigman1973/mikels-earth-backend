import unittest

from flask import Flask

from src.models.user import db
from src.models.web_product import WebProduct
from src.models.order import Order  # noqa: F401
from src.services.previous_harvest_draft import (
    AWARD,
    DRAFT_TITLE,
    LOT,
    PROPOSED_SKU,
    PROPOSED_SLUG,
    STOREFRONT_NOTICE,
    seed_previous_harvest_draft,
)


class PreviousHarvestDraftSeedTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_seed_creates_non_public_six_and_twelve_bottle_formats(self):
        with self.app.app_context():
            product, action, used_fallback = seed_previous_harvest_draft()
            db.session.commit()

            self.assertEqual(action, 'created')
            self.assertFalse(used_fallback)
            self.assertEqual(product.name, DRAFT_TITLE)
            self.assertEqual(product.slug, PROPOSED_SLUG)
            self.assertEqual(product.sku, PROPOSED_SKU)
            self.assertEqual(product.price, 59.70)
            self.assertIsNone(product.original_price)
            self.assertEqual(product.stock, 0)
            self.assertFalse(product.active)
            self.assertFalse(product.visible_in_store)
            self.assertFalse(product.sold_out)
            self.assertFalse(product.reservation_only)
            self.assertIsNone(product.reservation_stock_total)
            self.assertEqual(product.image, None)
            self.assertEqual(product.images, [])
            self.assertEqual(product.weight, '500 ml')
            self.assertEqual(product.award, AWARD)
            self.assertEqual(product.storefront_notice, STOREFRONT_NOTICE)
            self.assertEqual(product.addons, [])
            self.assertEqual(product.related_products, [])
            self.assertEqual(product.badges, [])
            self.assertEqual(product.claims, [])
            self.assertIsNone(product.volume_discount)
            self.assertEqual(product.tiered_discount, [{
                'actualQuantity': 6,
                'label': 'Pack de 6 botellas de 500 ml',
                'price': 59.70,
            }, {
                'actualQuantity': 12,
                'label': 'Caja de 12 botellas de 500 ml',
                'price': 119.40,
            }])
            self.assertIn(LOT, product.description)
            self.assertIn(AWARD, product.description)
            self.assertIn('119,40 EUR', product.description)
            self.assertIn('59,70 EUR', product.description)
            self.assertIn('9,95 EUR', product.description)
            self.assertIn(LOT, product.description_en)
            for prohibited in ('82779', '569', 'polifenoles', 'biofenoles', 'escasez', 'stock'):
                self.assertNotIn(prohibited, product.description.casefold())
                self.assertNotIn(prohibited, product.long_description.casefold())

            public_payload = product.to_frontend_dict()
            self.assertFalse(public_payload['available'])
            self.assertNotIn('stock', public_payload)
            self.assertNotIn('reservationStockTotal', public_payload)
            self.assertEqual(public_payload['storefrontNotice'], STOREFRONT_NOTICE)

    def test_closed_formats_price_exactly_and_never_sell_single_bottles(self):
        from decimal import Decimal
        from src.services.checkout_pricing import calculate_checkout_line_price

        with self.app.app_context():
            product, _, _ = seed_previous_harvest_draft()
            for quantity, total in [(6, Decimal('59.70')), (12, Decimal('119.40'))]:
                line = calculate_checkout_line_price(product, quantity)
                self.assertEqual(line.expected_line_total, total)
                self.assertEqual(line.base_unit_price, Decimal('9.9500'))
                self.assertTrue(line.fixed_pack)
            for quantity in (1, 3, 7):
                with self.assertRaises(ValueError):
                    calculate_checkout_line_price(product, quantity)

    def test_seed_is_idempotent_and_does_not_mutate_the_current_harvest(self):
        with self.app.app_context():
            current_harvest = WebProduct(
                name='Current harvest unchanged',
                slug='aceite-temprano-sin-filtrar',
                sku='MIKVET500',
                description='Existing catalogue content',
                price=19.90,
                category='Aceites',
                stock=44,
                active=True,
                visible_in_store=True,
                tiered_discount=[{'actualQuantity': 3, 'price': 56.70}],
            )
            db.session.add(current_harvest)
            db.session.commit()
            before = {
                'name': current_harvest.name,
                'slug': current_harvest.slug,
                'sku': current_harvest.sku,
                'description': current_harvest.description,
                'price': current_harvest.price,
                'stock': current_harvest.stock,
                'tiered_discount': current_harvest.tiered_discount,
            }

            first, first_action, _ = seed_previous_harvest_draft()
            db.session.commit()
            second, second_action, _ = seed_previous_harvest_draft()
            db.session.commit()

            self.assertEqual(first_action, 'created')
            self.assertEqual(second_action, 'updated')
            self.assertEqual(first.id, second.id)
            self.assertEqual(WebProduct.query.filter_by(name=DRAFT_TITLE).count(), 1)
            db.session.refresh(current_harvest)
            self.assertEqual({
                'name': current_harvest.name,
                'slug': current_harvest.slug,
                'sku': current_harvest.sku,
                'description': current_harvest.description,
                'price': current_harvest.price,
                'stock': current_harvest.stock,
                'tiered_discount': current_harvest.tiered_discount,
            }, before)

    def test_boot_never_resets_existing_inventory_or_reviewed_photos(self):
        with self.app.app_context():
            product, _, _ = seed_previous_harvest_draft()
            product.stock = 37
            product.image = 'https://example.test/reviewed-previous-harvest.webp'
            product.images = [product.image]
            db.session.commit()
            updated, action, _ = seed_previous_harvest_draft()
            db.session.commit()
            self.assertEqual(action, 'updated')
            self.assertEqual(updated.stock, 37)
            self.assertEqual(updated.image, product.image)
            self.assertEqual(updated.images, [product.image])
            self.assertFalse(updated.active)
            self.assertFalse(updated.visible_in_store)

    def test_seed_selects_safe_deterministic_identifiers_after_a_collision(self):
        with self.app.app_context():
            db.session.add(WebProduct(
                name='Existing unrelated product',
                slug=PROPOSED_SLUG,
                sku=PROPOSED_SKU,
                price=1.00,
                category='Packs',
                stock=1,
            ))
            db.session.commit()

            product, action, used_fallback = seed_previous_harvest_draft()
            db.session.commit()

            self.assertEqual(action, 'created')
            self.assertTrue(used_fallback)
            self.assertEqual(product.slug, f'{PROPOSED_SLUG}-borrador-1')
            self.assertEqual(product.sku, f'{PROPOSED_SKU}D1')
            existing = WebProduct.query.filter_by(slug=PROPOSED_SLUG).one()
            self.assertEqual(existing.name, 'Existing unrelated product')


if __name__ == '__main__':
    unittest.main()
