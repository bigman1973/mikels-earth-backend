import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_KEYS = ('SECRET_KEY', 'JWT_SECRET', 'ADMIN_SECRET_KEY')


class SecurityConfigurationTests(unittest.TestCase):
    def _import_main(self, environment):
        with tempfile.TemporaryDirectory() as temporary_directory:
            # Prevent a local .env file from changing this startup test.
            Path(temporary_directory, 'dotenv.py').write_text(
                'def load_dotenv(*args, **kwargs):\n    return False\n',
                encoding='utf-8',
            )
            env = os.environ.copy()
            for key in REQUIRED_KEYS:
                env.pop(key, None)
            env.update(environment)
            env['PYTHONPATH'] = os.pathsep.join([
                temporary_directory,
                str(ROOT),
                env.get('PYTHONPATH', ''),
            ])
            return subprocess.run(
                [sys.executable, '-c', 'import src.main'],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
            )

    def test_backend_refuses_to_start_without_required_security_variables(self):
        result = self._import_main({})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing required security environment variables', result.stderr)
        for key in REQUIRED_KEYS:
            self.assertIn(key, result.stderr)

    def test_backend_imports_when_all_required_security_variables_exist(self):
        result = self._import_main({
            'SECRET_KEY': 'test-flask-session-key',
            'JWT_SECRET': 'test-jwt-key',
            'ADMIN_SECRET_KEY': 'test-review-admin-key',
        })

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_removed_fallback_values_cannot_reappear(self):
        main_source = (ROOT / 'src' / 'main.py').read_text(encoding='utf-8')
        auth_source = (ROOT / 'src' / 'routes' / 'auth_routes.py').read_text(encoding='utf-8')
        review_source = (ROOT / 'src' / 'routes' / 'review_routes.py').read_text(encoding='utf-8')

        self.assertNotIn('asdf#FGSgvasgf$5$WGT', main_source)
        self.assertNotIn('mikels-admin-secret-key', auth_source)
        self.assertNotIn("os.environ.get('ADMIN_SECRET_KEY'", review_source)
        self.assertIn("os.environ['JWT_SECRET']", auth_source)
        self.assertIn("os.environ['ADMIN_SECRET_KEY']", review_source)


if __name__ == '__main__':
    unittest.main()
