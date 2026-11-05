"""PREPARED / NOT ACTIVE: SQLite unit fixtures only. No test in this file proves live PG/proxy readiness."""
from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch

from flask import Flask
from sqlalchemy import select, text

from src.models.form_attempt import FormAttempt, FormAuditBase
from src.models.user import db
from src.models.vip_signup import VipSignup
from prepared_contact_fixture import contact_bp
from prepared_vip_fixture import vip_bp
from src.services import form_security_prepared as security


class FormSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True, SECRET_KEY='unit-form-fixture-only',
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False,
            FORM_SECURITY_SQLITE_FIXTURE=True,
        )
        db.init_app(self.app)
        self.app.register_blueprint(vip_bp, url_prefix='/api/vip')
        self.app.register_blueprint(contact_bp, url_prefix='/api/contact')
        with self.app.app_context():
            db.create_all()
            FormAuditBase.metadata.create_all(db.engine)
        self.client = self.app.test_client()
        self.subscribe = self.enterContext(patch('prepared_vip_fixture.klaviyo_subscribe_vip_contact', return_value=(True, None)))
        self.event = self.enterContext(patch('prepared_vip_fixture.klaviyo_track_vip_signup', return_value=(True, None)))
        self.notify = self.enterContext(patch('prepared_contact_fixture.dispatch_contact_notification'))
        self.confirm = self.enterContext(patch('prepared_contact_fixture.dispatch_contact_confirmation'))

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            FormAuditBase.metadata.drop_all(db.engine)

    def vip(self, email='marisa@example.com', **overrides):
        return dict(email=email, first_name='Marisa', marketing_consent=True, **overrides)

    def contact(self, email='marisa@example.com', **overrides):
        values = dict(email=email, name='Marisa', phone='612345678', message='Quiero información sobre el aceite de oliva.')
        values.update(overrides)
        return values

    def rows(self):
        with self.app.app_context(), db.engine.connect() as connection:
            return connection.execute(select(FormAttempt.__table__).order_by(FormAttempt.id)).mappings().all()

    def test_honeypot_both_aliases_both_endpoints_zero_db_and_network_even_config_missing(self):
        self.app.config['SECRET_KEY'] = None
        self.app.config['FORM_CLIENT_IP_MODE'] = 'unverified'
        with self.app.app_context():
            FormAuditBase.metadata.drop_all(db.engine)
        for endpoint, payload in (('/api/vip/signup', self.vip()), ('/api/contact/send-message', self.contact())):
            for field in ('website', '_hp'):
                with self.subTest(endpoint=endpoint, field=field), patch.object(security, 'claim_attempt', side_effect=AssertionError('DB reached')):
                    response = self.client.post(endpoint, json={**payload, field: 'filled'})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json, security.GENERIC_SUCCESS)
        self.subscribe.assert_not_called()
        self.event.assert_not_called()
        self.notify.assert_not_called()
        self.confirm.assert_not_called()
        with self.app.app_context():
            self.assertEqual(VipSignup.query.count(), 0)

    def test_four_requests_in_34_seconds_only_first_three_claimed(self):
        base = datetime(2026, 11, 4, 20, 0, 0)
        for index, offset in enumerate((0, 10, 20, 34)):
            with patch.object(security, 'datetime') as clock:
                clock.utcnow.return_value = base + timedelta(seconds=offset)
                response = self.client.post('/api/vip/signup', json=self.vip(email=f'person{index}@example.com'))
            self.assertEqual(response.status_code, 201 if index < 3 else 429)
        self.assertEqual(self.subscribe.call_count, 3)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(response.json['code'], 'rate_limited')
        self.assertGreater(int(response.headers['Retry-After']), 3500)

    def test_contact_three_max_and_no_memory_store(self):
        for index in range(4):
            response = self.client.post('/api/contact/send-message', json=self.contact(email=f'person{index}@example.com'))
            self.assertEqual(response.status_code, 200 if index < 3 else 429)
        self.assertEqual(self.notify.call_count, 3)
        self.assertEqual(self.confirm.call_count, 3)
        self.assertEqual(len(self.rows()), 3)

    def test_shared_ip_quota_cannot_be_doubled_by_alternating_endpoints(self):
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip()).status_code, 201)
        self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact()).status_code, 200)
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip(email='other@example.com')).status_code, 201)
        response = self.client.post('/api/contact/send-message', json=self.contact(message='Necesito también información del envío.'))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.notify.call_count, 1)
        self.assertEqual(len(self.rows()), 3)

    def test_exact_rolling_boundary_and_different_ips(self):
        base = datetime(2026, 11, 4, 20, 0, 0)
        with patch.object(security, 'datetime') as clock:
            clock.utcnow.return_value = base
            for index in range(3):
                self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact(email=f'a{index}@example.com')).status_code, 200)
            clock.utcnow.return_value = base + timedelta(seconds=3599)
            self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact()).status_code, 429)
            self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact(), environ_base={'REMOTE_ADDR': '198.51.100.3'}).status_code, 200)
            clock.utcnow.return_value = base + timedelta(seconds=3600)
            self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact()).status_code, 200)

    def test_gmail_dotted_and_plus_are_dedupe_only_delivery_original_preserved(self):
        emails = (' O.Q.A.S.I.W@GMAIL.COM ', 'oqasiw@gmail.com', 'oqasiw+vip@googlemail.com')
        for index, email in enumerate(emails):
            response = self.client.post('/api/vip/signup', json=self.vip(email=email))
            self.assertEqual(response.status_code, 201 if index == 0 else 200)
        self.subscribe.assert_called_once()
        self.event.assert_called_once()
        self.assertEqual(self.subscribe.call_args.kwargs['email'], 'o.q.a.s.i.w@gmail.com')
        rows = self.rows()
        self.assertEqual([row['duplicate'] for row in rows], [False, True, True])
        self.assertEqual({row['canonical_email'] for row in rows}, {'oqasiw@gmail.com'})
        with self.app.app_context():
            self.assertEqual(VipSignup.query.count(), 1)
            self.assertEqual(VipSignup.query.one().email, 'o.q.a.s.i.w@gmail.com')

    def test_custom_domain_dots_and_plus_not_collapsed(self):
        for email in ('m.a.r.i.s.a@example.com', 'marisa@example.com', 'marisa+vip@example.com'):
            self.assertEqual(self.client.post('/api/vip/signup', json=self.vip(email=email)).status_code, 201)
        self.assertEqual(self.subscribe.call_count, 3)
        self.assertTrue(all(not row['duplicate'] for row in self.rows()))

    def test_contact_same_person_distinct_legitimate_messages_not_blocked(self):
        first = self.contact(email='m.a.r.i.s.a@gmail.com')
        second = self.contact(email='marisa+consulta@googlemail.com', message='También quiero saber si enviáis a Barcelona.')
        self.assertEqual(self.client.post('/api/contact/send-message', json=first).status_code, 200)
        self.assertEqual(self.client.post('/api/contact/send-message', json=second).status_code, 200)
        self.assertEqual(self.notify.call_count, 2)
        self.assertEqual(self.confirm.call_count, 2)
        self.assertEqual([row['duplicate'] for row in self.rows()], [False, True])
        self.assertEqual(self.confirm.call_args.args[1], 'marisa+consulta@googlemail.com')

    @patch('src.services.klaviyo_service.send_klaviyo_event', return_value=True)
    def test_contact_confirmation_event_matches_exact_published_service(self, send_event):
        from src.services.klaviyo_service import klaviyo_send_contact_confirmation
        self.assertTrue(klaviyo_send_contact_confirmation('Marisa Nueva', 'marisa@example.com', 'Una consulta legítima'))
        self.assertEqual(send_event.call_args.kwargs['profile_attrs'], {'first_name': 'Marisa'})
        self.assertEqual(send_event.call_args.kwargs['properties']['ContactName'], 'Marisa Nueva')
        self.assertEqual(send_event.call_args.kwargs['profile_email'], 'marisa@example.com')

    def test_existing_signup_is_read_only_duplicate_and_never_resubscribed(self):
        old_date = datetime(2026, 10, 1, 13, 0, 0)
        with self.app.app_context():
            old = VipSignup(email='M.A.R.I.S.A@googlemail.com', first_name='Historic', consent_marketing=True,
                            consented_at=old_date, source_ip='198.51.100.1',
                            klaviyo_subscription_status='accepted', klaviyo_event_status='accepted')
            db.session.add(old)
            db.session.commit()
            old_id = old.id
        response = self.client.post('/api/vip/signup', json=self.vip(email='marisa@gmail.com'))
        self.assertEqual(response.status_code, 200)
        self.subscribe.assert_not_called()
        self.event.assert_not_called()
        self.assertTrue(self.rows()[0]['duplicate'])
        with self.app.app_context():
            old = db.session.get(VipSignup, old_id)
            self.assertEqual(VipSignup.query.count(), 1)
            self.assertEqual(old.email, 'M.A.R.I.S.A@googlemail.com')
            self.assertEqual(old.consented_at, old_date)
            self.assertEqual(old.source_ip, '198.51.100.1')
            self.assertEqual(old.first_name, 'Historic')

    def test_failed_vip_flow_not_repeated_automatically(self):
        self.subscribe.return_value = (False, 'mock failure')
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip()).status_code, 502)
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip()).status_code, 200)
        self.subscribe.assert_called_once()
        self.event.assert_not_called()
        self.assertEqual([row['outcome'] for row in self.rows()], ['vip_failed', 'vip_duplicate'])

    def test_unexpected_vip_network_exception_is_generic_and_not_retried(self):
        self.subscribe.side_effect = RuntimeError('sensitive-config-must-not-leak')
        response = self.client.post('/api/vip/signup', json=self.vip())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('sensitive-config', response.get_data(as_text=True))
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip()).status_code, 200)
        self.subscribe.assert_called_once()
        self.assertEqual([row['outcome'] for row in self.rows()], ['vip_failed', 'vip_duplicate'])

    def test_optional_phone_and_spanish_nine_digits_preserved(self):
        for endpoint, factory in (('/api/vip/signup', self.vip), ('/api/contact/send-message', self.contact)):
            for index, value in enumerate((None, '', '612345678', '+34612345678', '0034612345678')):
                response = self.client.post(endpoint, json=factory(phone=value), environ_base={'REMOTE_ADDR': f'198.51.100.{index + (1 if endpoint.endswith("signup") else 10)}'})
                self.assertIn(response.status_code, (200, 201))

    def test_exact_ten_digit_phone_rejected_both_without_side_effects(self):
        for endpoint, factory in (('/api/vip/signup', self.vip), ('/api/contact/send-message', self.contact)):
            response = self.client.post(endpoint, json=factory(phone='612 345 6789'))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json['code'], 'invalid_phone')
        self.subscribe.assert_not_called()
        self.notify.assert_not_called()
        with self.app.app_context():
            self.assertEqual(VipSignup.query.count(), 0)

    def test_ip_ignores_spoofed_xff_and_hash_is_stable(self):
        for index in range(4):
            response = self.client.post('/api/vip/signup', json=self.vip(email=f'ip{index}@example.com'),
                                        headers={'X-Forwarded-For': f'203.0.113.{index}'})
            self.assertEqual(response.status_code, 201 if index < 3 else 429)
        rows = self.rows()
        self.assertEqual(len({row['ip_hash'] for row in rows}), 1)
        self.assertEqual(len(rows[0]['ip_hash']), 64)
        self.assertNotIn('127.0.0.1', rows[0]['ip_hash'])
        with self.app.test_request_context(environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            self.assertEqual(rows[0]['ip_hash'], security._ip_hash(security.client_ip()))

    def test_unverified_proxy_unknown_ip_missing_secret_or_table_fail_closed(self):
        for setting, value in (('FORM_CLIENT_IP_MODE', 'verified_header'), ('SECRET_KEY', None)):
            original = self.app.config.get(setting, 'direct')
            self.app.config[setting] = value
            response = self.client.post('/api/vip/signup', json=self.vip())
            self.assertEqual(response.status_code, 503)
            self.app.config[setting] = original
        self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact(), environ_base={'REMOTE_ADDR': 'unknown'}).status_code, 503)
        with self.app.app_context():
            FormAuditBase.metadata.drop_all(db.engine)
        response = self.client.post('/api/vip/signup', json=self.vip())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json['code'], 'form_unavailable')
        self.subscribe.assert_not_called()

    def test_sqlite_not_allowed_without_explicit_testing_fixture(self):
        self.app.config['FORM_SECURITY_SQLITE_FIXTURE'] = False
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip()).status_code, 503)
        self.app.config['FORM_SECURITY_SQLITE_FIXTURE'] = True
        self.app.config['TESTING'] = False
        self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact()).status_code, 503)
        self.assertEqual(len(self.rows()), 0)

    def test_verified_header_requires_exact_peer_and_single_overwritten_header(self):
        # Documentation-network fixture; NOT a claim about Railway's real CIDRs.
        self.app.config.update(FORM_CLIENT_IP_MODE='verified_header', FORM_TRUSTED_PROXY_CIDRS='192.0.2.0/24',
                               FORM_VERIFIED_IP_HEADER='X-Test-Verified-IP')
        for peer, headers, expected in (
            ('192.0.2.10', {'X-Test-Verified-IP': '198.51.100.12', 'X-Forwarded-For': '203.0.113.5'}, 201),
            ('203.0.113.1', {'X-Test-Verified-IP': '198.51.100.12'}, 503),
            ('192.0.2.10', {'X-Test-Verified-IP': '198.51.100.12, 203.0.113.5'}, 503),
            ('192.0.2.10', {}, 503),
        ):
            response = self.client.post('/api/vip/signup', json=self.vip(), headers=headers, environ_base={'REMOTE_ADDR': peer})
            self.assertEqual(response.status_code, expected)
        with self.app.app_context():
            self.assertEqual(VipSignup.query.one().source_ip, '198.51.100.12')
        self.app.config['FORM_TRUSTED_PROXY_CIDRS'] = '0.0.0.0/0'
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip()).status_code, 503)

    def test_invalid_submissions_consume_quota_but_not_vip_claim(self):
        for index in range(3):
            response = self.client.post('/api/vip/signup', json={'email': 'marisa@gmail.com', 'marketing_consent': False})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip()).status_code, 429)
        self.assertTrue(all(not row['vip_claim'] for row in self.rows()))

    def test_contact_timestamp_filter_remains_not_required(self):
        fast = self.contact(_ts=datetime.utcnow().timestamp() * 1000)
        self.assertEqual(self.client.post('/api/contact/send-message', json=fast).status_code, 200)
        self.notify.assert_not_called()
        self.assertEqual(self.rows()[0]['outcome'], 'contact_spam')
        self.assertEqual(self.client.post('/api/contact/send-message', json=self.contact()).status_code, 200)
        self.notify.assert_called_once()

    def test_payloads_and_email_spaces_invalid_without_network(self):
        for data in ([], None, 'string'):
            self.assertEqual(self.client.post('/api/vip/signup', json=data).status_code, 400)
        self.assertEqual(self.client.post('/api/vip/signup', json=self.vip(email='ma risa@gmail.com')).status_code, 400)
        self.subscribe.assert_not_called()

    def test_form_model_is_not_in_flask_create_all_metadata(self):
        self.assertNotIn('form_attempts', db.metadata.tables)
        self.assertIn('form_attempts', FormAuditBase.metadata.tables)

    def test_postgres_sql_contract_claims_under_transaction_lock_and_db_time_mock_only(self):
        # SQL contract test, deliberately NOT a PG integration/concurrency test.
        db_now = datetime(2026, 11, 4, 21, 0, 0)
        connection = MagicMock()
        connection.dialect.name = 'postgresql'
        clock_result = Mock()
        clock_result.scalar_one.return_value = db_now
        recent_result = Mock()
        recent_result.all.return_value = []
        prior_result = Mock()
        prior_result.first.return_value = None
        legacy_result = Mock()
        legacy_result.first.return_value = None
        inserted = Mock(inserted_primary_key=[42])
        connection.execute.side_effect = [Mock(), Mock(), Mock(), Mock(), clock_result,
                                          recent_result, prior_result, legacy_result, inserted]
        transaction = MagicMock()
        transaction.__enter__.return_value = connection
        engine = Mock()
        engine.begin.return_value = transaction
        with self.app.app_context(), patch.object(security, '_storage_engine', return_value=engine):
            claim = security.claim_attempt('vip', '198.51.100.1', 'm.a.r.i.s.a@gmail.com', eligible=True)
        self.assertEqual(claim.attempt_id, 42)
        self.assertEqual(claim.attempted_at, db_now)
        self.assertFalse(claim.duplicate)
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertIn('ISOLATION LEVEL READ COMMITTED', statements[0])
        self.assertIn('lock_timeout', statements[1])
        self.assertIn('statement_timeout', statements[2])
        self.assertIn('pg_advisory_xact_lock', statements[3])
        self.assertIn('clock_timestamp()', statements[4])
        self.assertIn('form_attempts', statements[5])
        self.assertIn('vip_signups', statements[7])
        self.assertIn('split_part', statements[7])
        self.assertIn('INSERT INTO form_attempts', statements[8])
        engine.begin.assert_called_once()
        self.subscribe.assert_not_called()
        self.event.assert_not_called()

    def test_additive_sql_contains_unique_vip_claim_and_no_historical_dml(self):
        source = (Path(__file__).resolve().parents[1] / 'migrations/add_form_attempts.sql').read_text()
        executable = '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith('--'))
        self.assertIn('uq_form_attempts_vip_claim', executable)
        self.assertIn('WHERE vip_claim = TRUE', executable)
        for forbidden in ('DELETE ', 'UPDATE ', 'ALTER TABLE vip_signups', 'DROP '):
            self.assertNotIn(forbidden, executable)


if __name__ == '__main__':
    unittest.main()
