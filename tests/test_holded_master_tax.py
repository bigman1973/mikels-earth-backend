import unittest
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import patch

from flask import Flask

from src.models.admin_user import AdminUser
from src.models.order import Order
from src.models.user import db
from src.routes import admin_panel_routes
from src.services.holded_service import holded_create_invoice
from src.services.holded_tax_service import (
    FiscalValidationError,
    build_holded_master_index,
    prepare_document_items,
    resolve_tax_profile,
)


# Fixtures represent the definitive fiscal recipes. Packaging remains in both
# recipes to preserve stock/cost logic but is intentionally not fiscalised.
PRODUCTS = [
    {"id": "oil5", "sku": "MIKVE5LP", "name": "Aceite 5 L", "taxes": ["s_iva_4"]},
    {"id": "early", "sku": "MIKVET500", "name": "Aceite Temprano 500 ml", "taxes": ["s_iva_4"]},
    {"id": "para", "sku": "MIKPARA450", "name": "Paraguayo", "taxes": ["s_iva_10"]},
    {"id": "nect", "sku": "MIKNECT450", "name": "Nectarina", "taxes": ["s_iva_10"]},
    {"id": "jam", "sku": "MIKPARJ250", "name": "Mermelada", "taxes": ["s_iva_10"]},
    {"id": "mini-ve", "sku": "MIKVE14", "name": "Miniatura VE", "taxes": ["s_iva_4"]},
    {"id": "mini-bio", "sku": "MIKBIO14", "name": "Miniatura BIO", "taxes": ["s_iva_4"]},
    {"id": "kraft", "sku": "MIKESTKRA", "name": "Caja kraft", "taxes": ["s_iva_21"]},
    {
        "id": "ypo",
        "sku": "MIKPACKYPO",
        "name": "Pack Degustación",
        "kind": "pack",
        "taxes": ["s_iva_10"],
        "pack_items": [
            {"product_selection": "jam", "units": 1},
            {"product_selection": "mini-ve", "units": 2},
            {"product_selection": "mini-bio", "units": 2},
            {"product_selection": "kraft", "units": 1},
        ],
    },
    {
        "id": "complete",
        "sku": "MIKPACKF",
        "name": "Pack Completo",
        "kind": "pack",
        "taxes": [],
        "pack_items": [
            {"product_selection": "oil5", "units": 1},
            {"product_selection": "early", "units": 1},
            {"product_selection": "para", "units": 1},
            {"product_selection": "nect", "units": 1},
            {"product_selection": "jam", "units": 1},
            {"product_selection": "mini-ve", "units": 2},
            {"product_selection": "mini-bio", "units": 2},
            {"product_selection": "kraft", "units": 1},
        ],
    },
    # Gift boxes are presentation only: even when a stock pack exists, they
    # emit a single line with the tax of the actual product.
    {
        "id": "early-gift",
        "sku": "MIKVET500R",
        "name": "Temprano con estuche",
        "kind": "pack",
        "taxes": ["s_iva_4"],
        "pack_items": [{"product_selection": "early", "units": 1}, {"product_selection": "kraft", "units": 1}],
    },
    {"id": "bio-gift", "sku": "MIKBIO19R", "name": "Ecológico con estuche", "taxes": ["s_iva_4"]},
    {"id": "retired", "sku": "MIKPAOVE500", "name": "Retirado", "taxes": ["s_iva_21"]},
]

# Current shop PVPs. MIKPARJ250 and miniatures are declared references inside
# the fiscal service because they do not have current standalone web SKUs.
WEB_REFERENCE_PRICES = {
    "MIKVE5LP": Decimal("43.00"),
    "MIKVET500": Decimal("17.15"),
    "MIKPARA450": Decimal("17.15"),
    "MIKNECT450": Decimal("17.15"),
}
TAX_RATE = {"s_iva_4": Decimal("0.04"), "s_iva_10": Decimal("0.10"), "s_iva_21": Decimal("0.21")}


def gross_for(line):
    return (
        Decimal(str(line["subtotal"]))
        * Decimal(str(line["units"]))
        * (Decimal("1") + TAX_RATE[line["tax"]])
    )


def total_gross(lines):
    return sum((gross_for(line) for line in lines), Decimal("0")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


class HoldedMasterTaxTests(unittest.TestCase):
    def test_simple_items_use_their_holded_master_tax_not_their_name(self):
        # Names intentionally point to the wrong tax categories.
        items = prepare_document_items([
            {"sku": "MIKVE5LP", "name": "Conserva con aceite", "price": 10.40, "quantity": 1},
            {"sku": "MIKPARJ250", "name": "Aceite en confitura", "price": 5.50, "quantity": 2},
        ], PRODUCTS, WEB_REFERENCE_PRICES)

        self.assertEqual(items[0]["tax"], "s_iva_4")
        self.assertEqual(items[0]["subtotal"], 10.0)
        self.assertEqual(items[1]["tax"], "s_iva_10")
        self.assertEqual(items[1]["subtotal"], 5.0)

    def test_gift_case_is_not_expanded_and_uses_its_product_tax(self):
        items = prepare_document_items([
            {"sku": "MIKVET500R", "name": "Temprano con caja", "price": 21.90, "quantity": 1},
            {"sku": "MIKBIO19R", "name": "Ecológico con caja", "price": 21.90, "quantity": 1},
        ], PRODUCTS, WEB_REFERENCE_PRICES)

        self.assertEqual([item["sku"] for item in items], ["MIKVET500R", "MIKBIO19R"])
        self.assertEqual([item["tax"] for item in items], ["s_iva_4", "s_iva_4"])
        self.assertEqual(total_gross(items), Decimal("43.80"))

    def test_degustation_expands_using_approved_reference_values_and_excludes_kraft(self):
        items = prepare_document_items([
            {"sku": "MIKPACKYPO", "name": "Pack Degustación", "price": 19.90, "gross_total": 19.90, "quantity": 1},
        ], PRODUCTS, WEB_REFERENCE_PRICES)

        self.assertEqual([item["sku"] for item in items], ["MIKPARJ250", "MIKVE14", "MIKBIO14"])
        self.assertEqual([item["tax"] for item in items], ["s_iva_10", "s_iva_4", "s_iva_4"])
        self.assertEqual([item["units"] for item in items], [1, 2, 2])
        self.assertNotIn("MIKESTKRA", [item["sku"] for item in items])
        # 19.90 € split over 6.50, 2 × 1.00 and 2 × 1.00: 12.32 / 3.79 / 3.79.
        self.assertEqual(total_gross(items), Decimal("19.90"))
        self.assertEqual(gross_for(items[0]).quantize(Decimal("0.01")), Decimal("12.32"))
        self.assertEqual(gross_for(items[1]).quantize(Decimal("0.01")), Decimal("3.79"))
        self.assertEqual(gross_for(items[2]).quantize(Decimal("0.01")), Decimal("3.79"))

    def test_complete_pack_expands_by_component_and_preserves_exact_gross_charge(self):
        # gross_total represents the actual Stripe line total after proportional
        # coupon allocation. It must win over unit price × quantity.
        items = prepare_document_items([
            {"sku": "MIKPACKF", "name": "Pack Completo", "price": 94.20, "gross_total": 188.39, "quantity": 2},
        ], PRODUCTS, WEB_REFERENCE_PRICES)

        self.assertEqual(
            [item["sku"] for item in items],
            ["MIKVE5LP", "MIKVET500", "MIKPARA450", "MIKNECT450", "MIKPARJ250", "MIKVE14", "MIKBIO14"],
        )
        self.assertEqual(
            [item["tax"] for item in items],
            ["s_iva_4", "s_iva_4", "s_iva_10", "s_iva_10", "s_iva_10", "s_iva_4", "s_iva_4"],
        )
        self.assertEqual([item["units"] for item in items], [2, 2, 2, 2, 2, 4, 4])
        self.assertNotIn("MIKESTKRA", [item["sku"] for item in items])
        self.assertEqual(total_gross(items), Decimal("188.39"))

    def test_only_the_two_approved_mixed_packs_have_mixed_panel_status(self):
        index = build_holded_master_index(PRODUCTS)
        self.assertTrue(resolve_tax_profile(index.by_sku["MIKPACKYPO"], index).is_mixed)
        self.assertTrue(resolve_tax_profile(index.by_sku["MIKPACKF"], index).is_mixed)
        self.assertFalse(resolve_tax_profile(index.by_sku["MIKVET500R"], index).is_mixed)

    def test_missing_master_tax_sku_or_recipe_fails_closed(self):
        with self.assertRaisesRegex(FiscalValidationError, "no tiene impuesto"):
            prepare_document_items([
                {"sku": "MIKVE5LP", "name": "Aceite", "price": 10.40, "quantity": 1},
            ], [{"id": "oil", "sku": "MIKVE5LP", "name": "Aceite", "taxes": []}])

        with self.assertRaisesRegex(FiscalValidationError, "no existe"):
            prepare_document_items([
                {"sku": "MISSING", "name": "No existe", "price": 10, "quantity": 1},
            ], PRODUCTS, WEB_REFERENCE_PRICES)

        invalid_recipe = [dict(product) for product in PRODUCTS if product.get("sku") != "MIKPACKYPO"]
        invalid_recipe.append({"id": "ypo", "sku": "MIKPACKYPO", "name": "Pack", "taxes": ["s_iva_10"], "pack_items": []})
        with self.assertRaisesRegex(FiscalValidationError, "no tiene escandallo"):
            prepare_document_items([
                {"sku": "MIKPACKYPO", "name": "Pack", "price": 19.90, "quantity": 1},
            ], invalid_recipe, WEB_REFERENCE_PRICES)

    def test_retired_sku_is_controlled_rejection(self):
        with self.assertRaisesRegex(FiscalValidationError, "retirado"):
            prepare_document_items([
                {"sku": "MIKPAOVE500", "name": "Retirado", "price": 10, "quantity": 1},
            ], PRODUCTS, WEB_REFERENCE_PRICES)

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
