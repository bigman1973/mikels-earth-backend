import unittest
from unittest.mock import patch

from flask import Flask

from src.models.product_notification import ProductNotification
from src.models.user import db
from src.routes.product_notify_routes import product_notify_bp


class ProductNotificationDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(product_notify_bp, url_prefix="/api")
        with self.app.app_context():
            db.create_all()
            db.session.add(
                ProductNotification(
                    email="cliente@example.com",
                    name="Cliente",
                    product_name="Producto agotado",
                    product_id="producto-agotado",
                )
            )
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _notify(self):
        return self.client.post(
            "/api/product-notify/available",
            json={"product_id": "producto-agotado", "product_name": "Producto disponible"},
            headers={"X-Admin-Key": "mikels-admin-2026"},
        )

    @patch("src.routes.product_notify_routes.dispatch_product_back_in_stock", return_value=False)
    def test_failed_klaviyo_event_does_not_mark_customer_as_notified(self, _dispatch):
        response = self._notify()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["notified"], 0)
        with self.app.app_context():
            self.assertFalse(ProductNotification.query.one().notified)

    @patch("src.routes.product_notify_routes.dispatch_product_back_in_stock", return_value=True)
    def test_only_accepted_klaviyo_event_marks_customer_as_notified(self, _dispatch):
        response = self._notify()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["notified"], 1)
        with self.app.app_context():
            self.assertTrue(ProductNotification.query.one().notified)


if __name__ == "__main__":
    unittest.main()
