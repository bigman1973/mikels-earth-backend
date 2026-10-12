import os
import unittest

from flask import Flask

os.environ.setdefault('JWT_SECRET', 'test-only-verified-review-remap-secret')

from src.models.user import db
from src.models.review import Review
from src.models.web_product import WebProduct
from src.routes.admin_panel_routes import (
    admin_panel_bp,
    get_verified_reviews_for_remap,
    remap_verified_review_products,
)


class VerifiedReviewProductRemapTests(unittest.TestCase):
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
            temprano = WebProduct(
                name='Aceite de Oliva Virgen Extra Temprano 500ml sin filtrar',
                slug='aceite-temprano-sin-filtrar',
                sku='MIKVET500',
                price=19.90,
                category='Aceites',
            )
            garrafa = WebProduct(
                name='Aceite de Oliva Virgen Extra 5L',
                slug='aceite-5l-caja-3',
                sku='MIKVE5LP',
                price=43.00,
                category='Aceites',
            )
            verified = Review(
                customer_email='cliente@example.com',
                customer_name='Cliente real',
                product_slug='aceite-temprano-500ml',
                product_name='Aceite de Oliva Virgen Extra Temprano 500ml',
                rating=5,
                comment='Muy bueno',
                status='approved',
                is_verified_purchase=True,
            )
            unverified = Review(
                customer_email='otro@example.com',
                customer_name='Otro cliente',
                product_slug='aceite-5l',
                product_name='Aceite de Oliva Virgen Extra 5L',
                rating=5,
                comment='No debe modificarse',
                status='approved',
                is_verified_purchase=False,
            )
            db.session.add_all([temprano, garrafa, verified, unverified])
            db.session.commit()
            self.verified_id = verified.id
            self.unverified_id = unverified.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _direct(self, view, path, method='GET', json=None):
        with self.app.test_request_context(path, method=method, json=json):
            return view.__wrapped__.__wrapped__()

    def test_readback_lists_verified_reviews_without_customer_email_or_comment(self):
        response = self._direct(get_verified_reviews_for_remap, '/api/admin/reviews/verified')
        payload = response.get_json()
        self.assertEqual([review['id'] for review in payload['reviews']], [self.verified_id])
        self.assertNotIn('customer_email', payload['reviews'][0])
        self.assertNotIn('comment', payload['reviews'][0])

    def test_remap_changes_only_verified_review_product_fields(self):
        response = self._direct(
            remap_verified_review_products,
            '/api/admin/reviews/remap-products',
            method='PUT',
            json={'mappings': [{
                'review_id': self.verified_id,
                'expected_slug': 'aceite-temprano-500ml',
                'target_slug': 'aceite-temprano-sin-filtrar',
            }]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['changed'], 1)
        self.assertEqual(payload['changes'][0]['before']['product_slug'], 'aceite-temprano-500ml')
        self.assertEqual(payload['changes'][0]['after']['product_slug'], 'aceite-temprano-sin-filtrar')
        self.assertIsNotNone(payload['changes'][0]['after']['updated_at'])
        with self.app.app_context():
            review = db.session.get(Review, self.verified_id)
            self.assertEqual(review.product_name, 'Aceite de Oliva Virgen Extra Temprano 500ml sin filtrar')
            self.assertTrue(review.is_verified_purchase)
            self.assertEqual(review.status, 'approved')
            self.assertEqual(review.rating, 5)
            self.assertEqual(review.comment, 'Muy bueno')

    def test_remap_refuses_stale_or_unverified_reviews_without_partial_change(self):
        response = self._direct(
            remap_verified_review_products,
            '/api/admin/reviews/remap-products',
            method='PUT',
            json={'mappings': [
                {
                    'review_id': self.verified_id,
                    'expected_slug': 'otro-slug',
                    'target_slug': 'aceite-temprano-sin-filtrar',
                },
                {
                    'review_id': self.unverified_id,
                    'expected_slug': 'aceite-5l',
                    'target_slug': 'aceite-5l-caja-3',
                },
            ]},
        )
        body, status_code = response
        self.assertEqual(status_code, 409)
        self.assertEqual(body.get_json()['error'], 'Remapeo rechazado')
        with self.app.app_context():
            self.assertEqual(db.session.get(Review, self.verified_id).product_slug, 'aceite-temprano-500ml')
            self.assertEqual(db.session.get(Review, self.unverified_id).product_slug, 'aceite-5l')

    def test_private_routes_require_active_panel_authentication(self):
        secured = Flask(__name__)
        secured.config.update(TESTING=True)
        secured.register_blueprint(admin_panel_bp, url_prefix='/api/admin')
        self.assertEqual(
            secured.test_client().get('/api/admin/reviews/verified').status_code,
            401,
        )
        self.assertEqual(
            secured.test_client().put('/api/admin/reviews/remap-products', json={'mappings': []}).status_code,
            401,
        )


if __name__ == '__main__':
    unittest.main()
