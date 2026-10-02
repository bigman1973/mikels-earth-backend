import os
import unittest

from flask import Flask

os.environ.setdefault('JWT_SECRET', 'test-only-blog-metadata-secret')

from src.models.user import db
from src.models.blog import BlogPost
from src.routes.blog_routes import admin_update_post


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
            response = admin_update_post.__wrapped__('admin-test', self.post_id)

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            post = db.session.get(BlogPost, self.post_id)
            self.assertEqual(post.slug, 'que-es-un-establecimiento-saludable-mikel-s-earth')
            self.assertEqual(post.content, '<p>Contenido histórico que no debe cambiar.</p>')
            self.assertEqual(post.title, '¿Qué es un establecimiento saludable? | Mikel\'s Fruit')
            self.assertEqual(post.excerpt, 'Una reflexión sobre alimentación y bienestar en la hostelería.')


if __name__ == '__main__':
    unittest.main()
