import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from src.services.order_receipt import build_receipt_snapshot, format_eur
from src.services.order_tax_snapshot import calculate_tax_totals_from_snapshot


class OrderReceiptSnapshotTests(unittest.TestCase):
    def test_spanish_money_format_is_saved_for_receipt_rendering(self):
        self.assertEqual(format_eur('17.15'), '17,15 €')
        self.assertEqual(format_eur('1000'), '1.000,00 €')

    def test_receipt_uses_persisted_amounts_and_complete_delivery_details(self):
        order = SimpleNamespace(
            order_number='MKL-TEST-001',
            paid_at=datetime(2026, 10, 2, 8, 15),
            items=[{'name': 'Paraguayo en almíbar', 'quantity': 1, 'gross_total': '17.15'}],
            tax_base=15.59,
            tax_total=1.56,
            shipping_cost=0,
            total=17.15,
            shipping_address='C/ Ejemplo 1, 2º A',
            shipping_postal_code='25003',
            shipping_city='Lleida',
            shipping_country='España',
            customer_phone='+34 600 000 000',
            needs_invoice=True,
            fiscal_name='Cliente de prueba SL',
            fiscal_nif='B00000000',
            fiscal_address='C/ Fiscal 2',
            fiscal_postal_code='25001',
            fiscal_city='Lleida',
            customer_email='cliente@example.test',
            email_sent=True,
        )
        receipt = build_receipt_snapshot(order)

        self.assertEqual(receipt['lines'][0]['amount_display'], '17,15 €')
        self.assertEqual(receipt['totals']['subtotal_display'], '15,59 €')
        self.assertEqual(receipt['totals']['shipping_display'], 'GRATIS')
        self.assertEqual(receipt['totals']['tax_display'], '1,56 €')
        self.assertEqual(receipt['totals']['total_display'], '17,15 €')
        self.assertIn('+34 600 000 000', receipt['shipping']['phone'])
        self.assertTrue(receipt['billing']['requested'])
        self.assertTrue(receipt['confirmation']['sent'])

    def test_tax_snapshot_applies_single_holded_rate_to_charged_gross_total(self):
        base, tax = calculate_tax_totals_from_snapshot(
            [{'sku': 'MIKPARA450R', 'gross_total': '17.15'}],
            {'MIKPARA450R': {'kind': 'single', 'rate': '0.10'}},
        )
        self.assertEqual(str(base), '15.59')
        self.assertEqual(str(tax), '1.56')

    def test_tax_snapshot_applies_mixed_pack_rates_without_name_inference(self):
        base, tax = calculate_tax_totals_from_snapshot(
            [{'sku': 'MIKPACKF', 'gross_total': '19.90'}],
            {'MIKPACKF': {
                'kind': 'mixed',
                'components': [
                    {'rate': '0.04', 'weight': '10.00'},
                    {'rate': '0.10', 'weight': '10.00'},
                ],
            }},
        )
        self.assertEqual(str(base), '18.61')
        self.assertEqual(str(tax), '1.29')
        self.assertEqual(base + tax, Decimal('19.90'))


if __name__ == '__main__':
    unittest.main()
