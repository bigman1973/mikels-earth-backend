"""Exercise the production CORS configuration in a network-free Flask fixture."""
import ast
from pathlib import Path
import unittest

from flask import Flask, jsonify
from flask_cors import CORS


def real_cors_resources():
    source = Path(__file__).resolve().parents[1] / 'src' / 'main.py'
    tree = ast.parse(source.read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'CORS']
    if len(calls) != 1:
        raise AssertionError('Expected one explicit CORS configuration')
    resource_node = next(arg.value for arg in calls[0].keywords
                         if arg.arg == 'resources')
    return ast.literal_eval(resource_node)


class FormCorsTests(unittest.TestCase):
    def test_rate_limit_wait_is_readable_from_frontend_origin(self):
        app = Flask(__name__)
        app.config['TESTING'] = True
        CORS(app, resources=real_cors_resources())

        @app.post('/api/vip/signup')
        def fixture_limit():
            response = jsonify({'error': 'Espera antes de volver a intentarlo.'})
            response.status_code = 429
            response.headers['Retry-After'] = '3600'
            return response

        response = app.test_client().post('/api/vip/signup',
            headers={'Origin': 'https://www.mikels.es'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '3600')
        self.assertIn('Retry-After', response.headers.get(
            'Access-Control-Expose-Headers', '').split(', '))
        self.assertEqual(response.headers['Access-Control-Allow-Origin'],
                         'https://www.mikels.es')

    def test_only_retry_after_added_to_exposed_headers(self):
        resources = real_cors_resources()['/api/*']
        self.assertEqual(resources['expose_headers'], ['Retry-After'])
        self.assertEqual(resources['allow_headers'], ['Content-Type', 'Authorization'])
        self.assertEqual(resources['origins'], '*')
        self.assertTrue(resources['supports_credentials'])


if __name__ == '__main__':
    unittest.main()
