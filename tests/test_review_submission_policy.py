import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROUTE = ROOT / 'src/routes/review_routes.py'


class ReviewSubmissionPolicyTests(unittest.TestCase):
    def test_new_reviews_do_not_generate_or_offer_discount_coupons(self):
        source = REVIEW_ROUTE.read_text()
        self.assertNotIn('GRACIAS10-', source)
        self.assertNotIn('_generate_review_coupon_code', source)
        self.assertNotIn('reward_coupon_code=coupon_code', source)
        self.assertNotIn("'coupon_code': coupon_code", source)
        self.assertNotIn('cupón de descuento del 10%', source)

    def test_submission_event_preserves_review_context_without_coupon_data(self):
        tree = ast.parse(REVIEW_ROUTE.read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        send_calls = [node for node in calls if getattr(node.func, 'id', None) == 'send_klaviyo_event']
        review_calls = [node for node in send_calls if any(
            isinstance(keyword.value, ast.Constant) and keyword.value.value == 'Mikels Review Submitted'
            for keyword in node.keywords if keyword.arg == 'metric_name'
        )]
        self.assertEqual(len(review_calls), 1)
        properties = next(keyword.value for keyword in review_calls[0].keywords if keyword.arg == 'properties')
        keys = {key.value for key in properties.keys if isinstance(key, ast.Constant)}
        self.assertTrue({'CustomerName', 'ProductName', 'Rating', 'Comment', 'Source'} <= keys)
        self.assertNotIn('CouponCode', keys)


if __name__ == '__main__':
    unittest.main()
