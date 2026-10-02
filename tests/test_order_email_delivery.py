import json
import os
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from src.services import email_dispatcher, klaviyo_service


ORDER = {
    'order_number': 'MKL-TEST-DELIVERY',
    'customer_email': 'cliente@example.com',
    'customer_name': 'Cliente de prueba',
    'customer_phone': '+34600000000',
    'items': [{'name': 'Producto de prueba', 'quantity': 1, 'price': 17.15}],
    'subtotal': Decimal('17.15'),
    'total': Decimal('17.15'),
    'shipping_address': 'Calle de prueba 1',
    'receipt': {
        'order_number': 'MKL-TEST-DELIVERY',
        'paid_at_display': '02/10/2026 09:00',
        'lines': [{'name': 'Producto de prueba', 'quantity': 1, 'amount': 17.15, 'amount_display': '17,15 €'}],
        'totals': {
            'subtotal': 17.15,
            'subtotal_display': '17,15 €',
            'discount': 0.0,
            'discount_display': '0,00 €',
            'shipping': 0.0,
            'shipping_display': 'GRATIS',
            'total': 17.15,
            'total_display': '17,15 €',
            'tax_included_label': 'IVA incluido',
        },
    },
}


class CanonicalOrderEmailDeliveryTests(unittest.TestCase):
    @patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key', 'BREVO_API_KEY': 'legacy-test-key'}, clear=False)
    @patch('src.services.email_service.send_customer_order_confirmation')
    @patch('src.services.klaviyo_service.klaviyo_send_order_confirmation')
    def test_customer_order_never_falls_back_to_legacy_brevo(self, klaviyo_confirmation, brevo_confirmation):
        klaviyo_confirmation.return_value = False

        self.assertFalse(email_dispatcher.dispatch_order_confirmation(ORDER))
        klaviyo_confirmation.assert_called_once_with(ORDER, return_result=False)
        brevo_confirmation.assert_not_called()

    @patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key', 'BREVO_API_KEY': 'legacy-test-key'}, clear=False)
    @patch('src.services.email_service.notify_new_order_email')
    @patch('src.services.klaviyo_service.klaviyo_notify_new_order')
    def test_internal_order_never_falls_back_to_legacy_brevo(self, klaviyo_notification, brevo_notification):
        klaviyo_notification.return_value = False

        self.assertFalse(email_dispatcher.dispatch_order_notification(ORDER))
        klaviyo_notification.assert_called_once_with(ORDER, return_result=False)
        brevo_notification.assert_not_called()

    @patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key'}, clear=False)
    @patch('src.services.klaviyo_service.requests.post')
    def test_decimal_order_totals_are_serialized_before_klaviyo_send(self, post):
        post.return_value = Mock(status_code=202, text='accepted')

        self.assertTrue(klaviyo_service.klaviyo_send_order_confirmation(ORDER))
        payload = post.call_args.kwargs['json']
        # Raises if a Decimal remains anywhere in the outbound JSON body.
        json.dumps(payload)
        self.assertEqual(payload['data']['attributes']['value'], 17.15)
        self.assertEqual(
            payload['data']['attributes']['properties']['Receipt']['totals']['total_display'],
            '17,15 €',
        )

    @patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key'}, clear=False)
    @patch('src.services.klaviyo_service.requests.post')
    def test_decimal_internal_order_total_is_serialized_before_klaviyo_send(self, post):
        post.return_value = Mock(status_code=202, text='accepted')

        self.assertTrue(klaviyo_service.klaviyo_notify_new_order(ORDER))
        payload = post.call_args.kwargs['json']
        json.dumps(payload)
        self.assertEqual(payload['data']['attributes']['value'], 17.15)
        self.assertEqual(
            payload['data']['attributes']['properties']['Receipt']['totals']['shipping_display'],
            'GRATIS',
        )

    @patch.dict(os.environ, {'KLAVIYO_API_KEY': 'unit-test-key'}, clear=False)
    @patch('src.services.klaviyo_service.requests.post')
    def test_klaviyo_failure_reason_is_available_to_the_order_alarm(self, post):
        post.return_value = Mock(status_code=400, text='invalid event payload')

        accepted, error = klaviyo_service.klaviyo_send_order_confirmation(ORDER, return_result=True)

        self.assertFalse(accepted)
        self.assertIn('Klaviyo HTTP 400', error)
        self.assertIn('invalid event payload', error)

    @patch.dict(os.environ, {'BREVO_API_KEY': 'alert-test-key', 'OWNER_EMAIL': 'info@mikels.es'}, clear=False)
    @patch('src.services.email_service.requests.post')
    def test_order_alarm_is_plain_text_and_never_a_customer_template(self, post):
        from src.services.email_service import send_order_delivery_alert

        post.return_value = Mock(status_code=201, text='created')
        self.assertTrue(send_order_delivery_alert(ORDER, {
            'Confirmación al cliente': 'Klaviyo HTTP 500: unavailable',
        }))

        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['to'][0]['email'], 'info@mikels.es')
        self.assertIn('MKL-TEST-DELIVERY', payload['subject'])
        self.assertIn('Klaviyo HTTP 500', payload['textContent'])
        self.assertNotIn('htmlContent', payload)
        self.assertNotIn('cliente@example.com', str(payload['to']))


if __name__ == '__main__':
    unittest.main()
