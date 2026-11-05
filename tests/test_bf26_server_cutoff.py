"""Local-only cutoff tests: synthetic DB, mocked Stripe/Klaviyo, blocked network."""
import importlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.services.bf26_checkout_cutoff import (
    BF26_CLOSE_AT, BF26_CLOSE_TIMESTAMP, BF26_LAST_VALID, BF26CampaignClosed,
    BF26_METADATA_KEY, checkout_policy, expire_open_session, is_closed,
)
from src.jobs.bf26_expiration import local_bf26_session_ids, run_once
import test_bf26_campaign_packs as bf_fixture
from src.models.checkout_tax_snapshot import CheckoutTaxSnapshot
from src.models.order import Order
from src.models.stock import StockMovement, StockReservation
from src.models.user import db

CLOSE = BF26_CLOSE_AT.astimezone(timezone.utc)


def stripe_session(session_id, campaign='BF26', status='open', mode='payment'):
    return {'id': session_id, 'status': status, 'mode': mode,
            'metadata': {BF26_METADATA_KEY: campaign}}


class PolicyTests(unittest.TestCase):
    def test_madrid_exact_last_second_and_exclusive_midnight(self):
        self.assertEqual(BF26_LAST_VALID.astimezone(timezone.utc).isoformat(),
                         '2026-11-30T22:59:59+00:00')
        self.assertEqual(CLOSE.isoformat(), '2026-11-30T23:00:00+00:00')
        for delta in (1, 0.001):
            self.assertFalse(is_closed(CLOSE - timedelta(seconds=delta)))
        for delta in (0, 0.001, 1):
            self.assertTrue(is_closed(CLOSE + timedelta(seconds=delta)))
            with self.assertRaises(BF26CampaignClosed):
                checkout_policy(CLOSE + timedelta(seconds=delta))

    def test_exact_30_minute_expiry_and_sweep_boundary_preserve_full_monday(self):
        for seconds_remaining in (86401, 86400, 3600, 1861, 1860, 1859,
                                  1801, 1800, 1799, 60, 2, 1):
            now = CLOSE - timedelta(seconds=seconds_remaining)
            with self.subTest(seconds_remaining=seconds_remaining):
                policy = checkout_policy(now)
                self.assertEqual(policy['expires_at'], now.timestamp() + 1800)
                self.assertIsInstance(policy['expires_at'], int)
                self.assertEqual(policy['metadata'], {
                    BF26_METADATA_KEY: 'BF26',
                    'purchase_campaign_closes_at': str(BF26_CLOSE_TIMESTAMP),
                    'purchase_campaign_requires_sweep': str(seconds_remaining < 1800).lower(),
                })
                if seconds_remaining == 1800:
                    self.assertEqual(policy['expires_at'], BF26_CLOSE_TIMESTAMP)

    def test_fractional_last_seconds_use_unix_integer_without_roundup_margin(self):
        for seconds_remaining in (1.001, 0.999, 0.001, 0.000001):
            now = CLOSE - timedelta(seconds=seconds_remaining)
            with self.subTest(seconds_remaining=seconds_remaining):
                policy = checkout_policy(now)
                self.assertEqual(policy['expires_at'], int(now.timestamp()) + 1800)
                self.assertIsInstance(policy['expires_at'], int)
                self.assertEqual(policy['metadata']['purchase_campaign_requires_sweep'], 'true')

    def test_naive_server_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            checkout_policy(datetime(2026, 11, 30))


class NativeJobTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.list.return_value = {'data': [], 'has_more': False}
        self.api.expire.return_value = {'status': 'expired'}
        self.api.retrieve.return_value = {'status': 'expired'}
        self.network = patch('socket.socket.connect', side_effect=AssertionError('Network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_import_has_no_handlers_jobs_or_provider_calls(self):
        with patch('stripe.checkout.Session.list') as provider:
            importlib.reload(importlib.import_module('src.jobs.bf26_expiration'))
            provider.assert_not_called()

    def test_before_cutoff_is_noop_even_with_legacy_ids(self):
        result = run_once(self.api, legacy_session_ids=['cs_fixture'], now=CLOSE-timedelta(microseconds=1))
        self.assertTrue(result['before_cutoff'])
        self.assertEqual(result['matched'], 0)
        self.assertEqual(self.api.mock_calls, [])

    def test_paginated_catchup_filters_only_marked_payment_and_deduplicates(self):
        self.api.list.side_effect = [
            {'data': [stripe_session('cs_bf'), stripe_session('cs_normal', 'ordinary'),
                      stripe_session('cs_sub', mode='subscription')], 'has_more': True},
            {'data': [stripe_session('cs_orphan')], 'has_more': False},
        ]
        result = run_once(self.api, legacy_session_ids=['cs_bf', 'cs_legacy'], now=CLOSE)
        self.assertEqual((result['matched'], result['expired'], result['retry']), (3, 3, 0))
        self.assertEqual({call.args[0] for call in self.api.expire.call_args_list},
                         {'cs_bf', 'cs_legacy', 'cs_orphan'})
        self.assertEqual(self.api.list.call_args.kwargs['starting_after'], 'cs_sub')
        self.assertNotIn('lt', self.api.list.call_args.kwargs['created'])
        for call in self.api.expire.call_args_list:
            self.assertNotIn('idempotency_key', call.kwargs)

    def test_expire_race_complete_never_cancels_or_refunds(self):
        self.api.expire.side_effect = RuntimeError('synthetic race')
        self.api.retrieve.return_value = {'status': 'complete'}
        self.assertEqual(expire_open_session(self.api, 'cs_fixture'), 'complete')
        self.assertEqual([c[0] for c in self.api.mock_calls], ['expire', 'retrieve'])

    def test_repeat_expired_and_retry_network_failure(self):
        self.api.expire.side_effect = RuntimeError('synthetic provider error')
        result = run_once(self.api, legacy_session_ids=['cs_fixture'], now=CLOSE+timedelta(hours=4))
        self.assertEqual(result['expired'], 1)
        self.api.retrieve.return_value = {'status': 'open'}
        result = run_once(self.api, legacy_session_ids=['cs_fixture'], now=CLOSE+timedelta(hours=4))
        self.assertEqual(result['retry'], 1)
        self.api.retrieve.side_effect = RuntimeError('synthetic failure')
        self.assertEqual(expire_open_session(self.api, 'cs_fixture'), 'retry')

    def test_scan_failure_and_page_cap_are_visible_for_orchestrator_retry(self):
        self.api.list.side_effect = RuntimeError('no raw text exposed')
        result = run_once(self.api, now=CLOSE)
        self.assertTrue(result['scan_failed'])
        self.assertNotIn('no raw text', str(result))
        self.api.list.side_effect = None
        self.api.list.return_value = {'data': [stripe_session('cs_bf')], 'has_more': True}
        result = run_once(self.api, now=CLOSE, max_pages=1)
        self.assertTrue(result['page_limit_reached'])


class CheckoutBoundaryTests(unittest.TestCase):
    # Reuse fixture setup/helpers only: do not duplicate/inherit its test methods.
    setUp = bf_fixture.BF26LocalCheckoutTests.setUp
    tearDown = bf_fixture.BF26LocalCheckoutTests.tearDown
    mock = bf_fixture.BF26LocalCheckoutTests.mock
    activate_fixture = bf_fixture.BF26LocalCheckoutTests.activate_fixture
    item = bf_fixture.BF26LocalCheckoutTests.item
    checkout = bf_fixture.BF26LocalCheckoutTests.checkout
    complete_local_payment = bf_fixture.BF26LocalCheckoutTests.complete_local_payment

    def freeze(self, now):
        return patch('src.services.bf26_checkout_cutoff.utc_now', return_value=now)

    def prepare(self):
        self.mock('socket.socket.connect', side_effect=AssertionError('Network forbidden'))
        self.activate_fixture()
        return self.item('BF26-TEMP6')

    def test_exact_midnight_rejects_direct_stale_mixed_cart_before_external_calls(self):
        item = self.prepare()
        for changes in ({}, {'sku': ''}, {'slug': ''}):
            with self.freeze(CLOSE):
                response = self.checkout([dict(item, **changes), self.item('NORMAL-LOCAL')])
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()['error'], 'BF26_CAMPAIGN_CLOSED')
        self.holded.assert_not_called()
        self.session.assert_not_called()
        with self.app.app_context():
            self.assertEqual(StockReservation.query.count(), 0)
            self.assertEqual(StockMovement.query.count(), 0)

    def test_last_second_opens_card_only_with_sweep_and_snapshot(self):
        item = self.prepare()
        now = CLOSE-timedelta(seconds=1)
        with self.freeze(now):
            response = self.checkout([item])
        self.assertEqual(response.status_code, 200)
        params = self.session.call_args.kwargs
        self.assertEqual(params['payment_method_types'], ['card'])
        self.assertEqual(params['expires_at'], now.timestamp() + 1800)
        self.assertEqual(params['metadata']['purchase_campaign_requires_sweep'], 'true')
        self.assertTrue(params['idempotency_key'].startswith('bf26-create-'))
        with self.app.app_context():
            allocation = StockReservation.query.one()
            self.assertEqual(allocation.expires_at, datetime.utcfromtimestamp(params['expires_at']))
            self.assertEqual(CheckoutTaxSnapshot.query.one().expires_at, allocation.expires_at)
            self.assertEqual(StockMovement.query.count(), 0)

    def test_last_30_minutes_and_last_seconds_request_exact_ttl_for_mixed_cart(self):
        item = self.prepare()
        for seconds_remaining in (1801, 1800, 1799, 60, 2, 1, 0.001):
            now = CLOSE-timedelta(seconds=seconds_remaining)
            with self.subTest(seconds_remaining=seconds_remaining), self.freeze(now):
                response = self.checkout([item, self.item('NORMAL-LOCAL')])
                self.assertEqual(response.status_code, 200)
                params = self.session.call_args.kwargs
                self.assertEqual(params['expires_at'], int(now.timestamp()) + 1800)
                self.assertEqual(params['metadata']['purchase_campaign_requires_sweep'],
                                 str(seconds_remaining < 1800).lower())
                self.assertEqual(params['payment_method_types'], ['card'])

    def test_stripe_minimum_validation_fails_closed_without_longer_or_default_retry(self):
        from stripe._error import InvalidRequestError

        item = self.prepare()
        self.session.side_effect = InvalidRequestError('synthetic expires_at minimum rejection',
                                                       param='expires_at')
        now = CLOSE-timedelta(seconds=1)
        with self.freeze(now):
            response = self.checkout([item])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['error'], 'BF26_CHECKOUT_UNAVAILABLE')
        self.assertNotIn('url', response.get_json())
        self.assertNotIn('sessionId', response.get_json())
        self.assertNotIn('synthetic', str(response.get_json()))
        self.session.assert_called_once()
        params = self.session.call_args.kwargs
        self.assertEqual(params['expires_at'], now.timestamp() + 1800)
        self.assertEqual(params['payment_method_types'], ['card'])
        with self.app.app_context():
            self.assertEqual(CheckoutTaxSnapshot.query.count(), 1)
            self.assertEqual(StockReservation.query.one().status, 'active')
            self.assertEqual(Order.query.count(), 0)
            self.assertEqual(StockMovement.query.count(), 0)

    def test_normal_cart_keeps_existing_behaviour_after_cutoff(self):
        self.prepare()
        with self.freeze(CLOSE):
            response = self.checkout([self.item('NORMAL-LOCAL')])
        self.assertEqual(response.status_code, 200)
        params = self.session.call_args.kwargs
        self.assertNotIn('expires_at', params)
        self.assertNotIn(BF26_METADATA_KEY, params['metadata'])

    def test_request_crosses_cutoff_during_validation_never_opens_stripe(self):
        item = self.prepare()
        clock = {'now': CLOSE-timedelta(seconds=2)}
        original = self.holded.side_effect
        def slow_validation():
            clock['now'] = CLOSE
            return original()
        self.holded.side_effect = slow_validation
        with patch('src.services.bf26_checkout_cutoff.utc_now', side_effect=lambda: clock['now']):
            response = self.checkout([item])
        self.assertEqual(response.status_code, 409)
        self.session.assert_not_called()

    def test_request_crosses_cutoff_inside_stripe_no_payable_url_exposed(self):
        item = self.prepare()
        clock = {'now': CLOSE-timedelta(seconds=1)}
        def slow_stripe(**kwargs):
            with self.app.app_context():
                self.assertEqual(CheckoutTaxSnapshot.query.count(), 1)
            clock['now'] = CLOSE
            return SimpleNamespace(id='cs_crossed', url='https://checkout.stripe.test/crossed')
        self.session.side_effect = slow_stripe
        expire = self.mock('src.routes.stripe_routes.stripe.checkout.Session.expire', return_value={'status': 'expired'})
        with patch('src.services.bf26_checkout_cutoff.utc_now', side_effect=lambda: clock['now']):
            response = self.checkout([item])
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('url', response.get_json())
        expire.assert_called_once()
        with self.app.app_context():
            self.assertEqual(CheckoutTaxSnapshot.query.count(), 1)
            self.assertEqual(StockReservation.query.one().status, 'active')
            self.assertEqual(StockMovement.query.count(), 0)

    def test_timeout_retains_snapshot_for_orphan_paid_event_and_sweep(self):
        item = self.prepare()
        self.session.side_effect = RuntimeError('ambiguous synthetic API timeout')
        with self.freeze(CLOSE-timedelta(seconds=1)):
            response = self.checkout([item])
        self.assertEqual(response.status_code, 503)
        params = self.session.call_args.kwargs
        with self.app.app_context():
            self.assertEqual(CheckoutTaxSnapshot.query.count(), 1)
            self.assertEqual(StockReservation.query.one().status, 'active')
        with self.freeze(CLOSE+timedelta(days=1)):
            self.complete_local_payment('cs_orphan_paid', params)
        with self.app.app_context():
            self.assertEqual(Order.query.count(), 1)

    def test_job_retains_snapshots_and_allocations_for_late_paid_webhook(self):
        item = self.prepare()
        with self.freeze(CLOSE-timedelta(hours=1)):
            response = self.checkout([item])
        self.assertEqual(response.status_code, 200)
        params = self.session.call_args.kwargs
        self.assertEqual(params['expires_at'], (CLOSE-timedelta(hours=1)).timestamp() + 1800)
        api = Mock()
        api.expire.return_value = {'status': 'complete'}
        api.list.return_value = {'data': [], 'has_more': False}
        with self.app.app_context():
            StockReservation.query.one().created_at = CLOSE.replace(tzinfo=None)-timedelta(hours=1)
            db.session.commit()
            ids = local_bf26_session_ids()
            self.assertEqual(ids, ['cs_local_bf26_1'])
            result = run_once(api, legacy_session_ids=ids, now=CLOSE+timedelta(days=1))
            self.assertEqual(result['complete'], 1)
            self.assertEqual(CheckoutTaxSnapshot.query.count(), 1)
            self.assertEqual(StockReservation.query.one().status, 'active')
            self.assertEqual(StockMovement.query.count(), 0)
            # Snapshot expiry is not a license to lose an already-paid order.
            snapshot = CheckoutTaxSnapshot.query.one()
            snapshot.expires_at = CLOSE.replace(tzinfo=None)-timedelta(minutes=1)
            db.session.commit()
        with self.freeze(CLOSE+timedelta(days=1)):
            self.complete_local_payment('cs_local_bf26_1', params)
        with self.app.app_context():
            self.assertEqual(Order.query.count(), 1)
            self.assertEqual(StockMovement.query.count(), 1)

    def test_paid_before_cutoff_delivered_days_late_survives_duplicate_event_ids(self):
        item = self.prepare()
        with self.freeze(CLOSE-timedelta(seconds=1)):
            self.assertEqual(self.checkout([item]).status_code, 200)
        params = self.session.call_args.kwargs
        product = params['line_items'][0]['price_data']['product_data']
        lines = [SimpleNamespace(amount_total=5970, quantity=1, description=product['name'],
                  price=SimpleNamespace(product=SimpleNamespace(metadata=product['metadata'])))]
        event = {'id': 'evt_valid_before', 'created': BF26_CLOSE_TIMESTAMP-1,
                 'type': 'checkout.session.completed', 'data': {'object': {
                    'id': 'cs_local_bf26_1', 'mode': 'payment', 'payment_status': 'paid',
                    'payment_intent': 'pi_fixture_late', 'amount_total': 5970,
                    'customer_details': {'email': bf_fixture.CUSTOMER['email']},
                    'metadata': params['metadata']}}}
        confirmation = self.mock('src.routes.stripe_routes.dispatch_order_confirmation', return_value=(True, None))
        self.mock('src.routes.stripe_routes.dispatch_order_notification', return_value=(True, None))
        self.mock('src.services.email_dispatcher.dispatch_post_purchase_event')
        self.mock('src.services.meta_conversions_api.dispatch_meta_purchase_event', return_value={'sent': False})
        with self.freeze(CLOSE+timedelta(days=3)), patch('src.routes.stripe_routes.stripe.Webhook.construct_event', return_value=event), patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items', return_value=SimpleNamespace(data=lines)):
            self.assertEqual(self.client.post('/api/stripe/webhook', data=b'{}').status_code, 200)
            self.assertTrue(self.client.post('/api/stripe/webhook', data=b'{}').get_json()['duplicate'])
            event['id'] = 'evt_distinct_same_session'
            self.assertTrue(self.client.post('/api/stripe/webhook', data=b'{}').get_json()['duplicate'])
        confirmation.assert_called_once()
        with self.app.app_context():
            self.assertEqual(Order.query.count(), 1)
            self.assertEqual(StockMovement.query.count(), 1)

    def test_bf_subscription_bypass_is_rejected_before_stripe_price(self):
        item = self.prepare()
        item['subscription_frequency'] = 'monthly'
        from test_bf26_campaign_packs import CUSTOMER
        response = self.client.post('/api/stripe/create-subscription-checkout', json={'item': item, 'customer_info': CUSTOMER})
        self.assertEqual(response.status_code, 422)
        self.stripe_price.assert_not_called()
        self.session.assert_not_called()

    def test_unpaid_completion_waits_for_late_async_success_and_deduplicates(self):
        item = self.prepare()
        with self.freeze(CLOSE-timedelta(seconds=1)):
            self.assertEqual(self.checkout([item]).status_code, 200)
        params = self.session.call_args.kwargs
        line = params['line_items'][0]
        product = line['price_data']['product_data']
        lines = [SimpleNamespace(amount_total=5970, quantity=1, description=product['name'],
                  price=SimpleNamespace(product=SimpleNamespace(metadata=product['metadata'])))]
        from test_bf26_campaign_packs import CUSTOMER
        event = {'id': 'evt_before_cutoff', 'created': BF26_CLOSE_TIMESTAMP-1,
                 'type': 'checkout.session.completed', 'data': {'object': {
                    'id': 'cs_local_bf26_1', 'mode': 'payment', 'payment_status': 'unpaid',
                    'payment_intent': 'pi_async_fixture', 'amount_total': 5970,
                    'customer_details': {'email': CUSTOMER['email']}, 'metadata': params['metadata']}}}
        confirmation = self.mock('src.routes.stripe_routes.dispatch_order_confirmation', return_value=(True, None))
        self.mock('src.routes.stripe_routes.dispatch_order_notification', return_value=(True, None))
        self.mock('src.services.email_dispatcher.dispatch_post_purchase_event')
        self.mock('src.services.meta_conversions_api.dispatch_meta_purchase_event', return_value={'sent': False})
        with self.freeze(CLOSE+timedelta(days=2)), patch('src.routes.stripe_routes.stripe.Webhook.construct_event', return_value=event), patch('src.routes.stripe_routes.stripe.checkout.Session.list_line_items', return_value=SimpleNamespace(data=lines)):
            response = self.client.post('/api/stripe/webhook', data=b'{}')
            self.assertTrue(response.get_json()['payment_pending'])
            with self.app.app_context():
                self.assertEqual(Order.query.count(), 0)
            event['type'] = 'checkout.session.async_payment_succeeded'
            event['id'] = 'evt_async_success'
            event['created'] = BF26_CLOSE_TIMESTAMP+60
            event['data']['object']['payment_status'] = 'paid'
            self.assertEqual(self.client.post('/api/stripe/webhook', data=b'{}').status_code, 200)
            event['type'] = 'checkout.session.completed'
            retry = self.client.post('/api/stripe/webhook', data=b'{}')
            self.assertTrue(retry.get_json()['duplicate'])
        confirmation.assert_called_once()
        with self.app.app_context():
            self.assertEqual(Order.query.count(), 1)
            self.assertEqual(StockMovement.query.count(), 1)


if __name__ == '__main__':
    unittest.main()
