import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.pricing_service import (
    PricingError,
    normalize_quantity,
    price_one_time_item,
    price_subscription_item,
)


class FakeProduct(SimpleNamespace):
    def to_frontend_dict(self, lang='es'):
        name = self.name_en if lang == 'en' and self.name_en else self.name
        return {'name': name}


def make_product(**overrides):
    values = {
        'id': 10,
        'sku': 'ACEITE-5L',
        'slug': 'aceite-5l',
        'name': 'Aceite 5L',
        'name_en': 'Olive Oil 5L',
        'price': 43.0,
        'weight': '5 L',
        'image': '/images/aceite-5l.jpg',
        'tiered_discount': None,
        'volume_discount': None,
        'subscription_available': True,
        'subscription_frequencies': [
            {'value': 'monthly', 'label': 'Mensual', 'discount': 10}
        ],
    }
    values.update(overrides)
    return FakeProduct(**values)


class PricingServiceTests(unittest.TestCase):
    def test_client_price_is_ignored(self):
        product = make_product(price=43.0)
        stale_item = {
            'id': product.id,
            'slug': product.slug,
            'price': 20.0,
            'finalPrice': 20.0,
            'quantity': 1,
        }
        with patch('src.services.pricing_service.resolve_product', return_value=product):
            result = price_one_time_item(stale_item, 'es')
        self.assertEqual(result['unit_amount'], 4300)
        self.assertEqual(result['line_total'], 4300)

    def test_volume_discount_is_recalculated_from_product(self):
        product = make_product(
            price=43.0,
            volume_discount={'minQuantity': 3, 'discount': 9.09},
        )
        with patch('src.services.pricing_service.resolve_product', return_value=product):
            result = price_one_time_item({'id': 10, 'price': 1, 'quantity': 3}, 'es')
        self.assertEqual(result['unit_amount'], 3909)
        self.assertEqual(result['line_total'], 11727)

    def test_tiered_discount_takes_precedence(self):
        product = make_product(
            price=20.0,
            volume_discount={'minQuantity': 2, 'discount': 5},
            tiered_discount=[
                {'minQuantity': 2, 'discount': 10},
                {'minQuantity': 4, 'discount': 20},
            ],
        )
        with patch('src.services.pricing_service.resolve_product', return_value=product):
            result = price_one_time_item({'id': 10, 'quantity': 4}, 'es')
        self.assertEqual(result['unit_amount'], 1600)
        self.assertEqual(result['line_total'], 6400)

    def test_subscription_price_and_name_are_server_side(self):
        product = make_product(price=43.0)
        stale_item = {
            'id': 10,
            'name': 'Cached name',
            'price': 1,
            'quantity': 1,
            'subscription_frequency': 'monthly',
        }
        with patch('src.services.pricing_service.resolve_product', return_value=product):
            result = price_subscription_item(stale_item, 'en')
        self.assertEqual(result['unit_amount'], 3870)
        self.assertEqual(result['name'], 'Olive Oil 5L')

    def test_invalid_quantity_is_rejected(self):
        with self.assertRaises(PricingError) as context:
            normalize_quantity(0)
        self.assertEqual(context.exception.code, 'INVALID_QUANTITY')


if __name__ == '__main__':
    unittest.main()
