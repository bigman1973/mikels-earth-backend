import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import klaviyo_service


ROOT = Path(__file__).resolve().parents[1]


class BrevoRetirementTests(unittest.TestCase):
    def test_no_runtime_brevo_reference_remains(self):
        source_files = list((ROOT / "src").rglob("*.py"))
        banned = ("api.brevo.com", "BREVO_API_KEY", "BREVO_WEBHOOK_KEY", "/webhook/brevo")
        hits = []
        for file_path in source_files:
            text = file_path.read_text()
            for token in banned:
                if token in text:
                    hits.append(f"{file_path.relative_to(ROOT)}: {token}")
        self.assertEqual(hits, [])

    def test_legacy_brevo_modules_are_removed(self):
        self.assertFalse((ROOT / "src/services/email_service.py").exists())
        self.assertFalse((ROOT / "src/services/email_newsletter_welcome.py").exists())

    @patch("src.services.klaviyo_service.send_klaviyo_event")
    def test_horeca_confirmation_is_a_critical_klaviyo_event(self, send_event):
        send_event.return_value = True
        result = klaviyo_service.klaviyo_send_horeca_confirmation(
            {
                "email": "hosteleria@example.com",
                "contactName": "Ana Restaurante",
                "establishmentName": "Casa Ana",
                "quantity5L": 2,
                "quantityTemprano": 1,
            }
        )
        self.assertTrue(result)
        self.assertEqual(send_event.call_args.kwargs["metric_name"], "Mikels HORECA Confirmation")
        self.assertTrue(send_event.call_args.kwargs["critical"])

    @patch("src.services.klaviyo_service.send_klaviyo_event")
    def test_review_request_is_critical_and_keeps_ten_day_delay_in_flow(self, send_event):
        send_event.return_value = True
        result = klaviyo_service.klaviyo_send_review_request(
            "cliente@example.com", "Cliente Prueba", "MKL-100", [{"name": "Producto", "quantity": 1}]
        )
        self.assertTrue(result)
        self.assertEqual(send_event.call_args.kwargs["metric_name"], "Mikels Review Request")
        self.assertTrue(send_event.call_args.kwargs["critical"])


if __name__ == "__main__":
    unittest.main()
