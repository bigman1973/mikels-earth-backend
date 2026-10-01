import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RetiredWorkshopEndpointTests(unittest.TestCase):
    def test_public_workshop_endpoint_is_not_registered(self):
        main_source = (ROOT / 'src' / 'main.py').read_text(encoding='utf-8')
        self.assertNotIn('experience_routes', main_source)
        self.assertNotIn('/api/experience', main_source)
        self.assertFalse((ROOT / 'src' / 'routes' / 'experience_routes.py').exists())

    def test_removed_workshop_post_no_longer_reaches_an_application_handler(self):
        environment = os.environ.copy()
        environment.update({
            'SECRET_KEY': 'test-session-secret',
            'JWT_SECRET': 'test-jwt-secret',
            'ADMIN_SECRET_KEY': 'test-admin-secret',
            'DATABASE_URL': 'sqlite:///:memory:',
        })
        script = """
from src.main import app
response = app.test_client().post('/api/experience/workshop-visit', json={})
# The frontend's GET-only SPA fallback owns the path after route removal, so a
# POST is rejected before any workshop handler can run.  It must never be 500.
assert response.status_code == 405, response.status_code
assert not any(rule.rule == '/api/experience/workshop-visit' for rule in app.url_map.iter_rules())
print('workshop endpoint removed: 405')
"""
        result = subprocess.run(
            [sys.executable, '-c', script],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('workshop endpoint removed: 405', result.stdout)


if __name__ == '__main__':
    unittest.main()
