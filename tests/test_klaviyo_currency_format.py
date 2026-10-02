import unittest
from unittest.mock import patch

from src.services import klaviyo_service


class KlaviyoCurrencyFormatTests(unittest.TestCase):
    def test_customer_facing_euro_format_uses_spanish_decimal_and_symbol(self):
        self.assertEqual(klaviyo_service._format_eur(17.15), '17,15 €')
        self.assertEqual(klaviyo_service._format_eur('1234.5'), '1.234,50 €')
        self.assertEqual(klaviyo_service._format_eur(0), '0,00 €')

    def test_order_items_html_is_customer_facing_euro_format(self):
        html = klaviyo_service._build_items_html([
            {'name': 'Producto de prueba', 'quantity': 1, 'price': 17.15},
        ])
        self.assertEqual(html, 'Producto de prueba x1 — 17,15 €')

    @patch('src.services.klaviyo_service.send_klaviyo_event')
    def test_order_confirmation_event_formats_every_visible_amount(self, send_event):
        send_event.return_value = True
        klaviyo_service.klaviyo_send_order_confirmation({
            'order_number': 'MKL-TEST-001',
            'customer_email': 'cliente@example.com',
            'customer_name': 'Cliente Prueba',
            'items': [{'name': 'Producto de prueba', 'quantity': 1, 'price': 17.15}],
            'subtotal': 17.15,
            'total': 17.15,
            'discount_amount': 1.90,
            'receipt': {
                'paid_at_display': '02/10/2026 08:15',
                'totals': {
                    'subtotal_display': '15,59 €',
                    'shipping_display': 'GRATIS',
                    'tax_display': '1,56 €',
                    'total_display': '17,15 €',
                },
            },
        })
        properties = send_event.call_args.kwargs['properties']
        self.assertEqual(properties['Subtotal'], '15,59 €')
        self.assertEqual(properties['Total'], '17,15 €')
        self.assertEqual(properties['subtotal'], '15,59 €')
        self.assertEqual(properties['total'], '17,15 €')
        self.assertEqual(properties['DiscountAmount'], '1,90 €')
        self.assertEqual(properties['discount_amount'], '1,90 €')
        self.assertIn('17,15 €', properties['ItemsHtml'])
        self.assertEqual(properties['ShippingText'], 'GRATIS')
        self.assertEqual(properties['Tax'], '1,56 €')
        self.assertEqual(properties['Date'], '02/10/2026 08:15')

    @patch('src.services.klaviyo_service.send_klaviyo_event')
    def test_customer_and_internal_order_events_keep_the_saved_receipt(self, send_event):
        receipt = {
            'brand': {'name': "Mikel's Fruit", 'logo_url': 'https://cdn.example/logo.png'},
            'order_number': 'MKL-TEST-204',
            'paid_at_display': '02/10/2026 09:30',
            'lines': [{'name': 'Producto de prueba', 'quantity': 1, 'amount_display': '17,15 €'}],
            'totals': {
                'subtotal_display': '16,49 €',
                'shipping_display': 'GRATIS',
                'tax_display': '0,66 €',
                'total_display': '17,15 €',
            },
            'shipping': {'lines': ['Calle de prueba 1', '25003 Lleida'], 'phone': '+34 621 144 701'},
            'billing': {'requested': True, 'lines': ['Nombre fiscal', 'NIF/CIF: B00000000']},
            'confirmation': {'email': 'cliente@example.com', 'sent': True},
            'next_steps': ['Preparamos tu pedido.'],
        }
        order = {
            'order_number': 'MKL-TEST-204',
            'customer_email': 'cliente@example.com',
            'customer_name': 'Cliente Prueba',
            'customer_phone': '+34 621 144 701',
            'shipping_address': 'Calle de prueba 1',
            'items': [{'name': 'Producto de prueba', 'quantity': 1, 'price': 17.15}],
            'subtotal': 16.49,
            'total': 17.15,
            'receipt': receipt,
        }

        klaviyo_service.klaviyo_send_order_confirmation(order)
        customer_properties = send_event.call_args.kwargs['properties']
        self.assertEqual(customer_properties['Receipt'], receipt)
        self.assertEqual(customer_properties['Total'], receipt['totals']['total_display'])

        klaviyo_service.klaviyo_notify_new_order(order)
        internal_properties = send_event.call_args.kwargs['properties']
        self.assertEqual(internal_properties['Receipt'], receipt)
        self.assertEqual(internal_properties['ShippingText'], receipt['totals']['shipping_display'])

    @patch('src.services.klaviyo_service.send_klaviyo_event')
    def test_cart_event_contains_display_ready_price_properties(self, send_event):
        send_event.return_value = True
        klaviyo_service.klaviyo_track_started_checkout(
            email='cliente@example.com',
            customer_name='Cliente Prueba',
            items=[{
                'name': 'Producto de prueba',
                'image': 'https://www.mikels.es/producto.jpg',
                'price': 17.15,
                'quantity': 1,
                'slug': 'producto-prueba',
            }],
            total=17.15,
            checkout_url='https://www.mikels.es/checkout',
            cart_token='currency-test',
        )
        properties = send_event.call_args.kwargs['properties']
        self.assertEqual(properties['Items'][0]['PriceFormatted'], '17,15 €')
        self.assertEqual(properties['TotalFormatted'], '17,15 €')
        self.assertEqual(properties['total_formatted'], '17,15 €')


if __name__ == '__main__':
    unittest.main()
