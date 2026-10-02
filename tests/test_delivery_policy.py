import unittest
from decimal import Decimal

from src.services.delivery_policy import (
    BALEARES_MINIMUM_ORDER,
    DeliveryPolicyError,
    normalized_postal_code,
    validate_delivery_destination,
)


class DeliveryPolicyTests(unittest.TestCase):
    def test_served_destinations_normalize_to_iso_codes(self):
        self.assertEqual(
            validate_delivery_destination(country='España', postal_code='25003', order_total='1.00'),
            'ES',
        )
        self.assertEqual(
            validate_delivery_destination(country='Portugal', postal_code='4000-001', order_total='1.00'),
            'PT',
        )
        self.assertEqual(normalized_postal_code(' 07 001 '), '07001')

    def test_excludes_only_the_four_published_spanish_prefixes(self):
        for postal_code in ('35001', '38001', '51001', '52001'):
            with self.subTest(postal_code=postal_code):
                with self.assertRaisesRegex(DeliveryPolicyError, 'No enviamos') as context:
                    validate_delivery_destination(country='España', postal_code=postal_code, order_total='100.00')
                self.assertEqual(context.exception.code, 'DESTINATION_NOT_SERVED')

    def test_baleares_requires_exact_minimum_after_discounts(self):
        with self.assertRaisesRegex(DeliveryPolicyError, '59,00') as context:
            validate_delivery_destination(country='ES', postal_code='07001', order_total='58.99')
        self.assertEqual(context.exception.code, 'BALEARES_MINIMUM_ORDER')
        self.assertEqual(BALEARES_MINIMUM_ORDER, Decimal('59.00'))
        self.assertEqual(
            validate_delivery_destination(country='ES', postal_code='07001', order_total='59.00'),
            'ES',
        )

    def test_france_is_not_a_served_destination(self):
        with self.assertRaisesRegex(DeliveryPolicyError, 'Solo enviamos') as context:
            validate_delivery_destination(country='Francia', postal_code='75001', order_total='100.00')
        self.assertEqual(context.exception.code, 'DESTINATION_NOT_SERVED')


if __name__ == '__main__':
    unittest.main()
