import unittest

from flask import Response

from src.main import app, prevent_stale_commerce_responses


class CommerceCacheHeaderTests(unittest.TestCase):
    def test_products_are_never_reused_without_revalidation(self):
        with app.test_request_context('/api/products'):
            response = prevent_stale_commerce_responses(Response())
        self.assertEqual(
            response.headers['Cache-Control'],
            'no-store, max-age=0, must-revalidate',
        )
        self.assertEqual(response.headers['Pragma'], 'no-cache')
        self.assertEqual(response.headers['Expires'], '0')

    def test_non_commerce_response_is_not_overridden(self):
        with app.test_request_context('/api/blog/posts'):
            response = prevent_stale_commerce_responses(Response())
        self.assertNotIn('Cache-Control', response.headers)


if __name__ == '__main__':
    unittest.main()
