import csv
import io
import unittest
from unittest.mock import patch

from flask import Flask
from PIL import Image

from src.models.user import db
from src.models.web_product import WebProduct
from src.routes.meta_catalog_routes import meta_catalog_bp


class _ImageResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def _png_bytes():
    image = Image.new('RGB', (640, 640), color=(125, 29, 45))
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


class MetaCatalogFeedTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(meta_catalog_bp, url_prefix='/api')

        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                WebProduct(
                    name='Paraguayo en Almíbar',
                    slug='paraguayo-almibar',
                    sku='MIKPARA450',
                    description='Paraguayo pelado a mano.',
                    price=17.15,
                    category='Conservas',
                    stock=10,
                    image='/images/paraguayo-principal.webp',
                    active=True,
                    visible_in_store=True,
                ),
                WebProduct(
                    name='Producto oculto',
                    slug='producto-oculto',
                    sku='HIDDEN-001',
                    description='No debe entrar en el feed.',
                    price=10,
                    category='Packs',
                    stock=5,
                    image='/images/hidden.jpg',
                    active=True,
                    visible_in_store=False,
                ),
                WebProduct(
                    name='Producto sin SKU',
                    slug='producto-sin-sku',
                    sku=None,
                    description='No puede tener identidad de catálogo.',
                    price=10,
                    category='Packs',
                    stock=5,
                    image='/images/no-sku.jpg',
                    active=True,
                    visible_in_store=True,
                ),
            ])
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_feed_contains_only_public_sellable_products_with_sku_identity(self):
        response = self.client.get('/api/meta-catalog/products.csv')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response.content_type)
        rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['id'], 'MIKPARA450')
        self.assertEqual(row['price'], '17.15 EUR')
        self.assertEqual(row['availability'], 'in stock')
        self.assertEqual(row['link'], 'https://www.mikels.es/producto/paraguayo-almibar')
        self.assertEqual(row['image_link'], 'https://api.mikels.es/api/meta-catalog/images/MIKPARA450.jpg')
        self.assertEqual(row['brand'], "Mikel's Fruit")

    @patch('src.routes.meta_catalog_routes.requests.get')
    def test_feed_image_reencodes_the_existing_source_as_jpeg(self, get):
        get.return_value = _ImageResponse(_png_bytes())
        response = self.client.get('/api/meta-catalog/images/MIKPARA450.jpg')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, 'image/jpeg')
        self.assertTrue(response.headers['Cache-Control'].startswith('public'))
        with Image.open(io.BytesIO(response.data)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (640, 640))

    def test_unknown_or_hidden_products_have_no_image_endpoint(self):
        self.assertEqual(self.client.get('/api/meta-catalog/images/HIDDEN-001.jpg').status_code, 404)
        self.assertEqual(self.client.get('/api/meta-catalog/images/UNKNOWN.jpg').status_code, 404)


if __name__ == '__main__':
    unittest.main()
