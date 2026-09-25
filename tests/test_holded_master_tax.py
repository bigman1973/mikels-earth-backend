import unittest
from unittest.mock import patch

from flask import Flask

from src.models.admin_user import AdminUser
from src.models.order import Order
from src.models.user import db
from src.routes import admin_panel_routes
from src.services.holded_service import holded_create_invoice
from src.services.holded_tax_service import (
    FiscalValidationError,
    PackAllocationStrategyRequired,
    prepare_document_items,
    resolve_tax_profile,
    build_holded_master_index,
)


PRODUCTS = [
    {"id": "oil", "sku": "MIKVE500", "name": "Aceite", "taxes": [{"tax": "s_iva_4"}]},
    {"id": "jam", "sku": "MIKPARJ250", "name": "Mermelada", "taxes": [{"tax": "s_iva_10"}]},
    {"id": "box", "sku": "MIKESTTEM", "name": "Estuche", "taxes": [{"tax": "s_iva_21"}]},
    {
        "id": "ypo",
        "sku": "MIKPACKYPO",
        "name": "Pack Degustación",
        "kind": "pack",
        "taxes": [{"tax": "s_iva_4"}],
        "pack_items": [
            {"product_selection": "jam", "units": 1},
            {"product_selection": "oil", "units": 2},
            {"product_selection": "box", "units": 1},
        ],
    },
]


class HoldedMasterTaxTests(unittest.TestCase):
    def test_simple_items_use_their_holded_master_tax_not_their_name(self):
        # The product name intentionally contains a misleading conservas word.
        items = prepare_document_items([
            {"sku": "MIKVE500", "name": "Conserva con aceite", "price": 10.40, "quantity": 1},
            {"sku": "MIKPARJ250", "name": "Aceite en confitura", "price": 5.50, "quantity": 2},
        ], PRODUCTS)

        self.assertEqual(items[0]["tax"], "s_iva_4")
        self.assertEqual(items[0]["subtotal"], 10.0)
        self.assertEqual(items[1]["tax"], "s_iva_10")
        self.assertEqual(items[1]["subtotal"], 5.0)

    def test_missing_master_tax_fails_closed(self):
        with self.assertRaisesRegex(FiscalValidationError, "no tiene impuesto"):
            prepare_document_items([
                {"sku": "MIKVE500", "name": "Aceite", "price": 10.40, "quantity": 1},
            ], [{"id": "oil", "sku": "MIKVE500", "name": "Aceite", "taxes": []}])

    def test_mixed_pack_is_explicitly_marked_and_rejected_pending_allocation(self):
        index = build_holded_master_index(PRODUCTS)
        profile = resolve_tax_profile(index.by_sku["MIKPACKYPO"], index)
        self.assertTrue(profile.is_mixed)
        self.assertEqual(profile.tax_ids, ("s_iva_10", "s_iva_4", "s_iva_21"))

        with self.assertRaisesRegex(PackAllocationStrategyRequired, "regla de reparto fiscal"):
            prepare_document_items([
                {"sku": "MIKPACKYPO", "name": "Pack Degustación", "price": 19.90, "quantity": 1},
            ], PRODUCTS)

    @patch("src.services.holded_service.requests.post")
    def test_holded_service_never_posts_a_line_without_explicit_tax(self, post):
        success, message = holded_create_invoice(
            contact_id="contact",
            items=[{"name": "Sin impuesto", "sku": "MIKSINTAX", "subtotal": 10, "units": 1}],
        )
        self.assertFalse(success)
        self.assertIn("no tiene impuesto maestro", message)
        post.assert_not_called()


class HoldedRouteFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()
            db.session.add(AdminUser(
                microsoft_id="admin-test",
                email="admin@farmsplanet.es",
                name="Admin test",
                role="admin",
                is_active=True,
            ))
            db.session.add(Order(
                order_number="MKL-FISCAL-TEST",
                customer_email="cliente@example.com",
                customer_name="Cliente",
                shipping_address="Calle de prueba 1",
                shipping_city="Lleida",
                shipping_postal_code="25001",
                items=[{"sku": "MIKSINTAX", "name": "Producto sin impuesto", "price": 10.0, "quantity": 1}],
                subtotal=10.0,
                total=10.0,
                needs_invoice=False,
            ))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_sales_order_stops_before_contact_or_document_when_tax_is_missing(self):
        # __wrapped__ twice reaches the decorated route body.  This lets the
        # test assert fiscal behaviour without coupling to Entra authentication.
        route_body = admin_panel_routes.create_order_in_holded.__wrapped__.__wrapped__
        with patch.object(admin_panel_routes, "holded_get_products", return_value=[]), \
             patch.object(admin_panel_routes, "holded_get_or_create_contact") as contact, \
             patch.object(admin_panel_routes, "holded_create_sales_order") as create_order, \
             self.app.test_request_context("/orders/1/create-in-holded", method="POST"):
            response, status = route_body(1)

        self.assertEqual(status, 422)
        self.assertEqual(response.get_json()["fiscal_validation"], "failed")
        contact.assert_not_called()
        create_order.assert_not_called()

    def test_invoice_or_ticket_stops_before_contact_or_document_when_tax_is_missing(self):
        route_body = admin_panel_routes.create_invoice_in_holded.__wrapped__.__wrapped__
        with patch.object(admin_panel_routes, "holded_get_products", return_value=[]), \
             patch.object(admin_panel_routes, "holded_get_or_create_contact") as contact, \
             patch.object(admin_panel_routes, "holded_create_invoice") as create_invoice, \
             patch.object(admin_panel_routes, "holded_create_salesreceipt") as create_ticket, \
             self.app.test_request_context("/orders/1/invoice", method="POST"):
            response, status = route_body(1)

        self.assertEqual(status, 422)
        self.assertEqual(response.get_json()["fiscal_validation"], "failed")
        contact.assert_not_called()
        create_invoice.assert_not_called()
        create_ticket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
