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

    def test_receipt_keeps_gross_totals_with_included_vat_label(self):
        order = SimpleNamespace(
            order_number='MKL-TEST-001',
            paid_at=datetime(2026, 10, 2, 8, 15),
            items=[
                {'name': 'Paraguayo en almíbar', 'quantity': 1, 'gross_total': '17.15', 'sku': 'MIKPARA450R'},
                {'name': 'Aceite temprano sin filtrar', 'quantity': 1, 'gross_total': '39.09', 'sku': 'MIKVET500R'},
            ],
            tax_base=53.18,
            tax_total=3.06,
            shipping_cost=0,
            total=56.24,
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
            customer_notes='mañanas',
        )
        receipt = build_receipt_snapshot(order)

        self.assertEqual(receipt['lines'][0]['amount_display'], '17,15 €')
        self.assertEqual(receipt['lines'][1]['amount_display'], '39,09 €')
        self.assertEqual(receipt['totals']['subtotal_display'], '56,24 €')
        self.assertEqual(receipt['totals']['shipping_display'], 'GRATIS')
        self.assertEqual(receipt['totals']['total_display'], '56,24 €')
        self.assertEqual(receipt['totals']['tax_included_label'], 'IVA incluido')
        self.assertIn('+34 600 000 000', receipt['shipping']['phone'])
        self.assertTrue(receipt['billing']['requested'])
        self.assertTrue(receipt['confirmation']['sent'])
        self.assertEqual(receipt['notes'], 'mañanas')

    def test_receipt_refuses_to_render_when_line_sum_and_total_do_not_match(self):
        order = SimpleNamespace(
            order_number='MKL-TEST-MISMATCH',
            paid_at=datetime(2026, 10, 2, 8, 15),
            items=[{'name': 'Paraguayo en almíbar', 'quantity': 2, 'gross_total': '34.30'}],
            shipping_cost=0,
            total=17.15,
            shipping_address='Calle de prueba 1',
            shipping_postal_code='25003',
            shipping_city='Lleida',
            shipping_country='España',
            customer_phone='',
            needs_invoice=False,
            fiscal_name=None,
            fiscal_nif=None,
            fiscal_address=None,
            fiscal_postal_code=None,
            fiscal_city=None,
            customer_email='cliente@example.com',
            email_sent=False,
        )

        with self.assertRaisesRegex(ValueError, r'líneas - descuento \+ envío'):
            build_receipt_snapshot(order)

    def test_receipt_reconciles_persisted_coupon_before_shipping(self):
        order = SimpleNamespace(
            order_number='MKL-TEST-DISCOUNT',
            paid_at=datetime(2026, 10, 2, 8, 15),
            items=[
                {'name': 'Paraguayo en almíbar', 'quantity': 1, 'gross_total': '17.15'},
                {'name': 'Aceite ecológico', 'quantity': 1, 'gross_total': '19.90'},
            ],
            discount_code='BIENVENIDA10',
            discount_amount='3.71',
            shipping_cost=0,
            total='33.34',
            shipping_address='Calle de prueba 1',
            shipping_postal_code='25003',
            shipping_city='Lleida',
            shipping_country='España',
            customer_phone='',
            needs_invoice=False,
            fiscal_name=None,
            fiscal_nif=None,
            fiscal_address=None,
            fiscal_postal_code=None,
            fiscal_city=None,
            customer_email='cliente@example.com',
            email_sent=True,
        )

        receipt = build_receipt_snapshot(order)
        self.assertEqual(receipt['totals']['subtotal_display'], '37,05 €')
        self.assertEqual(receipt['totals']['discount_display'], '3,71 €')
        self.assertEqual(receipt['totals']['discount_label'], 'Descuento (BIENVENIDA10)')
        self.assertEqual(receipt['totals']['total_display'], '33,34 €')

    def test_receipt_reconciles_a_fractional_cent_volume_tier(self):
        order = SimpleNamespace(
            order_number='MKL-TEST-VOLUME-12',
            paid_at=datetime(2026, 10, 2, 8, 15),
            # Stripe allocates the 15% session discount to this line. The
            # receipt keeps the pre-discount line total so its explicit
            # discount row explains 205.80 € -> 174.93 €.
            items=[{
                'name': 'Aceite temprano sin filtrar',
                'quantity': 12,
                'gross_total': '174.93',
                'receipt_line_total': '205.80',
            }],
            discount_code=None,
            discount_amount='30.87',
            shipping_cost=0,
            total='174.93',
            shipping_address='Calle de prueba 1',
            shipping_postal_code='25003',
            shipping_city='Lleida',
            shipping_country='España',
            customer_phone='',
            needs_invoice=False,
            fiscal_name=None,
            fiscal_nif=None,
            fiscal_address=None,
            fiscal_postal_code=None,
            fiscal_city=None,
            customer_email='cliente@example.com',
            email_sent=True,
        )

        receipt = build_receipt_snapshot(order)
        self.assertEqual(receipt['lines'][0]['amount_display'], '205,80 €')
        self.assertEqual(receipt['totals']['subtotal_display'], '205,80 €')
        self.assertEqual(receipt['totals']['discount_display'], '30,87 €')
        self.assertEqual(receipt['totals']['total_display'], '174,93 €')

    def test_reservation_receipt_uses_saved_reservation_steps(self):
        order = SimpleNamespace(
            order_number='MKL-TEST-RESERVA',
            paid_at=datetime(2026, 10, 2, 8, 15),
            items=[{
                'name': 'Aceite temprano sin filtrar',
                'quantity': 12,
                'gross_total': '218.90',
                'receipt_line_total': '238.80',
                'reservation_only': True,
            }],
            discount_code=None,
            discount_amount='19.90',
            shipping_cost=0,
            total='218.90',
            shipping_address='Calle de prueba 1',
            shipping_postal_code='25003',
            shipping_city='Lleida',
            shipping_country='España',
            customer_phone='',
            needs_invoice=False,
            fiscal_name=None,
            fiscal_nif=None,
            fiscal_address=None,
            fiscal_postal_code=None,
            fiscal_city=None,
            customer_email='cliente@example.com',
            email_sent=True,
        )

        receipt = build_receipt_snapshot(order)
        self.assertEqual(receipt['heading'], 'Reserva confirmada')
        self.assertEqual(receipt['totals']['subtotal_display'], '238,80 €')
        self.assertEqual(receipt['totals']['discount_display'], '19,90 €')
        self.assertEqual(receipt['totals']['total_display'], '218,90 €')
        self.assertEqual(receipt['next_steps'], [
            'Guardamos tus botellas.',
            'Los envíos salen la última semana de octubre.',
            'Te escribimos cuando salga el tuyo.',
        ])

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
