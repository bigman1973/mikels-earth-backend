import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.services.meta_conversions_api import dispatch_meta_purchase_event


class MetaConversionsApiTests(unittest.TestCase):
    def _order(self):
        return SimpleNamespace(
            order_number="MKL-20261009-AB12CD34",
            paid_at=datetime(2026, 10, 9, 8, 0),
            customer_email="cliente@example.test",
            customer_phone="+34 600 000 000",
            stripe_checkout_session_id="cs_test_123",
            total=218.90,
            items=[{
                "sku": "MIKVET500R",
                "quantity": 12,
                "gross_total": "218.90",
            }],
        )

    def test_purchase_is_not_sent_without_marketing_consent(self):
        with patch("src.services.meta_conversions_api.requests.post") as post:
            result = dispatch_meta_purchase_event(self._order(), marketing_consent=False)

        self.assertEqual(result, {"sent": False, "reason": "marketing_consent_not_granted"})
        post.assert_not_called()

    @patch.dict(os.environ, {
        "META_PIXEL_ID": "1234567890",
        "META_CONVERSIONS_API_TOKEN": "test-token",
        "FRONTEND_URL": "https://www.mikels.es",
    }, clear=False)
    @patch("src.services.meta_conversions_api.requests.post")
    def test_purchase_uses_order_number_for_browser_server_deduplication(self, post):
        response = Mock()
        response.content = b'{"events_received": 1}'
        response.json.return_value = {"events_received": 1, "fbtrace_id": "trace-test"}
        response.raise_for_status.return_value = None
        post.return_value = response

        result = dispatch_meta_purchase_event(self._order(), marketing_consent=True)

        self.assertTrue(result["sent"])
        self.assertEqual(result["events_received"], 1)
        _, kwargs = post.call_args
        self.assertEqual(kwargs["params"], {"access_token": "test-token"})
        event = kwargs["json"]["data"][0]
        self.assertEqual(event["event_name"], "Purchase")
        self.assertEqual(event["event_id"], "MKL-20261009-AB12CD34")
        self.assertEqual(event["action_source"], "website")
        self.assertEqual(event["custom_data"]["currency"], "EUR")
        self.assertEqual(event["custom_data"]["value"], 218.90)
        self.assertEqual(event["custom_data"]["content_ids"], ["MIKVET500R"])
        self.assertEqual(event["custom_data"]["contents"], [{
            "id": "MIKVET500R", "quantity": 12, "item_price": 18.24,
        }])
        self.assertEqual(event["event_source_url"], "https://www.mikels.es/order-success?session_id=cs_test_123")
        self.assertIn("em", event["user_data"])
        self.assertIn("ph", event["user_data"])

    @patch.dict(os.environ, {
        "META_PIXEL_ID": "1234567890",
        "META_CONVERSIONS_API_TOKEN": "test-token",
    }, clear=False)
    @patch("src.services.meta_conversions_api.requests.post")
    def test_purchase_without_sku_is_skipped_before_network_call(self, post):
        order = self._order()
        order.items = [{"quantity": 1, "gross_total": "19.90"}]

        result = dispatch_meta_purchase_event(order, marketing_consent=True)

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "missing_sku")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
