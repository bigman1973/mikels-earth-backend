"""Finite synthetic PostgreSQL tests. No provider or production DB access."""
import contextlib
import io
import json
import os
import sys
import threading
import time
import unittest
import uuid
from datetime import timedelta, timezone
from unittest.mock import Mock, PropertyMock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.jobs.bf26_expiration import BF26_JOB_LOCK, main, run_locked_once
from src.services.bf26_checkout_cutoff import BF26_CLOSE_AT, BF26_CLOSE_TIMESTAMP

CLOSE = BF26_CLOSE_AT.astimezone(timezone.utc)


class EntryPointTests(unittest.TestCase):
    def invoke(self, arguments, closed, environment=None):
        output = io.StringIO()
        with patch('sys.argv', ['bf26_expiration'] + arguments), \
                patch('src.jobs.bf26_expiration.is_closed', return_value=closed), \
                patch.dict(os.environ, environment or {}, clear=True), \
                contextlib.redirect_stdout(output), \
                patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            code = main()
        return code, json.loads(output.getvalue())

    def test_before_cutoff_needs_no_keys_or_db(self):
        with patch('src.models.user.db.init_app') as initialize, \
                patch('stripe.checkout.Session.list') as provider:
            code, result = self.invoke(['--execute'], False)
        self.assertEqual((code, result), (0, {'before_cutoff': True, 'provider_calls': 0}))
        initialize.assert_not_called()
        provider.assert_not_called()

    def test_missing_configuration_is_safe_aggregate(self):
        self.assertEqual(self.invoke(['--execute'], True),
                         (2, {'configuration_missing': True}))

    def test_non_postgresql_is_rejected_without_provider_or_raw_error(self):
        with patch('stripe.checkout.Session.list') as provider:
            code, result = self.invoke(['--execute'], True, {
                'DATABASE_URL': 'sqlite:///:memory:', 'STRIPE_SECRET_KEY': 'synthetic-only',
            })
        self.assertEqual(code, 2)
        self.assertEqual(result, {'job_failed': True, 'retry_required': True})
        provider.assert_not_called()

    def test_no_execute_or_invalid_page_count_cannot_act(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for arguments in ([], ['--execute', '--max-pages', '0']):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as error:
                    self.invoke(arguments, True)
                self.assertEqual(error.exception.code, 2)

    def test_postcutoff_resources_closed_even_on_sweep_failure(self):
        from src.models.user import db
        engine = Mock()
        with patch('src.jobs.bf26_expiration.run_locked_once', side_effect=RuntimeError('hidden')), \
                patch.object(db, 'init_app'), patch.object(db, 'session') as session, \
                patch('flask_sqlalchemy.SQLAlchemy.engine', new_callable=PropertyMock, return_value=engine):
            code, result = self.invoke(['--execute'], True, {
                'DATABASE_URL': 'postgresql://synthetic@localhost/synthetic',
                'STRIPE_SECRET_KEY': 'synthetic-only',
            })
        self.assertEqual((code, result), (2, {'job_failed': True, 'retry_required': True}))
        session.remove.assert_called_once()
        engine.dispose.assert_called_once()


class PostgreSQLOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get('VIP_FUNNEL_TEST_DATABASE_URL', '')
        parsed = make_url(url)
        if (parsed.get_backend_name() != 'postgresql' or
                not parsed.query.get('host', '').startswith('/tmp/') or
                parsed.database != 'vip_funnel_integrated_tests'):
            raise RuntimeError('Disposable Unix-socket PostgreSQL fixture is required')
        cls.schema = 'bf26_job_test_' + uuid.uuid4().hex
        cls.engine = create_engine(url, pool_size=4)
        with cls.engine.begin() as connection:
            connection.execute(text('CREATE SCHEMA ' + cls.schema))
        # Import registers mappings only; does not initialize src.main or any DB.
        from src.models.order import Order
        from src.models.stock import StockReservation
        from src.models.web_product import WebProduct
        cls.test_engine = create_engine(url, pool_size=4, connect_args={
            'options': '-c search_path=' + cls.schema,
        })
        cls.Session = sessionmaker(bind=cls.test_engine)
        with cls.test_engine.begin() as connection:
            connection.execute(text('CREATE TABLE web_products (id integer PRIMARY KEY, sku text)'))
            connection.execute(text('CREATE TABLE stock_reservations '
                                    '(id integer PRIMARY KEY, product_id integer, '
                                    'stripe_session_id text, created_at timestamp, status text)'))
            connection.execute(text("INSERT INTO web_products VALUES (1, 'BF26-TEMP6'), (2, 'NORMAL')"))
            connection.execute(text("INSERT INTO stock_reservations VALUES "
                                    "(1, 1, 'cs_fixture_legacy', :created, 'released'), "
                                    "(2, 1, 'cs_fixture_legacy', :created, 'expired'), "
                                    "(3, 2, 'cs_fixture_normal', :created, 'active'), "
                                    "(4, 1, '', :created, 'active'), "
                                    "(5, 1, NULL, :created, 'active')"),
                               {'created': CLOSE.replace(tzinfo=None) - timedelta(hours=1)})

    @classmethod
    def tearDownClass(cls):
        cls.test_engine.dispose()
        with cls.engine.begin() as connection:
            connection.execute(text('DROP SCHEMA ' + cls.schema + ' CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        self.api = Mock()
        self.api.list.return_value = {'data': [], 'has_more': False}
        self.api.expire.return_value = {'status': 'expired'}
        self.api.retrieve.return_value = {'status': 'expired'}
        self.session = self.Session()
        self.addCleanup(self.session.close)

    def test_before_cutoff_no_database_or_provider_call(self):
        database = Mock()
        self.assertEqual(run_locked_once(self.api, database,
                                         now=CLOSE - timedelta(microseconds=1)),
                         {'before_cutoff': True, 'provider_calls': 0})
        self.assertEqual(database.mock_calls, [])
        self.assertEqual(self.api.mock_calls, [])

    def test_main_end_to_end_uses_postgresql_lock_and_aggregate_only(self):
        url = make_url(os.environ['VIP_FUNNEL_TEST_DATABASE_URL'])
        url = url.update_query_dict({'options': '-c search_path=' + self.schema})
        output, errors = io.StringIO(), io.StringIO()
        def noisy_provider(_identity):
            print('synthetic-sensitive-provider-body')
            print('synthetic-sensitive-provider-body', file=sys.stderr)
            return {'status': 'expired'}
        with patch('sys.argv', ['bf26_expiration', '--execute']), \
                patch.dict(os.environ, {'DATABASE_URL': str(url),
                                       'STRIPE_SECRET_KEY': 'synthetic-only'}, clear=True), \
                patch('src.jobs.bf26_expiration.is_closed', return_value=True), \
                patch('src.jobs.bf26_expiration.utc_now', return_value=CLOSE), \
                patch('stripe.checkout.Session.list', return_value={'data': [], 'has_more': False}), \
                patch('stripe.checkout.Session.expire', side_effect=noisy_provider) as expire, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['expired'], 1)
        self.assertNotIn('cs_fixture', output.getvalue())
        self.assertNotIn('synthetic-only', output.getvalue())
        self.assertNotIn('synthetic-sensitive-provider-body', output.getvalue())
        self.assertEqual(errors.getvalue(), '')
        expire.assert_called_once_with('cs_fixture_legacy')

    def test_only_bf_linkage_selected_distinct_even_when_released(self):
        result = run_locked_once(self.api, self.session, now=CLOSE)
        self.assertEqual((result['matched'], result['expired']), (1, 1))
        self.api.expire.assert_called_once_with('cs_fixture_legacy')
        self.assertNotIn('cs_fixture', json.dumps(result))
        with self.test_engine.connect() as connection:
            statuses = connection.execute(text('SELECT status FROM stock_reservations ORDER BY id')).scalars().all()
        self.assertEqual(statuses, ['released', 'expired', 'active', 'active', 'active'])

    def test_transaction_is_read_only_and_can_see_postcutoff_orphan(self):
        def expire(_identity):
            self.assertEqual(self.session.execute(text('SHOW transaction_read_only')).scalar(), 'on')
            return {'status': 'complete'}
        self.api.expire.side_effect = expire
        self.api.list.return_value = {'data': [
            {'id': 'cs_fixture_orphan', 'mode': 'payment', 'metadata': {'purchase_campaign': 'BF26'}},
            {'id': 'cs_fixture_notbf', 'mode': 'payment', 'metadata': {}},
        ], 'has_more': False}
        result = run_locked_once(self.api, self.session, now=CLOSE + timedelta(minutes=10))
        self.assertEqual((result['matched'], result['complete']), (2, 2))
        params = self.api.list.call_args.kwargs
        self.assertEqual(params['status'], 'open')
        self.assertNotIn('lt', params['created'])
        self.assertLessEqual(params['created']['gte'], BF26_CLOSE_TIMESTAMP)

    def test_busy_lock_requires_catchup_without_provider_and_retry_succeeds(self):
        with self.Session() as holder, holder.begin():
            acquired = holder.execute(text('SELECT pg_try_advisory_xact_lock(:n, :j)'),
                                      {'n': BF26_JOB_LOCK[0], 'j': BF26_JOB_LOCK[1]}).scalar()
            self.assertTrue(acquired)
            result = run_locked_once(self.api, self.session, now=CLOSE)
            self.assertEqual(result, {'lock_busy': True, 'retry_required': True})
            self.assertEqual(self.api.mock_calls, [])
        result = run_locked_once(self.api, self.session, now=CLOSE)
        self.assertEqual(result['expired'], 1)

    def test_concurrent_replicas_only_one_calls_provider(self):
        entered, release = threading.Event(), threading.Event()
        outputs, errors = [], []
        def expire(_identity):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Fixture concurrency timeout')
            return {'status': 'expired'}
        self.api.expire.side_effect = expire
        def first_replica():
            try:
                with self.Session() as session:
                    outputs.append(run_locked_once(self.api, session, now=CLOSE))
            except Exception as error:
                errors.append(type(error).__name__)
        thread = threading.Thread(target=first_replica)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            other = Mock()
            self.assertEqual(run_locked_once(other, self.session, now=CLOSE),
                             {'lock_busy': True, 'retry_required': True})
            self.assertEqual(other.mock_calls, [])
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(outputs[0]['expired'], 1)

    def test_failure_releases_lock_and_next_run_recovers(self):
        with patch('src.jobs.bf26_expiration.local_bf26_session_ids', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                run_locked_once(self.api, self.session, now=CLOSE)
        result = run_locked_once(self.api, self.session, now=CLOSE)
        self.assertEqual(result['expired'], 1)

    def test_connection_loss_releases_lock_without_checkpoint(self):
        connection = self.test_engine.connect()
        transaction = connection.begin()
        self.assertTrue(connection.execute(text('SELECT pg_try_advisory_xact_lock(:n, :j)'),
                                           {'n': BF26_JOB_LOCK[0], 'j': BF26_JOB_LOCK[1]}).scalar())
        connection.invalidate()
        transaction.rollback()
        connection.close()
        for _ in range(20):
            result = run_locked_once(self.api, self.session, now=CLOSE)
            if not result.get('lock_busy'):
                break
            time.sleep(0.05)
        self.assertEqual(result['expired'], 1)

    def test_repeated_completed_payment_is_retained_and_provider_failure_retried(self):
        self.api.expire.side_effect = RuntimeError('synthetic race')
        self.api.retrieve.return_value = {'status': 'complete'}
        for _ in range(2):
            result = run_locked_once(self.api, self.session, now=CLOSE + timedelta(days=1))
            self.assertEqual((result['complete'], result['retry']), (1, 0))
        self.api.retrieve.return_value = {'status': 'open'}
        result = run_locked_once(self.api, self.session, now=CLOSE)
        self.assertEqual(result['retry'], 1)
        self.api.expire.side_effect = None
        result = run_locked_once(self.api, self.session, now=CLOSE)
        self.assertEqual(result['expired'], 1)
        self.assertEqual({call[0] for call in self.api.mock_calls}, {'list', 'expire', 'retrieve'})


if __name__ == '__main__':
    unittest.main()
