"""Local fixtures only: provider calls mocked; no live consent or IP evidence."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import Flask
from src.models.user import db
from src.models.vip_signup import VipSignup
from src.routes.vip_routes import vip_bp
from src.routes.contact_routes import contact_bp
from src.services import form_security as security


class FormSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(vip_bp, url_prefix='/api/vip')
        self.app.register_blueprint(contact_bp, url_prefix='/api/contact')
        with self.app.app_context():
            db.create_all()
        self.client = self.app.test_client()
        self.subscribe = self.enterContext(patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None)))
        self.event = self.enterContext(patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None)))
        self.notify = self.enterContext(patch('src.routes.contact_routes.dispatch_contact_notification'))
        self.confirm = self.enterContext(patch('src.routes.contact_routes.dispatch_contact_confirmation'))

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def vip(self, **overrides):
        return {'email': 'form-fixture@example.invalid', 'first_name': 'Prueba', 'marketing_consent': True, **overrides}

    def contact(self, **overrides):
        return {'email': 'form-fixture@example.invalid', 'name': 'Prueba', 'phone': '', 'message': 'Una consulta de prueba sobre los productos.', **overrides}

    def endpoints(self):
        return (('/api/vip/signup', self.vip), ('/api/contact/send-message', self.contact))

    def test_honeypot_all_aliases_both_endpoints_before_db_ip_and_any_provider(self):
        with self.app.app_context():
            db.drop_all()
        for endpoint, factory in self.endpoints():
            for field in security.HONEYPOT_FIELDS:
                for value in ('filled', False, 0, [], {}):
                    with self.subTest(endpoint=endpoint, field=field), \
                            patch('src.routes.vip_routes.consent_ip_evidence', side_effect=AssertionError('IP reached')), \
                            patch('src.routes.vip_routes.phone_is_rejected', side_effect=AssertionError('Phone reached')), \
                            patch('src.routes.contact_routes.phone_is_rejected', side_effect=AssertionError('Phone reached')):
                        captured = StringIO()
                        with redirect_stdout(captured):
                            response = self.client.post(endpoint, json=factory(**{field: value}))
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json, security.GENERIC_SUCCESS)
                        self.assertEqual(captured.getvalue(), '')
        for provider in (self.subscribe, self.event, self.notify, self.confirm):
            provider.assert_not_called()
        with self.app.app_context():
            db.create_all()
            self.assertEqual(VipSignup.query.count(), 0)
        self.assertEqual(security.HONEYPOT_OUTCOME, 'honeypot_discarded')

    def test_exact_ten_unprefixed_digits_rejected_before_consent_or_providers(self):
        for endpoint, factory in self.endpoints():
            for value in ('6123456789', '612 345 6789', '(612) 345-6789', '612.345.6789', 6123456789):
                response = self.client.post(endpoint, json=factory(phone=value))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json['code'], 'invalid_phone')
        for provider in (self.subscribe, self.event, self.notify, self.confirm):
            provider.assert_not_called()
        with self.app.app_context():
            self.assertEqual(VipSignup.query.count(), 0)

    def test_optional_empty_nine_spanish_and_prefixed_phone_accepted(self):
        for endpoint, factory in self.endpoints():
            for value in (None, '', ' ', '612345678', '612 345 678', '+34612345678', '0034612345678', '0012345678'):
                self.assertIn(self.client.post(endpoint, json=factory(phone=value)).status_code, (200, 201))

    def test_no_unknown_proxy_bucket_and_forged_headers_do_not_limit_anyone(self):
        self.assertFalse(security.IP_RATE_LIMITS_ENABLED)
        self.app.config.update(FORM_CLIENT_IP_MODE='direct', FORM_TRUSTED_PROXY_CIDRS='0.0.0.0/0')
        for endpoint, factory in self.endpoints():
            for index in range(12):
                response = self.client.post(endpoint, json=factory(),
                    headers={'X-Forwarded-For': f'198.51.100.{index}', 'X-Real-IP': '198.51.100.42'},
                    environ_base={'REMOTE_ADDR': '127.0.0.1'})
                self.assertIn(response.status_code, (200, 201))
        self.assertEqual(self.subscribe.call_count, 12)
        self.assertEqual(self.notify.call_count, 12)

    def test_consent_observation_preserved_as_unverified_not_asserted_to_klaviyo(self):
        response = self.client.post('/api/vip/signup', json=self.vip(), headers={
            'X-Forwarded-For': '203.0.113.1', 'X-Real-IP': '198.51.100.42'})
        self.assertEqual(response.status_code, 201)
        with self.app.app_context():
            row = VipSignup.query.one()
            self.assertEqual(row.source_ip, '198.51.100.42')
            self.assertEqual(row.source, 'vip_black_friday_2026:ip_unverified:x_real_ip')
            self.assertIsNotNone(row.consented_at)
        self.assertEqual(self.subscribe.call_args.kwargs['source_ip'], 'unverified')
        self.assertEqual(self.event.call_args.kwargs['source_ip'], 'unverified')

    def test_missing_or_malformed_real_ip_has_no_xff_or_socket_fallback(self):
        for value in (None, '', 'bad', '198.51.100.1, 198.51.100.2', 'fe80::1%eth0'):
            headers = {'X-Forwarded-For': '203.0.113.1'}
            if value is not None:
                headers['X-Real-IP'] = value
            response = self.client.post('/api/vip/signup', json=self.vip(), headers=headers)
            self.assertEqual(response.status_code, 201)
            with self.app.app_context():
                row = VipSignup.query.order_by(VipSignup.id.desc()).first()
                self.assertEqual(row.source_ip, 'unverified')
                self.assertEqual(row.source, 'vip_black_friday_2026:ip_unverified:unavailable')

    def test_invalid_payload_and_oversized_body_before_provider(self):
        for endpoint, _ in self.endpoints():
            for value in ([], None, 'string'):
                self.assertEqual(self.client.post(endpoint, json=value).status_code, 400)
            self.assertEqual(self.client.post(endpoint, data='x' * 16385, content_type='application/json').status_code, 413)
        self.subscribe.assert_not_called()
        self.notify.assert_not_called()

    def test_contact_preserves_timing_filter_without_phone_requirement(self):
        import time
        response = self.client.post('/api/contact/send-message', json=self.contact(_ts=time.time() * 1000))
        self.assertEqual(response.status_code, 200)
        self.notify.assert_not_called()
        self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact()).status_code, 200)
        self.notify.assert_called_once()

    def test_contact_provider_exception_is_neutral_and_not_logged(self):
        self.notify.side_effect = RuntimeError('private-fixture')
        captured = StringIO()
        with redirect_stdout(captured):
            response = self.client.post('/api/contact/send-message', json=self.contact())
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private-fixture', response.get_data(as_text=True))
        self.assertEqual(captured.getvalue(), '')

    def test_all_legacy_form_ip_limits_removed_and_main_bypasses_bootstrap(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('vip_routes.py', 'contact_routes.py', 'newsletter_routes.py', 'horeca_routes.py', 'product_notify_routes.py', 'review_routes.py'):
            source = (root / 'src/routes' / name).read_text()
            self.assertNotIn('rate_store', source)
            self.assertNotIn('rate_limit_store', source)
            self.assertNotIn('X-Forwarded-For', source)
            self.assertNotIn('remote_addr', source)
        main = (root / 'src/main.py').read_text()
        block = main[main.index('def create_tables():'):main.index('    if not hasattr(app,')]
        self.assertIn("('/api/vip/signup', '/api/contact/send-message')", block)
        self.assertNotIn('claim_attempt', (root / 'src/routes/vip_routes.py').read_text())


if __name__ == '__main__':
    unittest.main()
