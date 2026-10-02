import os
import unittest
from pathlib import Path

from flask import Flask

os.environ.setdefault('JWT_SECRET', 'test-only-blog-metadata-secret')

from src.models.user import db
from src.models.blog import BlogPost
from src.routes.blog_routes import admin_update_post, blog_bp


class BlogMetadataUpdateTests(unittest.TestCase):
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
            post = BlogPost(
                title="¿Qué es un Establecimiento Saludable Mikel's Earth?",
                slug='que-es-un-establecimiento-saludable-mikel-s-earth',
                content='<p>Contenido histórico que no debe cambiar.</p>',
                excerpt='Texto antiguo.',
                status='published',
            )
            db.session.add(post)
            db.session.commit()
            self.post_id = post.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_metadata_update_preserves_existing_slug_and_body(self):
        with self.app.test_request_context(
            f'/api/blog/admin/posts/{self.post_id}',
            method='PUT',
            json={
                'title': '¿Qué es un establecimiento saludable? | Mikel\'s Fruit',
                'excerpt': 'Una reflexión sobre alimentación y bienestar en la hostelería.',
                'preserve_slug': True,
            },
        ):
            # Bypass the two production authorization wrappers; their
            # enforcement is tested separately below.
            response = admin_update_post.__wrapped__.__wrapped__(self.post_id)

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            post = db.session.get(BlogPost, self.post_id)
            self.assertEqual(post.slug, 'que-es-un-establecimiento-saludable-mikel-s-earth')
            self.assertEqual(post.content, '<p>Contenido histórico que no debe cambiar.</p>')
            self.assertEqual(post.title, '¿Qué es un establecimiento saludable? | Mikel\'s Fruit')
            self.assertEqual(post.excerpt, 'Una reflexión sobre alimentación y bienestar en la hostelería.')

    def test_blog_admin_routes_require_the_active_panel_session(self):
        secured = Flask(__name__)
        secured.config.update(TESTING=True)
        secured.register_blueprint(blog_bp, url_prefix='/api/blog')

        response = secured.test_client().get('/api/blog/admin/posts')

        self.assertEqual(response.status_code, 401)
        self.assertIn('Token de autenticación requerido', response.get_json()['error'])

    def test_legacy_blog_login_fallback_is_not_present(self):
        source = Path('src/routes/blog_routes.py').read_text(encoding='utf-8')

        self.assertNotIn('BLOG_ADMIN_USERNAME', source)
        self.assertNotIn('BLOG_ADMIN_PASSWORD', source)
        self.assertNotIn('mikels-blog-secret-key', source)
        self.assertNotIn("@blog_bp.route('/admin/login'", source)
        self.assertIn("os.getenv('CLOUDINARY_CLOUD_NAME', '')", source)
        self.assertIn("os.getenv('CLOUDINARY_API_KEY', '')", source)
        self.assertIn("os.getenv('CLOUDINARY_API_SECRET', '')", source)


if __name__ == '__main__':
    unittest.main()
