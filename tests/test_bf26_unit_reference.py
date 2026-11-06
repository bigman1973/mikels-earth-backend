"""Display references only; disposable SQLite, no real catalogue or network."""
import json
import os
from pathlib import Path
import unittest
from decimal import Decimal
from flask import Flask
from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.product_routes import product_bp
from src.services.campaign_packs import CAMPAIGN_PACKS, protect_campaign_pack_public_properties

APPROVED = (
    ('BF26-TEMP6', '9.95', '19.90', 50, 'botella', '59.70'),
    ('BF26-TEMP12', '9.95', '19.90', 50, 'botella', '119.40'),
    ('BF26-ECO6', '11.90', '19.90', 40, 'botella', '71.40'),
    ('BF26-PARA3', '10.30', '17.15', 40, 'tarro', '30.90'),
    ('BF26-PARA6', '10.28', '17.15', 40, 'tarro', '61.70'),
    ('BF26-5L3', '32.25', '43.00', 25, 'garrafa', '96.75'),
)

class BF26UnitReferenceTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(product_bp, url_prefix='/api')
        with self.app.app_context():
            db.create_all()
            for p in CAMPAIGN_PACKS:
                # Synthetic unavailable preview; production state is never read.
                image = '/images/paraguayo-principal.webp' if 'PARA' in p.sku else '/images/aceite-5l-3-garrafas.jpg' if '5L' in p.sku else '/images/aceite-temprano-principal.jpeg' if 'TEMP' in p.sku else '/images/aceite-ecologico-principal.jpg'
                db.session.add(WebProduct(name=p.name, sku=p.sku, slug=p.slug, price=float(p.price), category='Packs', image=image, active=False, visible_in_store=False, stock=0, in_promotion=False, original_price=999))
            db.session.add(WebProduct(name='Temprano 2026/27', sku='MIKVET500', slug='aceite-temprano-sin-filtrar', price=19.90, category='Aceites', active=True, visible_in_store=True, stock=0, original_price=999))
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_exact_unit_metadata_master_prices_and_no_mutation(self):
        with self.app.app_context():
            for sku, unit, usual, percent, label, price in APPROVED:
                product = WebProduct.query.filter_by(sku=sku).one()
                before = (product.price, product.original_price, product.active, product.visible_in_store, product.in_promotion, product.stock)
                for lang in ('es', 'en'):
                    data = product.to_frontend_dict(lang)
                    self.assertEqual(data['pricingData'], {'campaign': 'BF26', 'basis': 'unit', 'unitPrice': unit, 'usualUnitPrice': usual, 'unitLabel': label, 'discountPercent': percent, 'referenceSource': 'Jordi · orden 05-11-2026'})
                    self.assertEqual(Decimal(str(data['price'])), Decimal(price))
                    self.assertNotIn('originalPrice', data)
                    self.assertNotIn('stock', data)
                    self.assertFalse(data['available'])
                    self.assertFalse(data['visibleInStore'])
                    self.assertFalse(data['inPromotion'])
                self.assertEqual(before, (product.price, product.original_price, product.active, product.visible_in_store, product.in_promotion, product.stock))
                self.assertNotIn(product, db.session.dirty)
            para = WebProduct.query.filter_by(sku='BF26-PARA6').one().to_frontend_dict()
            self.assertNotEqual(Decimal(para['pricingData']['unitPrice']) * 6, Decimal(str(para['price'])))

    def test_mismatched_master_omits_reference_and_never_changes_price(self):
        with self.app.app_context():
            product = WebProduct.query.filter_by(sku='BF26-PARA6').one()
            product.price = 61.71  # disposable fixture only
            data = protect_campaign_pack_public_properties(product, {'pricingData': {'stale': True}})
            self.assertEqual(data['price'], 61.71)
            self.assertNotIn('pricingData', data)

    def test_current_harvest_excluded_and_inactive_hidden_access_unchanged(self):
        current = self.client.get('/api/products/aceite-temprano-sin-filtrar').get_json()
        self.assertEqual(current['price'], 19.90)
        for key in ('pricingData', 'originalPrice', 'tieredDiscount', 'volumeDiscount'):
            self.assertNotIn(key, current)
        listed = self.client.get('/api/products').get_json()['products']
        self.assertEqual([p['sku'] for p in listed], ['MIKVET500'])
        for pack in CAMPAIGN_PACKS:
            self.assertEqual(self.client.get('/api/products/' + pack.slug).status_code, 404)

    def test_emit_authentic_local_flask_responses_for_react_preview(self):
        inactive_statuses = [self.client.get('/api/products/' + p.slug).status_code for p in CAMPAIGN_PACKS]
        # ONLY ephemeral fixture masters are made resolvable to exercise the
        # real HTTP handler. They remain hidden and unavailable; no sale path.
        with self.app.app_context():
            for p in WebProduct.query.filter(WebProduct.sku.in_([p.sku for p in CAMPAIGN_PACKS])).all():
                p.active = True
            db.session.commit()
        details = []
        for pack in CAMPAIGN_PACKS:
            response = self.client.get('/api/products/' + pack.slug)
            self.assertEqual(response.status_code, 200)
            details.append(response.get_json())
        current = self.client.get('/api/products/aceite-temprano-sin-filtrar').get_json()
        listed = self.client.get('/api/products').get_json()
        self.assertEqual([p['sku'] for p in listed['products']], ['MIKVET500'])
        fixture = {'provenance': 'Real Flask /api/products/<slug> responses from disposable SQLite only; not production.', 'preview_only': True, 'inactive_pack_http_statuses': inactive_statuses, 'products': details, 'current_harvest': current, 'listing_response': listed}
        output = os.environ.get('BF26_REFERENCE_FIXTURE_OUTPUT')
        if output:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + '\n')
