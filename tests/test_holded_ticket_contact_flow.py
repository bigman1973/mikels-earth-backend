import os
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from src.models.order import Order
from src.models.user import db
from src.routes import admin_panel_routes
from src.services import holded_service


class HoldedResponse:
    def __init__(self, status_code, text='', payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class TicketContactFlowTests(unittest.TestCase):
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
            db.session.add(Order(
                order_number='MKL-TICKET-80',
                customer_email='cliente@example.com',
                customer_name='Cliente Ticket',
                customer_phone='',
                shipping_address='Calle de prueba 1',
                shipping_city='Lleida',
                shipping_postal_code='25001',
                shipping_country='España',
                items=[{'sku': 'MIKVET500', 'name': 'Aceite Temprano', 'quantity': 1, 'price': 17.15}],
                subtotal=17.15,
                shipping_cost=0,
                total=17.15,
                needs_invoice=False,
            ))
            db.session.commit()

        # The route is wrapped first by role_required and then admin_required.
        self.invoice_handler = admin_panel_routes.create_invoice_in_holded.__wrapped__.__wrapped__

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _invoke(self, order_id=1):
        with self.app.test_request_context(f'/orders/{order_id}/invoice', method='POST'):
            return self.app.make_response(self.invoice_handler(order_id))

    @patch('src.routes.admin_panel_routes.holded_get_or_create_contact_detailed')
    @patch('src.routes.admin_panel_routes.holded_create_salesreceipt')
    @patch('src.routes.admin_panel_routes._order_items_for_holded')
    def test_ticket_never_resolves_or_creates_contact(
        self, order_items, create_ticket, get_or_create_contact
    ):
        order_items.return_value = [{
            'name': 'Aceite Temprano', 'units': 1, 'subtotal': 16.49,
            'tax': 's_iva_4', 'sku': 'MIKVET500'
        }]
        create_ticket.return_value = (True, {
            'id': 'receipt-80', 'document_number': 'T2600080'
        })

        response = self._invoke()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['doc_number'], 'T2600080')
        get_or_create_contact.assert_not_called()
        create_ticket.assert_called_once_with(
            items=order_items.return_value,
            notes='Ticket pedido web #MKL-TICKET-80'
        )

    @patch('src.routes.admin_panel_routes.holded_get_or_create_contact_detailed')
    @patch('src.routes.admin_panel_routes.holded_create_salesreceipt')
    @patch('src.routes.admin_panel_routes._order_items_for_holded')
    def test_ticket_failure_preserves_literal_holded_response(
        self, order_items, create_ticket, get_or_create_contact
    ):
        order_items.return_value = [{
            'name': 'Aceite Temprano', 'units': 1, 'subtotal': 16.49,
            'tax': 's_iva_4', 'sku': 'MIKVET500'
        }]
        holded_error = {
            'stage': 'creación de ticket',
            'http_status': 422,
            'literal_response': '{"message":"Tax key not configured"}'
        }
        create_ticket.return_value = (False, holded_error)

        response = self._invoke()

        self.assertEqual(response.status_code, 502)
        payload = response.get_json()
        self.assertEqual(payload['holded_error'], holded_error)
        self.assertIn('HTTP 422', payload['error'])
        self.assertIn('Tax key not configured', payload['error'])
        get_or_create_contact.assert_not_called()

    @patch('src.routes.admin_panel_routes.holded_get_or_create_contact_detailed')
    @patch('src.routes.admin_panel_routes.holded_create_invoice')
    @patch('src.routes.admin_panel_routes._order_items_for_holded')
    def test_complete_invoice_keeps_contact_and_exposes_its_error(
        self, order_items, create_invoice, get_or_create_contact
    ):
        with self.app.app_context():
            order = Order.query.get(1)
            order.needs_invoice = True
            order.fiscal_name = 'Empresa Cliente SL'
            order.fiscal_nif = 'B12345678'
            db.session.commit()
        order_items.return_value = [{
            'name': 'Aceite Temprano', 'units': 1, 'subtotal': 16.49,
            'tax': 's_iva_4', 'sku': 'MIKVET500'
        }]
        holded_error = {
            'stage': 'creación de contacto',
            'http_status': 400,
            'literal_response': '{"info":"Email inválido"}'
        }
        get_or_create_contact.return_value = (None, holded_error)

        response = self._invoke()

        self.assertEqual(response.status_code, 502)
        payload = response.get_json()
        self.assertEqual(payload['holded_error'], holded_error)
        self.assertIn('Email inválido', payload['error'])
        create_invoice.assert_not_called()


class HoldedV2TicketServiceTests(unittest.TestCase):
    @patch.dict(os.environ, {'HOLDED_V2_API_TOKEN': 'test-v2-token'}, clear=False)
    @patch('src.services.holded_service.requests.get')
    @patch('src.services.holded_service.requests.post')
    def test_v2_ticket_has_no_contact_and_is_approved(self, post, get):
        post.side_effect = [
            HoldedResponse(201, '{"id":"receipt-80"}', {'id': 'receipt-80'}),
            HoldedResponse(200, '{}', {}),
        ]
        get.return_value = HoldedResponse(200, '{"document_number":"T2600080"}', {
            'id': 'receipt-80', 'document_number': 'T2600080', 'draft': False
        })

        success, result = holded_service.holded_create_salesreceipt(
            items=[{
            'name': 'Aceite Temprano', 'description': '500 ml',
            'units': 1, 'subtotal': 16.49, 'tax': 's_iva_4', 'sku': 'MIKVET500',
            'product_id': 'early-master'
            }],
            notes='Ticket pedido web #MKL-TICKET-80'
        )

        self.assertTrue(success)
        self.assertEqual(result['id'], 'receipt-80')
        self.assertEqual(result['document_number'], 'T2600080')
        create_call = post.call_args_list[0]
        self.assertEqual(create_call.args[0], 'https://api.holded.com/api/v2/sales-receipts')
        self.assertNotIn('contact_id', create_call.kwargs['json'])
        self.assertEqual(create_call.kwargs['json']['items'][0], {
            'type': 'product',
            'name': 'Aceite Temprano',
            'description': '500 ml',
            'product_id': 'early-master',
            'units': 1,
            'price': 16.49,
            'taxes': ['s_iva_4'],
            'sku': 'MIKVET500'
        })
        self.assertEqual(
            post.call_args_list[1].args[0],
            'https://api.holded.com/api/v2/sales-receipts/receipt-80/approve'
        )

    @patch.dict(os.environ, {'HOLDED_V2_API_TOKEN': 'test-v2-token'}, clear=False)
    @patch('src.services.holded_service.requests.post')
    def test_ticket_refuses_unlinked_line_before_any_holded_post(self, post):
        success, result = holded_service.holded_create_salesreceipt(
            items=[{
                'name': 'Aceite sin enlace', 'units': 1, 'subtotal': 16.49,
                'tax': 's_iva_4', 'sku': 'MIKVET500'
            }],
            notes='Ticket de prueba'
        )

        self.assertFalse(success)
        self.assertIn('identificador maestro de Holded', result['literal_response'])
        post.assert_not_called()

    @patch('src.services.holded_service.requests.post')
    def test_detailed_contact_error_preserves_http_and_body(self, post):
        with patch('src.services.holded_service.requests.get') as get:
            get.return_value = HoldedResponse(200, '[]', [])
            post.return_value = HoldedResponse(
                422, '{"message":"Duplicate or invalid email"}'
            )
            contact_id, error = holded_service.holded_get_or_create_contact_detailed(
                email='invalid@example.com',
                name='Cliente Factura',
                phone='',
                address_data={'address': 'Calle 1', 'city': 'Lleida', 'postal_code': '25001'},
                vatnumber='B12345678'
            )

        self.assertIsNone(contact_id)
        self.assertEqual(error['stage'], 'creación de contacto')
        self.assertEqual(error['http_status'], 422)
        self.assertEqual(error['literal_response'], '{"message":"Duplicate or invalid email"}')


if __name__ == '__main__':
    unittest.main()
