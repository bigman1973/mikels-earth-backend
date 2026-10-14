import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from src.services.scheduled_pack_pricing import (
    active_catalogue_price,
    fixed_pack_quantity_options,
    fixed_pack_tier_for_quantity,
    resolve_fixed_pack_price,
    serialize_tiered_pricing,
)


ENDS_AT = '2026-10-26T22:59:59Z'
TIERS = [
    {'actualQuantity': 1, 'label': '1 botella 500 ml', 'reservationPrice': 17.50, 'regularPrice': 19.90, 'priceEndsAt': ENDS_AT},
    {'actualQuantity': 3, 'label': 'Pack de 3', 'reservationPrice': 49.90, 'regularPrice': 56.70, 'priceEndsAt': ENDS_AT, 'featured': True},
    {'actualQuantity': 6, 'label': 'Pack de 6', 'reservationPrice': 94.50, 'regularPrice': 107.40, 'priceEndsAt': ENDS_AT},
    {'actualQuantity': 12, 'label': 'Caja de 12', 'reservationPrice': 178.50, 'regularPrice': 202.80, 'priceEndsAt': ENDS_AT},
]


class ScheduledPackPricingTests(unittest.TestCase):
    def setUp(self):
        self.product = SimpleNamespace(price=19.90, tiered_discount=TIERS)

    def test_reservation_prices_are_active_through_26_october_madrid_deadline(self):
        now = datetime(2026, 10, 26, 22, 59, 59, tzinfo=timezone.utc)
        self.assertEqual(active_catalogue_price(self.product, now=now), Decimal('17.50'))
        self.assertEqual(
            resolve_fixed_pack_price(fixed_pack_tier_for_quantity(self.product, 12), now=now),
            Decimal('178.50'),
        )

    def test_regular_prices_activate_automatically_on_27_october_madrid(self):
        now = datetime(2026, 10, 26, 23, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(active_catalogue_price(self.product, now=now), Decimal('19.90'))
        self.assertEqual(
            resolve_fixed_pack_price(fixed_pack_tier_for_quantity(self.product, 3), now=now),
            Decimal('56.70'),
        )

    def test_fixed_pack_options_are_exact_and_monotonic_by_unit_price(self):
        self.assertEqual(fixed_pack_quantity_options(self.product), [1, 3, 6, 12])
        prices = [Decimal(str(tier['reservationPrice'])) / Decimal(str(tier['actualQuantity'])) for tier in TIERS]
        self.assertEqual(prices, sorted(prices, reverse=True))

    def test_pack_of_three_is_the_only_recommended_option(self):
        featured = [tier['actualQuantity'] for tier in TIERS if tier.get('featured')]
        self.assertEqual(featured, [3])

    def test_public_tiers_expose_active_price_and_reference_price(self):
        rows = serialize_tiered_pricing(
            self.product,
            now=datetime(2026, 10, 14, tzinfo=timezone.utc),
        )
        self.assertEqual([row['price'] for row in rows], [17.5, 49.9, 94.5, 178.5])
        self.assertEqual([row['regularPrice'] for row in rows], [19.9, 56.7, 107.4, 202.8])
        self.assertTrue(all(row['fixedPack'] for row in rows))


if __name__ == '__main__':
    unittest.main()
