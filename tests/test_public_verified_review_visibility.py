import os
import unittest

from flask import Flask

os.environ.setdefault('ADMIN_SECRET_KEY', 'test-only-public-reviews-secret')

from src.models.user import db
from src.models.review import Review
from src.routes.review_routes import review_bp


class PublicVerifiedReviewVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(review_bp, url_prefix='/api/reviews')

        with self.app.app_context():
            db.create_all()
            verified_temprano = Review(
                customer_email='temprano@example.com',
                customer_name='Compra Temprano',
                product_slug='aceite-temprano-sin-filtrar',
                product_name='Temprano',
                rating=5,
                comment='Compra real de temprano',
                status='approved',
                is_verified_purchase=True,
            )
            verified_garrafa = Review(
                customer_email='garrafa@example.com',
                customer_name='Compra Garrafa',
                product_slug='aceite-5l-caja-3',
                product_name='Garrafa',
                rating=3,
                comment='Compra real de garrafa',
                status='approved',
                is_verified_purchase=True,
            )
            unverified_legacy = Review(
                customer_email='legacy@example.com',
                customer_name='No verificada',
                product_slug='aceite-temprano-500ml',
                product_name='Temprano histórico',
                rating=5,
                comment='Debe quedar fuera de lo público',
                status='approved',
                is_verified_purchase=False,
            )
            rejected_verified = Review(
                customer_email='rejected@example.com',
                customer_name='Rechazada',
                product_slug='aceite-temprano-sin-filtrar',
                product_name='Temprano',
                rating=4,
                comment='No publicada por estado',
                status='rejected',
                is_verified_purchase=True,
            )
            db.session.add_all([
                verified_temprano,
                verified_garrafa,
                unverified_legacy,
                rejected_verified,
            ])
            db.session.commit()
            self.verified_ids = {verified_temprano.id, verified_garrafa.id}
            self.unverified_legacy_id = unverified_legacy.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_public_list_hides_unverified_reviews_including_legacy_slugs(self):
        client = self.app.test_client()

        response = client.get('/api/reviews?limit=100')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['total'], 2)
        self.assertEqual({review['id'] for review in payload['reviews']}, self.verified_ids)
        self.assertTrue(all(review['is_verified_purchase'] for review in payload['reviews']))

        legacy_response = client.get('/api/reviews?product_slug=aceite-temprano-500ml')
        self.assertEqual(legacy_response.status_code, 200)
        self.assertEqual(legacy_response.get_json()['total'], 0)
        self.assertEqual(legacy_response.get_json()['reviews'], [])

        with self.app.app_context():
            historical_review = db.session.get(Review, self.unverified_legacy_id)
            self.assertIsNotNone(historical_review)
            self.assertFalse(historical_review.is_verified_purchase)
            self.assertEqual(historical_review.status, 'approved')
            self.assertEqual(historical_review.product_slug, 'aceite-temprano-500ml')

    def test_public_stats_use_only_verified_approved_reviews(self):
        response = self.app.test_client().get('/api/reviews/stats')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'total_reviews': 2,
            'average_rating': 4.0,
            'rating_distribution': {'1': 0, '2': 0, '3': 1, '4': 0, '5': 1},
        })

    def test_featured_reviews_never_expose_unverified_reviews(self):
        response = self.app.test_client().get('/api/reviews/featured?limit=20')

        self.assertEqual(response.status_code, 200)
        reviews = response.get_json()['reviews']
        self.assertEqual(len(reviews), 1)
        self.assertIn(reviews[0]['id'], self.verified_ids)
        self.assertNotEqual(reviews[0]['id'], self.unverified_legacy_id)
        self.assertTrue(reviews[0]['is_verified_purchase'])


if __name__ == '__main__':
    unittest.main()
