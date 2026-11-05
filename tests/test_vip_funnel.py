"""Local-only tests. PostgreSQL integration is opt-in, restricted to a Unix socket.

Run with VIP_FUNNEL_TEST_DATABASE_URL pointing at a disposable local test DB.
No Klaviyo, Stripe, remote DB or HTTP calls are permitted.
"""
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('JWT_SECRET', 'local-fixture-only-not-a-real-secret')
from flask import Flask, jsonify
from sqlalchemy import text
from sqlalchemy.engine import make_url
from werkzeug.datastructures import MultiDict

from src.models.user import db
from src.models.admin_user import AdminUser
from src.models.vip_signup import VipSignup
from src.routes.auth_routes import generate_admin_token
from src.routes import vip_routes
from src.routes.vip_funnel_routes import (vip_funnel_bp, validated_range, utc_bounds, daily_payload)
from src.services.vip_funnel import (install_vip_funnel, record_aggregate, mark_vip_outcome, increment_sql, MADRID)

ROOT = Path(__file__).resolve().parents[1]
DB_URL = os.getenv('VIP_FUNNEL_TEST_DATABASE_URL', '')


def local_app(url='sqlite:///:memory:'):
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    install_vip_funnel(app)
    app.register_blueprint(vip_routes.vip_bp, url_prefix='/api/vip')
    app.register_blueprint(vip_funnel_bp)
    return app


def increment_worker(url, iterations, at_iso):
    # Spawn processes: independent engine/pool, no forked inherited connections.
    app = local_app(url)
    at = datetime.fromisoformat(at_iso)
    with app.app_context():
        return sum(record_aggregate({'submissions': 1, 'unknown_outcome': 1}, at) and
                   record_aggregate({'validation_rejected': 1, 'unknown_outcome': -1}, at)
                   for _ in range(iterations))


class FunnelUnitTests(unittest.TestCase):
    def test_range_strict_dates_bounds_duplicate_and_unknown_arguments(self):
        today = date(2026, 11, 5)
        self.assertEqual(validated_range(MultiDict(), today), (date(2026, 10, 30), today))
        invalid = [MultiDict({'start': '2026-1-01'}), MultiDict({'end': '2026-02-30'}),
                   MultiDict({'start': '2026-11-05', 'end': '2026-11-04'}),
                   MultiDict({'end': '2026-11-06'}), MultiDict({'start': '2026-01-01'}),
                   MultiDict([('start', '2026-11-04'), ('start', '2026-11-05')]),
                   MultiDict({'token': 'must-not-be-accepted'})]
        for args in invalid:
            with self.subTest(keys=list(args)), self.assertRaises(ValueError):
                validated_range(args, today)

    def test_madrid_dst_days_are_23_and_25_hours(self):
        for day, hours in [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)]:
            start, end = utc_bounds(day, day)
            self.assertEqual((end - start).total_seconds(), hours * 3600)

    def test_no_data_is_null_not_fabricated_zero(self):
        days = daily_payload(date(2026, 11, 4), date(2026, 11, 5), None, None, [], [], {})
        self.assertTrue(all(value is None for day in days for value in day['live'].values()))
        self.assertTrue(all(day['health'] == 'unknown' for day in days))

    def test_sql_is_whitelisted_and_atomic(self):
        sql = str(increment_sql({'submissions': 1, 'unknown_outcome': 1}))
        self.assertIn('ON CONFLICT (day) DO UPDATE', sql)
        self.assertIn('submissions = vip_funnel_daily.submissions + :submissions', sql)
        self.assertIn('first_submission_at', sql)
        with self.assertRaises(ValueError):
            increment_sql({'email': 1})
        result_sql = str(increment_sql({'unknown_outcome': -1, 'successful_signup': 1}))
        self.assertIn('unknown_outcome > 0', result_sql)
        self.assertNotIn('INSERT', result_sql)

    def test_new_storage_schema_contains_no_identity_columns(self):
        sql = (ROOT / 'migrations/20261105_vip_funnel_daily.sql').read_text()
        for forbidden in ('email', 'source_ip', 'user_agent', 'fingerprint', 'referrer', 'signup_id', 'token'):
            self.assertNotIn(forbidden, sql.lower())
        self.assertNotIn('vip_funnel_daily', db.metadata.tables)

    def test_anonymous_expired_invalid_and_inactive_auth(self):
        app = local_app()
        with app.app_context():
            db.create_all()
            inactive = AdminUser(microsoft_id='inactive-fixture', email='inactive@example.invalid', name='Fixture', is_active=False)
            db.session.add(inactive)
            db.session.commit()
            inactive_token = generate_admin_token(inactive)
        client = app.test_client()
        import jwt
        expired = jwt.encode({'sub': 'fixture', 'exp': datetime.now(timezone.utc) - timedelta(seconds=10)}, os.environ['JWT_SECRET'], algorithm='HS256')
        for headers, expected in [({}, 401), ({'Authorization': 'Bearer invalid'}, 401),
                                  ({'Authorization': f'Bearer {expired}'}, 401),
                                  ({'Authorization': f'Bearer {inactive_token}'}, 403)]:
            response = client.get('/api/admin/vip/funnel', headers=headers)
            self.assertEqual(response.status_code, expected)
            self.assertEqual(response.headers['Cache-Control'], 'no-store, private')
        self.assertEqual(client.get('/api/admin/vip/funnel?token=invalid').status_code, 401)

    def test_write_failure_does_not_rollback_signup_and_preserves_hotfix(self):
        app = local_app()
        with app.app_context():
            db.create_all()
        vip_routes._vip_rate_store.clear()
        with patch('src.services.vip_funnel.record_aggregate', return_value=False), \
                patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None)), \
                patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None)):
            response = app.test_client().post('/api/vip/signup', json={
                'email': 'signup@example.invalid', 'marketing_consent': True})
        self.assertEqual(response.status_code, 201)
        with app.app_context():
            signup = VipSignup.query.one()
            self.assertEqual((signup.klaviyo_subscription_status, signup.klaviyo_event_status), ('accepted', 'accepted'))

    def test_storage_exception_never_touches_business_session(self):
        app = local_app()
        with app.app_context(), patch.object(db.session, 'rollback') as rollback:
            self.assertFalse(record_aggregate({'pageviews': 1}))  # SQLite is not silently used in production.
            rollback.assert_not_called()

    def test_auth_lookup_failure_is_generic_not_raw_exception(self):
        app = local_app()
        import jwt
        token = jwt.encode({'sub': 'fixture'}, os.environ['JWT_SECRET'], algorithm='HS256')
        with app.app_context(), patch('sqlalchemy.orm.query.Query.first', side_effect=RuntimeError('NOT-TO-BE-RETURNED')):
            response = app.test_client().get('/api/admin/vip/funnel', headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('NOT-TO-BE-RETURNED', response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'], 'no-store, private')


@unittest.skipUnless(DB_URL, 'Disposable PostgreSQL Unix socket URL not provided')
class FunnelPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(DB_URL)
        if not url.drivername.startswith('postgresql') or not str(url.query.get('host', '')).startswith('/') or not url.database.endswith('_tests'):
            raise RuntimeError('Refusing non-local or non-test database')
        cls.app = local_app(DB_URL)
        with cls.app.app_context():
            db.create_all()
            with db.engine.begin() as conn:
                migration = (ROOT / 'migrations/20261105_vip_funnel_daily.sql').read_text()
                conn.exec_driver_sql(migration)

    def setUp(self):
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Network blocked in tests'))
        self.network.start()
        self.client = self.app.test_client()
        vip_routes._vip_rate_store.clear()
        with self.app.app_context():
            with db.engine.begin() as conn:
                conn.execute(text('TRUNCATE vip_funnel_daily, vip_signups, admin_users RESTART IDENTITY'))
            admin = AdminUser(microsoft_id='admin-fixture', email='admin@example.invalid', name='Fixture', role='admin', is_active=True)
            db.session.add(admin)
            db.session.commit()
            self.headers = {'Authorization': 'Bearer ' + generate_admin_token(admin)}

    def tearDown(self):
        self.network.stop()

    def submit(self, consent=True):
        return self.client.post('/api/vip/signup', json={'email': 'signup@example.invalid', 'marketing_consent': consent})

    def counts(self):
        with self.app.app_context(), db.engine.connect() as conn:
            return dict(conn.execute(text('SELECT * FROM vip_funnel_daily ORDER BY day DESC LIMIT 1')).mappings().one())

    def test_all_rejections_and_hotfix_classification_close_outcome_sum(self):
        self.assertEqual(self.submit(False).status_code, 400)
        for error in vip_routes._VIP_INELIGIBLE_ERRORS:
            with patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(False, error)), \
                    patch('src.routes.vip_routes.klaviyo_track_vip_signup') as event:
                self.assertEqual(self.submit().status_code, 502)
                event.assert_not_called()
        with patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(False, 'LOOKUP_FAILED')):
            self.assertEqual(self.submit().status_code, 502)
        self.assertEqual(self.submit().status_code, 429)
        counts = self.counts()
        self.assertEqual(counts['submissions'], 6)
        self.assertEqual(counts['validation_rejected'], 1)
        self.assertEqual(counts['ineligible'], 3)
        self.assertEqual(counts['technical_failure'], 1)
        self.assertEqual(counts['rate_limited'], 1)
        self.assertEqual(counts['unknown_outcome'], 0)

    def test_success_requires_event_accepted(self):
        with patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None)), \
                patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(False, 'EVENT_FAILED')):
            self.assertEqual(self.submit().status_code, 502)
        with patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None)), \
                patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None)):
            self.assertEqual(self.submit().status_code, 201)
        counts = self.counts()
        self.assertEqual(counts['successful_signup'], 1)
        self.assertEqual(counts['technical_failure'], 1)
        self.assertEqual(counts['submissions'], 2)

    def test_recovered_initial_write_failure_reconstructs_one_submission_with_health_marker(self):
        real_record = record_aggregate
        calls = 0
        def fail_first(deltas, at=None):
            nonlocal calls
            calls += 1
            return False if calls == 1 else real_record(deltas, at)
        with patch('src.services.vip_funnel.record_aggregate', side_effect=fail_first), \
                patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None)), \
                patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None)):
            self.assertEqual(self.submit().status_code, 201)
        counts = self.counts()
        self.assertEqual(counts['submissions'], 1)
        self.assertEqual(counts['successful_signup'], 1)
        self.assertEqual(counts['unknown_outcome'], 0)
        self.assertEqual(counts['telemetry_failure'], 1)

    def test_lost_final_write_leaves_unknown_instead_of_fabricating_success(self):
        real_record = record_aggregate
        def fail_result(deltas, at=None):
            return False if 'successful_signup' in deltas else real_record(deltas, at)
        with patch('src.services.vip_funnel.record_aggregate', side_effect=fail_result), \
                patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None)), \
                patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None)):
            self.assertEqual(self.submit().status_code, 201)
        counts = self.counts()
        self.assertEqual(counts['submissions'], 1)
        self.assertEqual(counts['successful_signup'], 0)
        self.assertEqual(counts['unknown_outcome'], 1)
        self.assertEqual(counts['telemetry_failure'], 1)
        with self.app.app_context():
            self.assertEqual(VipSignup.query.one().klaviyo_event_status, 'accepted')

    def test_honeypot_and_unclassified_2xx_contract(self):
        app = Flask('contract')
        app.config.update(SQLALCHEMY_DATABASE_URI=DB_URL, SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(app)
        install_vip_funnel(app)
        @app.route('/api/vip/signup', methods=['POST'])
        def early():
            from flask import request
            if request.json.get('trap'):
                mark_vip_outcome('honeypot')
            return jsonify({'neutral': True})
        client = app.test_client()
        client.post('/api/vip/signup', json={'trap': True})
        client.post('/api/vip/signup', json={})
        counts = self.counts()
        self.assertEqual(counts['honeypot'], 1)
        self.assertEqual(counts['unknown_outcome'], 1)
        self.assertEqual(counts['successful_signup'], 0)

    def test_pageview_empty_write_no_pii_and_unknown_days(self):
        self.assertEqual(self.client.get('/api/vip/pageview').status_code, 405)
        self.assertEqual(self.client.post('/api/vip/pageview', json={'email': 'forbidden@example.invalid'}).status_code, 400)
        self.assertEqual(self.client.post('/api/vip/pageview?query=forbidden').status_code, 400)
        self.assertEqual(self.client.post('/api/vip/pageview', headers={'User-Agent': 'NOT-STORED', 'Referer': 'https://not-stored.invalid'}).status_code, 202)
        response = self.client.get('/api/admin/vip/funnel', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        encoded = json.dumps(response.get_json())
        for forbidden in ('@example', 'NOT-STORED', 'not-stored.invalid', 'Authorization', self.headers['Authorization']):
            self.assertNotIn(forbidden, encoded)
        self.assertIsNotNone(response.json['pageviews_coverage_start'])
        self.assertIsNone(response.json['coverage_start'])
        self.assertIsNone(response.json['daily'][-1]['live']['submissions'])
        self.assertIsNone(response.json['daily'][0]['live']['pageviews'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store, private')

    def test_private_ranges_and_history_are_source_scoped_without_live_duplicate(self):
        today = datetime.now(timezone.utc).astimezone(MADRID).date()
        observation = datetime.now(timezone.utc)
        with self.app.app_context():
            for source, at in [('vip_black_friday_2026', observation - timedelta(hours=1)),
                               ('other', observation - timedelta(hours=1))]:
                db.session.add(VipSignup(email='historical@example.invalid', source_ip='192.0.2.1',
                    source=source, consent_marketing=True, created_at=at.replace(tzinfo=None),
                    klaviyo_subscription_status='accepted', klaviyo_event_status='accepted'))
            db.session.commit()
            self.assertTrue(record_aggregate({'submissions': 1, 'unknown_outcome': 1}, observation))
            self.assertTrue(record_aggregate({'successful_signup': 1, 'unknown_outcome': -1}, observation))
        response = self.client.get('/api/admin/vip/funnel', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sum(day['historical_persisted']['successful_signup'] for day in response.json['daily'] if day['historical_persisted']), 1)
        self.assertEqual(sum(day['live']['successful_signup'] or 0 for day in response.json['daily']), 1)
        for query in ['start=invalid', 'end=2099-01-01', 'token=not-allowed', 'start=2026-01-01&start=2026-01-02']:
            self.assertEqual(self.client.get('/api/admin/vip/funnel?' + query, headers=self.headers).status_code, 400)
        self.assertEqual(response.json['coverage_start'], observation.isoformat())

    def test_concurrent_multi_process_upserts_have_no_lost_updates(self):
        at = datetime.now(timezone.utc).isoformat()
        workers, iterations = 4, 30
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            results = list(pool.map(increment_worker, [DB_URL] * workers, [iterations] * workers, [at] * workers))
        self.assertEqual(sum(results), workers * iterations)
        counts = self.counts()
        self.assertEqual(counts['submissions'], workers * iterations)
        self.assertEqual(counts['validation_rejected'], workers * iterations)
        self.assertEqual(counts['unknown_outcome'], 0)

    def test_missing_table_is_unavailable_and_signup_still_succeeds(self):
        with self.app.app_context(), db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE vip_funnel_daily RENAME TO unavailable_funnel_daily'))
        try:
            response = self.client.get('/api/admin/vip/funnel', headers=self.headers)
            self.assertEqual(response.status_code, 503)
            self.assertIsNone(response.json['daily'])
            with patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None)), \
                    patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None)):
                self.assertEqual(self.submit().status_code, 201)
            with self.app.app_context():
                self.assertEqual(VipSignup.query.count(), 1)
        finally:
            with self.app.app_context(), db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE unavailable_funnel_daily RENAME TO vip_funnel_daily'))


if __name__ == '__main__':
    unittest.main()
