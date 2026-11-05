"""No production network. All identities are disposable local fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from flask import Flask
import requests
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from src.models.user import db
from src.models.vip_signup import VipSignup
from src.models.vip_conversion import vip_signup_conversions as table
from src.routes.vip_routes import vip_bp
from src.routes.vip_conversion_routes import vip_conversion_bp
from src.services import vip_conversion_confirmation as service
from src.services import vip_membership_verification as verification
from src.services import meta_vip_conversions_api as meta
from src.services import klaviyo_service

ROOT = Path(__file__).resolve().parents[1]
DB_URL = os.getenv('VIP_FUNNEL_TEST_DATABASE_URL', '')
EMAIL = 'conversion@example.invalid'
UA = 'Mozilla/5.0 local-fixture-browser'


def local_app(url='sqlite:///:memory:'):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='local-only-vip-signature-secret-32-bytes',
                      SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    app.register_blueprint(vip_bp, url_prefix='/api/vip')
    app.register_blueprint(vip_conversion_bp)
    return app


def migrate(app):
    sql = (ROOT / 'migrations/20261105_vip_signup_conversions.sql').read_text()
    with app.app_context(), db.engine.begin() as conn:
        conn.exec_driver_sql(sql)


def evidence(state='member', **overrides):
    values = dict(state=state, list_id='fixture-list', profile_id='fixture-profile' if state != 'absent' else None,
                  checked_at=datetime.utcnow())
    values.update(overrides)
    return verification.VipMembershipEvidence(**values)


def remote_profile(**overrides):
    marketing = {'consent': 'SUBSCRIBED', 'can_receive_email_marketing': True,
                 'suppression': [], 'list_suppressions': []}
    marketing.update(overrides)
    return {'type': 'profile', 'id': 'fixture-profile', 'attributes': {
        'email': EMAIL, 'joined_group_at': '2026-11-01T10:00:00Z',
        'subscriptions': {'email': {'marketing': marketing}}}}


class MembershipVerificationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'KLAVIYO_API_KEY': 'local-fixture-key',
                                                   'KLAVIYO_VIP_LIST_ID': 'fixture-list'})
        self.environment.start()
        klaviyo_service._vip_list_id_cache = None

    def tearDown(self):
        self.environment.stop()
        klaviyo_service._vip_list_id_cache = None

    def read(self, document, status=200):
        response = Mock(status_code=status)
        response.json.return_value = document
        with patch.object(klaviyo_service, 'requests') as transport:
            transport.get.return_value = response
            result = verification.read_vip_membership(EMAIL)
            transport.post.assert_not_called()
            transport.patch.assert_not_called()
            self.assertEqual(transport.get.call_count, 1)
            call = transport.get.call_args
            self.assertEqual(call.args[0], 'https://a.klaviyo.com/api/lists/fixture-list/profiles')
            self.assertEqual(call.kwargs['params'], {
                'filter': f'equals(email,{json.dumps(EMAIL)})',
                'additional-fields[profile]': 'subscriptions', 'page[size]': 2})
            self.assertEqual(call.kwargs['headers']['revision'], '2024-10-15')
        return result

    def test_current_consent_and_membership_single_get(self):
        self.assertEqual(self.read({'data': [remote_profile()], 'links': {'next': None}}).state, 'member')

    def test_authoritative_empty_collection(self):
        self.assertEqual(self.read({'data': [], 'links': {'next': None}}).state, 'absent')

    def test_empty_collection_without_explicit_terminal_page_is_unknown(self):
        for document in ({'data': []}, {'data': [], 'links': {}}, {'data': [], 'links': {'self': 'fixture'}}):
            self.assertEqual(self.read(document).state, 'unknown')

    def test_202_403_429_500_are_never_membership(self):
        for status in (202, 403, 429, 500):
            with self.subTest(status=status):
                self.assertEqual(self.read({'data': [remote_profile()]}, status).state, 'unknown')

    def test_ambiguous_paginated_error_malformed_responses(self):
        for document in ({}, {'data': None}, {'data': [], 'errors': []},
                         {'data': [], 'links': {'next': 'next-page'}},
                         {'data': [], 'links': {'next': False}},
                         {'data': [remote_profile(), remote_profile()]},
                         {'data': ['not-profile']}, {'data': [], 'links': []}):
            with self.subTest(document=str(document)[:20]):
                self.assertEqual(self.read(document).state, 'unknown')

    def test_mismatched_email_type_id_joined_timestamp(self):
        for field, value in (('id', '../not-valid'), ('type', 'list'),
                             ('email', 'different@example.invalid'), ('joined_group_at', None),
                             ('joined_group_at', '2099-01-01T10:00:00Z'),
                             ('joined_group_at', '2026-11-01T10:00:00')):
            profile = remote_profile()
            (profile if field in ('id', 'type') else profile['attributes'])[field] = value
            self.assertEqual(self.read({'data': [profile]}).state, 'mismatch' if field == 'email' else 'unknown')

    def test_ineligible_suppressed_pending_and_unverified_consent(self):
        for overrides, state in [({'consent': 'UNSUBSCRIBED'}, 'ineligible'),
                                 ({'consent': 'NEVER_SUBSCRIBED'}, 'ineligible'),
                                 ({'can_receive_email_marketing': False}, 'ineligible'),
                                 ({'can_receive_email_marketing': 1}, 'unknown'),
                                 ({'consent': 'PENDING'}, 'unknown'),
                                 ({'suppression': [{'reason': 'HARD_BOUNCE', 'timestamp': 'fixture'}]}, 'ineligible'),
                                 ({'list_suppressions': [{'list_id': 'other-list', 'reason': 'USER_SUPPRESSED', 'timestamp': 'fixture'}]}, 'ineligible'),
                                 ({'suppression': None}, 'unknown')]:
            with self.subTest(state=state):
                self.assertEqual(self.read({'data': [remote_profile(**overrides)]}).state, state)

    def test_timeout_invalid_json_unknown_and_no_pii_logs(self):
        captured = StringIO()
        for failure in (requests.Timeout('secret-key?email=' + EMAIL), ValueError(EMAIL)):
            with patch.object(klaviyo_service.requests, 'get', side_effect=failure), redirect_stdout(captured):
                self.assertEqual(verification.read_vip_membership(EMAIL).state, 'unknown')
        self.assertEqual(captured.getvalue(), '')

    def test_reuse_private_hotfix_helpers_without_modification(self):
        with patch.object(klaviyo_service, '_vip_collection', wraps=klaviyo_service._vip_collection) as collection, \
                patch.object(klaviyo_service, '_vip_existing_consent_error', wraps=klaviyo_service._vip_existing_consent_error) as consent:
            self.read({'data': [remote_profile()]})
            collection.assert_called_once()
            consent.assert_called_once()


class MetaVipSenderTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(event_id=str(uuid4()), email_hash=sha256(EMAIL.encode()).hexdigest(),
                         user_agent=UA, event_time=datetime.utcnow(), marketing_consent=True)
        self.environment = patch.dict(os.environ, {'META_PIXEL_ID': '12345678', 'META_CAPI_ACCESS_TOKEN': 'local-secret-token'})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_minimal_payload_real_user_agent_token_header_canonical_source(self):
        with patch.object(meta.requests, 'post', return_value=Mock(status_code=200, json=lambda: {'events_received': 1})) as post:
            self.assertTrue(meta.dispatch_meta_vip_event(**self.args)['sent'])
        call = post.call_args
        self.assertNotIn('local-secret-token', call.args[0])
        self.assertNotIn('params', call.kwargs)
        self.assertEqual(call.kwargs['headers'], {'Authorization': 'Bearer local-secret-token'})
        event = call.kwargs['json']['data'][0]
        self.assertEqual(event['event_source_url'], 'https://www.mikels.es/vip/gracias')
        self.assertEqual(event['event_name'], 'VIPSignupConfirmed')
        self.assertEqual(event['event_id'], self.args['event_id'])
        self.assertEqual(event['user_data'], {'em': [self.args['email_hash']], 'client_user_agent': UA})
        self.assertEqual(set(event), {'event_name', 'event_id', 'event_time', 'event_source_url', 'action_source', 'user_data'})
        self.assertNotIn(EMAIL, json.dumps(call.kwargs['json']))
        self.assertEqual(call.kwargs['timeout'], 10)

    def test_receipt_must_prove_exactly_one_event(self):
        for receipt in ({}, None, [], {'events_received': True}, {'events_received': 0},
                        {'events_received': 2}, {'events_received': 1, 'error': 'secret'}):
            with patch.object(meta.requests, 'post', return_value=Mock(status_code=200, json=lambda: receipt)):
                self.assertEqual(meta.dispatch_meta_vip_event(**self.args), {'sent': False, 'reason': 'meta_receipt_unverified'})

    def test_timeouts_http_and_invalid_json_are_sanitized(self):
        for failure in (requests.Timeout('url?access_token=secret&email=' + EMAIL),
                        requests.RequestException(EMAIL), ValueError('secret')):
            with patch.object(meta.requests, 'post', side_effect=failure):
                result = meta.dispatch_meta_vip_event(**self.args)
                self.assertFalse(result['sent'])
                self.assertNotIn('secret', str(result))
                self.assertNotIn(EMAIL, str(result))
        with patch.object(meta.requests, 'post', return_value=Mock(status_code=500)):
            self.assertEqual(meta.dispatch_meta_vip_event(**self.args)['reason'], 'meta_http_failed')

    def test_strict_cookie_consent_blocks_sender(self):
        for value in (False, None, 1, 'true', [], {}):
            with patch.object(meta.requests, 'post') as post:
                self.assertFalse(meta.dispatch_meta_vip_event(**dict(self.args, marketing_consent=value))['sent'])
                post.assert_not_called()

    def test_no_fake_identity_or_user_agent_fallback(self):
        for changes in ({'user_agent': ''}, {'user_agent': '\r\nFake'}, {'user_agent': 'x' * 2049},
                        {'email_hash': EMAIL}, {'event_id': 'signup-id'}, {'event_time': None}):
            with patch.object(meta.requests, 'post') as post:
                self.assertFalse(meta.dispatch_meta_vip_event(**dict(self.args, **changes))['sent'])
                post.assert_not_called()

    def test_not_configured_does_not_send(self):
        with patch.dict(os.environ, {'META_CAPI_ACCESS_TOKEN': ''}), patch.object(meta.requests, 'post') as post:
            self.assertEqual(meta.dispatch_meta_vip_event(**self.args)['reason'], 'meta_not_configured')
            post.assert_not_called()


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.app = local_app()
        with self.app.app_context():
            db.create_all()
        migrate(self.app)
        self.client = self.app.test_client()
        self.pre = patch('src.routes.vip_routes.read_vip_membership', side_effect=lambda email: evidence('absent'))
        self.current = patch.object(service, 'read_vip_membership', side_effect=lambda email: evidence())
        self.subscribe = patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None))
        self.event = patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None))
        self.sender = patch.object(service, 'dispatch_meta_vip_event', return_value={'sent': True, 'reason': 'accepted'})
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('No network in tests'))
        self.mocks = [p.start() for p in (self.pre, self.current, self.subscribe, self.event, self.sender, self.network)]

    def tearDown(self):
        for p in (self.pre, self.current, self.subscribe, self.event, self.sender, self.network):
            p.stop()
        with self.app.app_context():
            db.session.remove()
            with db.engine.begin() as conn:
                conn.execute(text('DROP TABLE IF EXISTS vip_signup_conversions'))
            db.drop_all()

    def signup(self, **changes):
        return self.client.post('/api/vip/signup', json=dict({'email': EMAIL, 'marketing_consent': True}, **changes))

    def proof(self):
        response = self.signup()
        self.assertEqual(response.status_code, 201)
        self.assertIn('conversion', response.json)
        return response.json['conversion']

    def confirm(self, proof, **changes):
        return self.client.post('/api/vip/conversion', json=dict(token=proof['token'], meta_marketing_consent=True, **changes), headers={'User-Agent': UA})

    def row(self):
        with self.app.app_context(), db.engine.connect() as conn:
            return dict(conn.execute(select(table)).mappings().one())

    def mutate(self, **values):
        with self.app.app_context(), db.engine.begin() as conn:
            conn.execute(update(table).values(**values))

    @contextmanager
    def async_transports(self):
        """Real routes/services, only HTTP replaced; no client/Meta/Klaviyo network."""
        empty = {'data': [], 'links': {'next': None}}
        klaviyo_service._vip_list_id_cache = None
        try:
            with patch.dict(os.environ, {'KLAVIYO_API_KEY': 'local-fixture-key',
                                         'KLAVIYO_VIP_LIST_ID': 'fixture-list',
                                         'META_PIXEL_ID': '12345678',
                                         'META_CAPI_ACCESS_TOKEN': 'local-fixture-token'}), \
                    patch.object(klaviyo_service, 'requests') as transport, \
                    patch.object(meta, 'requests') as capi, \
                    patch('src.routes.vip_routes.read_vip_membership', wraps=verification.read_vip_membership), \
                    patch.object(service, 'read_vip_membership', wraps=verification.read_vip_membership), \
                    patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', wraps=klaviyo_service.klaviyo_subscribe_vip_contact), \
                    patch('src.routes.vip_routes.klaviyo_track_vip_signup', wraps=klaviyo_service.klaviyo_track_vip_signup), \
                    patch.object(service, 'dispatch_meta_vip_event', wraps=meta.dispatch_meta_vip_event):
                transport.get.side_effect = [Mock(status_code=200, json=lambda: empty)] * 3
                transport.post.side_effect = [
                    Mock(status_code=201, json=lambda: {'data': {'type': 'profile', 'id': 'fixture-profile'}}),
                    Mock(status_code=202), Mock(status_code=202)]
                capi.post.return_value = Mock(status_code=200, json=lambda: {'events_received': 1})
                yield transport, capi
        finally:
            klaviyo_service._vip_list_id_cache = None

    def test_integrated_new_subscribe_202_candidate_pending_then_actual_member_one_capi(self):
        with self.async_transports() as (transport, capi):
            proof = self.proof()
            self.assertEqual(transport.post.call_count, 3)
            subscribe_call = transport.post.call_args_list[1]
            self.assertTrue(subscribe_call.args[0].endswith('/profile-subscription-bulk-create-jobs'))
            self.assertEqual(subscribe_call.kwargs['json']['data']['relationships']['list']['data']['id'], 'fixture-list')
            with self.app.app_context():
                signup = VipSignup.query.one()
                self.assertEqual((signup.klaviyo_subscription_status, signup.klaviyo_event_status), ('accepted', 'accepted'))
            row = self.row()
            for key in ('profile_id', 'post_checked_at', 'confirmed_at', 'last_checked_at'):
                self.assertIsNone(row[key])
            self.assertEqual(row['capi_attempts'], 0)
            capi.post.assert_not_called()
            empty = {'data': [], 'links': {'next': None}}
            transport.get.side_effect = None
            transport.get.return_value = Mock(status_code=200, json=lambda: empty)
            pending = self.confirm(proof)
            self.assertEqual(pending.status_code, 202)
            self.assertEqual(pending.json, {'confirmed': False, 'pending': True})
            self.assertEqual(pending.headers['Cache-Control'], 'no-store, private')
            self.assertNotIn(EMAIL, pending.get_data(as_text=True))
            self.assertEqual(self.row(), row)
            capi.post.assert_not_called()
            transport.get.return_value = Mock(status_code=200, json=lambda: {'data': [remote_profile()], 'links': {'next': None}})
            def receipt(*args, **kwargs):
                claimed = self.row()  # Independent connection sees COMMITTED claim before HTTP.
                self.assertEqual(claimed['capi_state'], 'sending')
                self.assertEqual(claimed['capi_attempts'], 1)
                self.assertEqual(claimed['profile_id'], 'fixture-profile')
                self.assertIsNotNone(claimed['post_checked_at'])
                self.assertIsNotNone(claimed['confirmed_at'])
                return Mock(status_code=200, json=lambda: {'events_received': 1})
            capi.post.side_effect = receipt
            confirmed = self.confirm(proof)
            self.assertEqual(confirmed.status_code, 200)
            self.assertEqual(confirmed.json, {'confirmed': True, 'event_id': proof['event_id'],
                                             'event_name': 'VIPSignupConfirmed', 'capi_sent': True})
            self.assertTrue(self.confirm(proof).json['confirmed'])
            self.assertEqual(capi.post.call_count, 1)
            event = capi.post.call_args.kwargs['json']['data'][0]
            self.assertEqual(event['event_id'], proof['event_id'])
            self.assertEqual(event['event_time'], int(self.row()['confirmed_at'].replace(tzinfo=meta.timezone.utc).timestamp()))
            self.assertEqual(transport.post.call_count, 3)  # No resubscription at confirmation.

    def test_integrated_async_suppressions_and_indeterminate_get_never_confirm_or_send(self):
        with self.async_transports() as (transport, capi):
            proof = self.proof()
            transport.get.side_effect = None
            cases = [({'data': [remote_profile(consent='UNSUBSCRIBED')]}, 200, False),
                     ({'data': [remote_profile(suppression=[{'reason': 'HARD_BOUNCE', 'timestamp': 'fixture'}])]}, 200, False),
                     ({'data': [remote_profile(list_suppressions=[{'list_id': 'other', 'reason': 'USER_SUPPRESSED', 'timestamp': 'fixture'}])]}, 200, False),
                     ({'data': [remote_profile(consent='PENDING')]}, 200, True),
                     ({'data': [remote_profile(suppression=None)]}, 200, True),
                     ({'data': [], 'links': {'next': None}}, 200, True),
                     ({'data': []}, 200, True), ({'data': [remote_profile()]}, 202, True),
                     ({}, 429, True), ({}, 500, True)]
            for document, remote_status, pending in cases:
                transport.get.return_value = Mock(status_code=remote_status, json=lambda: document)
                result = self.confirm(proof)
                self.assertEqual(result.status_code, 202 if pending else 200)
                self.assertEqual(result.json, {'confirmed': False, 'pending': True} if pending else {'confirmed': False})
                for key in ('profile_id', 'post_checked_at', 'confirmed_at'):
                    self.assertIsNone(self.row()[key])
                self.assertEqual(self.row()['capi_attempts'], 0)
            transport.get.side_effect = requests.Timeout('private-fixture')
            self.assertEqual(self.confirm(proof).status_code, 202)
            capi.post.assert_not_called()

    def test_integrated_post_get_explicit_email_mismatch_omits_candidate(self):
        with self.async_transports() as (transport, capi):
            wrong = remote_profile()
            wrong['attributes']['email'] = 'different@example.invalid'
            empty = {'data': [], 'links': {'next': None}}
            transport.get.side_effect = [Mock(status_code=200, json=lambda: empty),
                                         Mock(status_code=200, json=lambda: empty),
                                         Mock(status_code=200, json=lambda: {'data': [wrong]})]
            signup = self.signup()
            self.assertEqual(signup.status_code, 201)
            self.assertNotIn('conversion', signup.json)
            capi.post.assert_not_called()

    def test_async_candidate_binds_actual_profile_and_rejects_later_mismatch(self):
        self.mocks[1].side_effect = lambda email: evidence('unknown')
        proof = self.proof()
        self.assertIsNone(self.row()['profile_id'])
        self.mocks[1].side_effect = lambda email: evidence(profile_id='confirmed-profile')
        self.assertTrue(self.confirm(proof).json['confirmed'])
        self.assertEqual(self.row()['profile_id'], 'confirmed-profile')
        for changes in ({'profile_id': 'other'}, {'list_id': 'other'},
                        {'state': 'mismatch'}, {'state': 'unknown', 'profile_id': 'other'}):
            self.mocks[1].side_effect = lambda email: evidence(**changes)
            result = self.confirm(proof)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json, {'confirmed': False})
        self.assertEqual(self.mocks[4].call_count, 1)

    def test_async_invalid_proof_is_terminal_200_without_get_or_capi(self):
        self.mocks[1].side_effect = lambda email: evidence('unknown')
        proof = self.proof()
        self.mocks[1].reset_mock()
        result = self.confirm(dict(proof, token=proof['token'] + 'tampered'))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json, {'confirmed': False})
        self.mocks[1].assert_not_called()
        self.mocks[4].assert_not_called()

    def test_async_pending_does_not_consume_capi_attempts_or_change_event_time(self):
        self.mocks[1].side_effect = lambda email: evidence('absent')
        proof = self.proof()
        for _ in range(3):
            self.assertEqual(self.confirm(proof).status_code, 202)
        self.assertEqual(self.row()['capi_attempts'], 0)
        self.assertIsNone(self.row()['confirmed_at'])
        self.mocks[1].side_effect = lambda email: evidence()
        self.mocks[4].return_value = {'sent': False, 'reason': 'meta_timeout'}
        for _ in range(4):
            self.assertTrue(self.confirm(proof).json['confirmed'])
        self.assertEqual(self.mocks[4].call_count, 2)
        self.assertEqual(self.mocks[4].call_args_list[0], self.mocks[4].call_args_list[1])
        self.assertEqual(self.mocks[4].call_args.kwargs['event_id'], proof['event_id'])

    def test_schema_nullability_and_no_confirmed_without_real_post_evidence(self):
        self.mocks[1].side_effect = lambda email: evidence('absent')
        self.proof()
        self.assertTrue(table.c.profile_id.nullable)
        self.assertTrue(table.c.post_checked_at.nullable)
        for values in ({'profile_id': 'fake'}, {'post_checked_at': datetime.utcnow()},
                       {'confirmed_at': datetime.utcnow()}):
            with self.assertRaises(IntegrityError):
                self.mutate(**values)
        self.assertIsNone(self.row()['confirmed_at'])

    def test_201_proof_has_no_pii_and_no_meta_send_before_thanks(self):
        proof = self.proof()
        self.mocks[4].assert_not_called()
        with self.app.app_context():
            decoded = service._serializer().loads(proof['token'])
        self.assertNotIn(EMAIL, json.dumps(decoded))
        self.assertEqual(decoded['event_id'], proof['event_id'])
        self.assertEqual(proof['event_name'], 'VIPSignupConfirmed')
        self.assertEqual(self.row()['capi_state'], 'pending')

    def test_existing_vip_or_ineligible_or_unknown_pre_read_never_issues_proof(self):
        for state in ('member', 'ineligible', 'unknown'):
            self.mocks[0].side_effect = lambda email: evidence(state)
            response = self.signup()
            self.assertEqual(response.status_code, 201)
            self.assertNotIn('conversion', response.json)
        self.mocks[1].assert_not_called()
        self.mocks[4].assert_not_called()

    def test_202_accepted_candidate_is_not_proven_membership(self):
        for state in ('absent', 'unknown'):
            self.mocks[1].side_effect = lambda email: evidence(state)
            response = self.signup(email=f'{state}@example.invalid')
            self.assertEqual(response.status_code, 201)
            self.assertIn('conversion', response.json)
            result = self.confirm(response.json['conversion'])
            self.assertEqual(result.status_code, 202)
            self.assertEqual(result.json, {'confirmed': False, 'pending': True})
        self.mocks[1].side_effect = lambda email: evidence('ineligible')
        response = self.signup(email='ineligible@example.invalid')
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('conversion', response.json)
        self.mocks[4].assert_not_called()

    def test_explicit_mismatched_post_get_never_issues_candidate(self):
        for index, changes in enumerate(({'state': 'mismatch'}, {'list_id': 'other-list'},
                                         {'state': 'unknown', 'list_id': 'other-list'})):
            self.mocks[1].side_effect = lambda email: evidence(**changes)
            response = self.signup(email=f'mismatch{index}@example.invalid')
            self.assertEqual(response.status_code, 201)
            self.assertNotIn('conversion', response.json)
        self.mocks[4].assert_not_called()

    def test_honeypot_validation_ineligible_and_failed_never_make_proof(self):
        self.assertEqual(self.signup(website='trap').status_code, 200)
        self.assertEqual(self.client.post('/api/vip/signup', json={'email': EMAIL, 'marketing_consent': False}).status_code, 400)
        self.mocks[0].assert_not_called()
        for reason in ('VIP_EXISTING_NO_EMAIL_CONSENT', 'VIP_EXISTING_EMAIL_SUPPRESSED', 'VIP_EXISTING_LIST_SUPPRESSED', 'TIMEOUT'):
            self.mocks[2].return_value = (False, reason)
            response = self.signup()
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('conversion', response.json)
        self.mocks[2].return_value = (True, None)
        self.mocks[3].return_value = (False, 'TIMEOUT')
        self.assertEqual(self.signup().status_code, 502)
        self.mocks[4].assert_not_called()

    def test_confirmation_revalidates_membership_and_sends_same_id_once(self):
        proof = self.proof()
        response = self.confirm(proof)
        self.assertEqual(response.json, {'confirmed': True, 'event_id': proof['event_id'], 'event_name': 'VIPSignupConfirmed', 'capi_sent': True})
        self.assertEqual(self.mocks[1].call_count, 2)
        self.assertEqual(self.mocks[4].call_args.kwargs['event_id'], proof['event_id'])
        self.assertEqual(self.mocks[4].call_args.kwargs['user_agent'], UA)
        self.assertEqual(self.row()['capi_state'], 'sent')
        self.assertEqual(response.headers['Cache-Control'], 'no-store, private')

    def test_replay_refresh_and_repeated_signup_keep_one_event(self):
        proof = self.proof()
        for _ in range(4):
            self.assertTrue(self.confirm(proof).json['confirmed'])
        self.assertEqual(self.mocks[4].call_count, 1)
        response = self.signup()
        self.assertNotIn('conversion', response.json)
        self.assertEqual(self.row()['event_id'], proof['event_id'])

    def test_strict_marketing_boolean_for_public_confirmation(self):
        proof = self.proof()
        for value in (False, None, 1, 'true', [], {}):
            response = self.client.post('/api/vip/conversion', json={'token': proof['token'], 'meta_marketing_consent': value}, headers={'User-Agent': UA})
            self.assertEqual(response.json, {'confirmed': False})
        self.mocks[4].assert_not_called()

    def test_forged_missing_token_bare_signup_id_and_wrong_campaign(self):
        proof = self.proof()
        bodies = [{}, {'signup_id': 1, 'meta_marketing_consent': True},
                  {'token': '', 'meta_marketing_consent': True},
                  {'token': proof['token'] + 'forged', 'meta_marketing_consent': True}]
        with self.app.app_context():
            valid = service._serializer().loads(proof['token'])
            forged = service._serializer().dumps(dict(valid, campaign='other'))
        bodies.append({'token': forged, 'meta_marketing_consent': True})
        for body in bodies:
            self.assertEqual(self.client.post('/api/vip/conversion', json=body, headers={'User-Agent': UA}).json, {'confirmed': False})
        self.mocks[4].assert_not_called()

    def test_expired_database_and_signed_ttl_fail_closed(self):
        proof = self.proof()
        self.mutate(created_at=datetime.utcnow() - timedelta(hours=1),
                    expires_at=datetime.utcnow() - timedelta(seconds=1))
        self.assertFalse(self.confirm(proof).json['confirmed'])
        self.mutate(expires_at=datetime.utcnow() + timedelta(minutes=10))
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=int(time.time()) + service.PROOF_TTL_SECONDS + 1):
            self.assertFalse(self.confirm(proof).json['confirmed'])
        self.mocks[4].assert_not_called()

    def test_local_signup_rejected_pending_failed_or_consent_revoked(self):
        proof = self.proof()
        for values in ({'klaviyo_subscription_status': 'failed'}, {'klaviyo_subscription_status': 'pending'},
                       {'klaviyo_subscription_status': 'ineligible'}, {'klaviyo_event_status': 'failed'},
                       {'klaviyo_event_status': 'pending'}, {'consent_marketing': False}):
            with self.app.app_context(), db.engine.begin() as conn:
                conn.execute(update(VipSignup.__table__).values(klaviyo_subscription_status='accepted', klaviyo_event_status='accepted', consent_marketing=True))
                conn.execute(update(VipSignup.__table__).values(**values))
            self.assertEqual(self.confirm(proof).json, {'confirmed': False})
        self.mocks[4].assert_not_called()

    def test_remote_withdrawal_nonmember_pending_timeout_never_confirms(self):
        proof = self.proof()
        for state in ('absent', 'ineligible', 'unknown'):
            self.mocks[1].side_effect = lambda email: evidence(state)
            result = self.confirm(proof)
            self.assertEqual(result.json, {'confirmed': False} if state == 'ineligible' else
                             {'confirmed': False, 'pending': True})
            self.assertEqual(result.status_code, 200 if state == 'ineligible' else 202)
        self.mocks[1].side_effect = requests.Timeout(EMAIL)
        result = self.confirm(proof)
        self.assertEqual(result.json, {'confirmed': False, 'pending': True})
        self.assertEqual(result.status_code, 202)
        self.assertIsNone(self.row()['confirmed_at'])
        self.assertEqual(self.row()['capi_attempts'], 0)
        self.mocks[4].assert_not_called()

    def test_remote_list_or_profile_mismatch_never_confirms(self):
        proof = self.proof()
        for changes in ({'list_id': 'other'}, {'profile_id': 'other'}):
            self.mocks[1].side_effect = lambda email: evidence(**changes)
            self.assertEqual(self.confirm(proof).json, {'confirmed': False})
        self.mocks[4].assert_not_called()

    def test_timeout_explicit_retry_max_two_with_stable_id_and_time(self):
        proof = self.proof()
        self.mocks[4].return_value = {'sent': False, 'reason': 'meta_timeout'}
        for _ in range(6):
            result = self.confirm(proof).json
            self.assertTrue(result['confirmed'])
            self.assertFalse(result['capi_sent'])
            self.assertEqual(result['event_id'], proof['event_id'])
        self.assertEqual(self.mocks[4].call_count, 2)
        args = [call.kwargs for call in self.mocks[4].call_args_list]
        self.assertEqual(args[0], args[1])
        self.assertEqual(self.row()['capi_attempts'], 2)
        self.assertEqual(self.row()['capi_state'], 'unknown')

    def test_retry_after_timeout_success_replay_never_resends(self):
        proof = self.proof()
        self.mocks[4].side_effect = [{'sent': False, 'reason': 'meta_timeout'}, {'sent': True, 'reason': 'accepted'}]
        self.assertFalse(self.confirm(proof).json['capi_sent'])
        self.assertTrue(self.confirm(proof).json['capi_sent'])
        self.assertTrue(self.confirm(proof).json['capi_sent'])
        self.assertEqual(self.mocks[4].call_count, 2)

    def test_invalid_receipt_permanent_failure_or_inflight_no_automatic_recovery(self):
        proof = self.proof()
        for state, reason in (('unknown', 'meta_receipt_unverified'), ('failed', 'meta_http_failed'),
                              ('failed', 'meta_not_configured'), ('sending', None)):
            self.mutate(capi_state=state, capi_reason=reason, capi_attempts=1, confirmed_at=datetime.utcnow())
            self.assertTrue(self.confirm(proof).json['confirmed'])
            self.assertFalse(self.confirm(proof).json['capi_sent'])
        self.mocks[4].assert_not_called()

    def test_missing_table_failclosed_does_not_break_signup(self):
        with self.app.app_context(), db.engine.begin() as conn:
            conn.execute(text('DROP TABLE vip_signup_conversions'))
        response = self.signup()
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('conversion', response.json)
        self.assertEqual(self.client.post('/api/vip/conversion', json={'token': 'invalid', 'meta_marketing_consent': True}).json, {'confirmed': False})
        with self.app.app_context():
            self.assertEqual(VipSignup.query.one().klaviyo_event_status, 'accepted')

    def test_signing_disabled_never_breaks_signup(self):
        self.app.config['SECRET_KEY'] = None
        self.assertNotIn('conversion', self.signup().json)

    def test_optional_analytics_exceptions_do_not_break_signup_or_leak(self):
        self.mocks[0].side_effect = RuntimeError('secret-email=' + EMAIL)
        response = self.signup()
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('conversion', response.json)
        self.assertNotIn(EMAIL, response.get_data(as_text=True))
        self.mocks[0].side_effect = lambda email: evidence('absent')
        with patch('src.routes.vip_routes.prepare_vip_conversion', side_effect=RuntimeError('secret')):
            response = self.signup()
            self.assertEqual(response.status_code, 201)
            self.assertNotIn('conversion', response.json)

    def test_untrusted_payload_query_email_ua_and_raw_exception_never_leak(self):
        proof = self.proof()
        extra = self.client.post('/api/vip/conversion', json={'token': proof['token'], 'meta_marketing_consent': True, 'email': EMAIL})
        self.assertEqual(extra.json, {'confirmed': False})
        query = self.client.post('/api/vip/conversion?email=ignored', json={'token': proof['token'], 'meta_marketing_consent': True})
        self.assertEqual(query.json, {'confirmed': False})
        no_ua = self.client.post('/api/vip/conversion', json={'token': proof['token'], 'meta_marketing_consent': True}, headers={'User-Agent': ''})
        self.assertEqual(no_ua.json, {'confirmed': False})
        self.mocks[4].side_effect = RuntimeError('access_token=secret&email=' + EMAIL)
        response = self.confirm(proof)
        self.assertNotIn(EMAIL, response.get_data(as_text=True))
        self.assertNotIn('secret', response.get_data(as_text=True))
        self.assertEqual(self.row()['capi_reason'], 'meta_request_failed')

    def test_wrong_proof_event_and_signup_link_fail_closed(self):
        proof = self.proof()
        with self.app.app_context():
            decoded = service._serializer().loads(proof['token'])
            for key, value in (('event_id', str(uuid4())), ('signup_id', 9999), ('conversion_id', str(uuid4()))):
                other = service._serializer().dumps(dict(decoded, **{key: value}))
                self.assertEqual(self.confirm({'token': other}).json, {'confirmed': False})
        self.mocks[4].assert_not_called()

    def test_table_has_no_clear_email_ip_ua_or_token_and_not_in_runtime_metadata(self):
        self.assertNotIn('vip_signup_conversions', db.metadata.tables)
        self.assertEqual(self.row_if_created_hash(), sha256(EMAIL.encode()).hexdigest())
        self.assertFalse({'email', 'source_ip', 'user_agent', 'token'} & set(table.c.keys()))

    def row_if_created_hash(self):
        self.proof()
        return self.row()['email_hash']

    def test_migration_repeatable_and_unrelated_tables_untouched(self):
        migrate(self.app)
        migrate(self.app)
        with self.app.app_context():
            self.assertEqual(VipSignup.query.count(), 0)
        proof = self.proof()
        self.assertEqual(self.row()['event_id'], proof['event_id'])

    def test_malformed_and_oversized_public_payloads_generic(self):
        for payload in ('[]', 'null', '{', '"invalid"', 'x' * 5000):
            result = self.client.post('/api/vip/conversion', data=payload, content_type='application/json')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json, {'confirmed': False})

    def test_confirmation_registered_and_excluded_from_runtime_seed(self):
        source = (ROOT / 'src/main.py').read_text()
        block = source[source.index('def create_tables():'):source.index('    if not hasattr(app,')]
        self.assertIn("if request.path == '/api/vip/conversion':", block)
        self.assertIn('app.register_blueprint(vip_conversion_bp)', source)


@unittest.skipUnless(DB_URL, 'Disposable PostgreSQL Unix socket URL not provided')
class ConversionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(DB_URL)
        if not url.drivername.startswith('postgresql') or not str(url.query.get('host', '')).startswith('/') or not url.database.endswith('_tests'):
            raise RuntimeError('Refusing non-local/non-test PostgreSQL')
        cls.app = local_app(DB_URL)
        with cls.app.app_context():
            db.create_all()
        migrate(cls.app)

    @classmethod
    def tearDownClass(cls):
        # The shared local runner has older suites which TRUNCATE vip_signups.
        # Remove ONLY our disposable test table so its FK cannot affect them.
        with cls.app.app_context(), db.engine.begin() as conn:
            conn.execute(text('DROP TABLE vip_signup_conversions'))

    def setUp(self):
        with self.app.app_context(), db.engine.begin() as conn:
            conn.execute(text('DELETE FROM vip_signup_conversions'))
            conn.execute(text('DELETE FROM vip_signups'))
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('No network'))
        self.network.start()
        self.pre = patch('src.routes.vip_routes.read_vip_membership', side_effect=lambda email: evidence('absent'))
        self.current = patch.object(service, 'read_vip_membership', side_effect=lambda email: evidence())
        self.subscribe = patch('src.routes.vip_routes.klaviyo_subscribe_vip_contact', return_value=(True, None))
        self.event = patch('src.routes.vip_routes.klaviyo_track_vip_signup', return_value=(True, None))
        self.sender = patch.object(service, 'dispatch_meta_vip_event', return_value={'sent': True, 'reason': 'accepted'})
        self.mocks = [p.start() for p in (self.pre, self.current, self.subscribe, self.event, self.sender)]

    def tearDown(self):
        for p in (self.pre, self.current, self.subscribe, self.event, self.sender, self.network):
            p.stop()
        with self.app.app_context(), db.engine.begin() as conn:
            conn.execute(text('DELETE FROM vip_signup_conversions'))
            conn.execute(text('DELETE FROM vip_signups'))

    def proof(self):
        response = self.app.test_client().post('/api/vip/signup', json={'email': EMAIL, 'marketing_consent': True})
        self.assertEqual(response.status_code, 201)
        return response.json['conversion']

    def confirm_worker(self, token):
        with self.app.app_context():
            return service.confirm_vip_conversion(token, marketing_consent=True, user_agent=UA)

    def test_async_candidate_postgresql_null_evidence_pending_then_concurrent_single_claim(self):
        self.mocks[1].side_effect = lambda email: evidence('absent')
        proof = self.proof()
        self.assertEqual(self.confirm_worker(proof['token']), {'confirmed': False, 'pending': True})
        with self.app.app_context(), db.engine.connect() as conn:
            row = conn.execute(select(table)).mappings().one()
            self.assertIsNone(row['profile_id'])
            self.assertIsNone(row['post_checked_at'])
            self.assertIsNone(row['confirmed_at'])
            self.assertEqual(row['capi_attempts'], 0)
            columns = dict(conn.execute(text("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name='vip_signup_conversions'")).all())
            self.assertEqual(columns['profile_id'], 'YES')
            self.assertEqual(columns['post_checked_at'], 'YES')
        self.mocks[4].assert_not_called()
        self.mocks[1].side_effect = lambda email: evidence(profile_id='actual-profile')
        def delayed(**args):
            with self.app.app_context(), db.engine.connect() as conn:
                claimed = conn.execute(select(table)).mappings().one()
                self.assertEqual(claimed['capi_state'], 'sending')
                self.assertEqual(claimed['capi_attempts'], 1)
                self.assertEqual(claimed['profile_id'], 'actual-profile')
                self.assertIsNotNone(claimed['post_checked_at'])
                self.assertEqual(claimed['confirmed_at'], args['event_time'])
            time.sleep(0.08)
            return {'sent': True, 'reason': 'accepted'}
        self.mocks[4].side_effect = delayed
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.confirm_worker, [proof['token']] * 8))
        self.assertEqual(self.mocks[4].call_count, 1)
        self.assertTrue(all(result.get('event_id') == proof['event_id'] for result in results if result['confirmed']))
        self.assertTrue(self.confirm_worker(proof['token'])['capi_sent'])
        self.assertEqual(self.mocks[4].call_count, 1)

    def test_async_postgresql_constraints_reject_fake_confirmation(self):
        self.mocks[1].side_effect = lambda email: evidence('unknown')
        self.proof()
        for values in ({'profile_id': 'fake'}, {'post_checked_at': datetime.utcnow()},
                       {'confirmed_at': datetime.utcnow()}):
            with self.app.app_context(), self.assertRaises(IntegrityError):
                with db.engine.begin() as conn:
                    conn.execute(update(table).values(**values))
        self.mocks[4].assert_not_called()

    def test_concurrent_confirmations_postgresql_claim_sends_once(self):
        proof = self.proof()
        def delayed(**args):
            time.sleep(0.08)
            return {'sent': True, 'reason': 'accepted'}
        self.mocks[4].side_effect = delayed
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.confirm_worker, [proof['token']] * 8))
        self.assertEqual(self.mocks[4].call_count, 1)
        self.assertTrue(all(result.get('event_id') == proof['event_id'] for result in results if result['confirmed']))
        with self.app.app_context(), db.engine.connect() as conn:
            row = conn.execute(select(table)).mappings().one()
            self.assertEqual(row['capi_attempts'], 1)
            self.assertEqual(row['capi_state'], 'sent')

    def test_concurrent_new_entries_unique_campaign_hash(self):
        def signup_worker(_):
            return self.app.test_client().post('/api/vip/signup', json={'email': EMAIL, 'marketing_consent': True}).json
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(signup_worker, range(6)))
        self.assertEqual(sum('conversion' in item for item in results), 1)
        with self.app.app_context(), db.engine.connect() as conn:
            self.assertEqual(len(conn.execute(select(table)).all()), 1)
            self.assertEqual(VipSignup.query.count(), 6)

    def test_timeout_claim_persisted_explicit_retry_stable_event(self):
        proof = self.proof()
        self.mocks[4].return_value = {'sent': False, 'reason': 'meta_timeout'}
        for _ in range(4):
            self.assertTrue(self.confirm_worker(proof['token'])['confirmed'])
        self.assertEqual(self.mocks[4].call_count, 2)
        self.assertEqual(self.mocks[4].call_args_list[0], self.mocks[4].call_args_list[1])

    def test_missing_table_never_rolls_back_normal_signup(self):
        with self.app.app_context(), db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE vip_signup_conversions RENAME TO unavailable_vip_conversions'))
        try:
            response = self.app.test_client().post('/api/vip/signup', json={'email': EMAIL, 'marketing_consent': True})
            self.assertEqual(response.status_code, 201)
            self.assertNotIn('conversion', response.json)
            with self.app.app_context():
                self.assertEqual(VipSignup.query.one().klaviyo_event_status, 'accepted')
        finally:
            with self.app.app_context(), db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE unavailable_vip_conversions RENAME TO vip_signup_conversions'))

    def test_schema_migration_repeatable_foreign_key_and_no_runtime_creation(self):
        migrate(self.app)
        migrate(self.app)
        self.assertNotIn('vip_signup_conversions', db.metadata.tables)
        with self.app.app_context(), db.engine.connect() as conn:
            keys = conn.execute(text("SELECT constraint_type FROM information_schema.table_constraints WHERE table_name='vip_signup_conversions'")).scalars().all()
            self.assertIn('FOREIGN KEY', keys)
            self.assertIn('UNIQUE', keys)
            self.assertIn('CHECK', keys)


if __name__ == '__main__':
    unittest.main()
