import os
import unittest
from unittest.mock import Mock, patch

from flask import Flask

os.environ.setdefault('JWT_SECRET', 'test-jwt-key')
os.environ.setdefault('ADMIN_SECRET_KEY', 'test-admin-key')
os.environ.setdefault('SECRET_KEY', 'test-flask-key')
os.environ.setdefault('KLAVIYO_API_KEY', 'test-klaviyo-key')

from src.routes import admin_klaviyo_routes


class KlaviyoCampaignDraftTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    @staticmethod
    def response(status_code, payload=None, text=''):
        response = Mock(status_code=status_code, text=text)
        response.json.return_value = payload or {}
        return response

    def call_route(self, route, payload=None):
        with self.app.test_request_context(json=payload):
            result = route.__wrapped__.__wrapped__()
        return result

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_lists_audiences_without_mutating_klaviyo(self, requests):
        requests.get.side_effect = [
            self.response(200, {'data': [{'id': 'list-newsletter', 'attributes': {'name': 'Newsletter'}}]}),
            self.response(200, {'data': [{'id': 'segment-customers', 'attributes': {'name': 'Particulares'}}]}),
        ]

        response, status = self.call_route(admin_klaviyo_routes.list_campaign_audiences)

        self.assertEqual(status, 200)
        self.assertEqual(response.get_json()['lists'], [{'id': 'list-newsletter', 'name': 'Newsletter', 'type': 'list'}])
        self.assertEqual(response.get_json()['segments'], [{'id': 'segment-customers', 'name': 'Particulares', 'type': 'segment'}])
        self.assertEqual(requests.get.call_args_list[0].kwargs['params']['page[size]'], 10)
        requests.post.assert_not_called()

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_reads_minimal_profile_fields_for_named_preview(self, requests):
        requests.get.return_value = self.response(200, {'data': [{
            'id': 'profile-jordi',
            'attributes': {'email': 'jordi@mikels.es', 'first_name': 'Jordi', 'last_name': 'Giró'},
        }]})

        with self.app.test_request_context('/admin/klaviyo/profiles?emails=jordi@mikels.es'):
            response, status = admin_klaviyo_routes.lookup_klaviyo_profiles.__wrapped__.__wrapped__()

        self.assertEqual(status, 200)
        self.assertEqual(response.get_json()['profiles'][0], {
            'id': 'profile-jordi',
            'email': 'jordi@mikels.es',
            'first_name': 'Jordi',
            'last_name': 'Giró',
            'found': True,
        })
        requests.post.assert_not_called()

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_excludes_internal_profiles_only_from_draft_campaign_without_consent_changes(self, requests):
        requests.get.side_effect = [
            self.response(200, {'data': {'attributes': {'status': 'Draft', 'audiences': {'excluded': ['preview-list']}}}}),
            self.response(200, {'data': [{'id': 'profile-info'}]}),
            self.response(200, {'data': [{'id': 'profile-jordi'}]}),
            self.response(200, {'data': [{'id': 'profile-lfgd'}]}),
            self.response(200, {'data': []}),
        ]
        requests.post.side_effect = [
            self.response(201, {'data': {'id': 'internal-exclusion-list'}}),
            self.response(204),
        ]
        requests.patch.return_value = self.response(200, {'data': {'id': 'campaign-1'}})

        with self.app.test_request_context(json={
            'emails': ['info@mikels.es', 'jordi@mikels.es', 'jordi@lfgd.es'],
            'list_name': 'Excluir campaña Temprano 2026/27',
        }):
            response, status = admin_klaviyo_routes.set_klaviyo_campaign_internal_exclusions.__wrapped__.__wrapped__('campaign-1')

        self.assertEqual(status, 200)
        body = response.get_json()
        self.assertEqual(body['campaign_status'], 'Draft')
        self.assertTrue(body['exclusion_list_created'])
        self.assertEqual(body['excluded_emails'], ['info@mikels.es', 'jordi@mikels.es', 'jordi@lfgd.es'])
        self.assertEqual(requests.post.call_args_list[1].kwargs['json']['data'], [
            {'type': 'profile', 'id': 'profile-info'},
            {'type': 'profile', 'id': 'profile-jordi'},
            {'type': 'profile', 'id': 'profile-lfgd'},
        ])
        audience_update = requests.patch.call_args.kwargs['json']['data']['attributes']['audiences']
        self.assertEqual(audience_update['excluded'], ['preview-list', 'internal-exclusion-list'])

    @patch('src.routes.admin_klaviyo_routes._klaviyo_metric_ids')
    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_creates_dynamic_reservation_exclusion_from_placed_orders_since_cutoff(self, requests, metric_ids):
        metric_ids.return_value = {'Mikels Placed Order': 'metric-placed-order'}
        requests.get.return_value = self.response(200, {'data': [], 'links': {'next': None}})
        requests.post.return_value = self.response(201, {'data': {'id': 'segment-reserved'}})
        response, status = self.call_route(admin_klaviyo_routes.ensure_temprano_reservation_exclusion, {
            'since': '2026-10-07T00:00:00+02:00',
            'segment_name': 'Excluir reservas Temprano desde 07-10-2026',
        })
        self.assertEqual(status, 201)
        body = response.get_json()
        self.assertTrue(body['created'])
        self.assertEqual(body['segment_id'], 'segment-reserved')
        payload = requests.post.call_args.kwargs['json']['data']
        self.assertEqual(payload['type'], 'segment')
        condition = payload['attributes']['definition']['condition_groups'][0]['conditions'][0]
        self.assertEqual(condition, {
            'type': 'profile-metric',
            'metric_id': 'metric-placed-order',
            'measurement': 'count',
            'measurement_filter': {'type': 'numeric', 'operator': 'greater-than', 'value': 0},
            'timeframe_filter': {'type': 'date', 'operator': 'after', 'date': '2026-10-07T00:00:00+02:00'},
            'metric_filters': None,
        })

    @patch('src.routes.admin_klaviyo_routes._klaviyo_metric_ids')
    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_reuses_equivalent_dynamic_reservation_exclusion_after_utc_normalization(self, requests, metric_ids):
        metric_ids.return_value = {'Mikels Placed Order': 'metric-placed-order'}
        requests.get.return_value = self.response(200, {'data': [{
            'id': 'segment-reserved',
            'attributes': {
                'name': 'Excluir reservas Temprano desde 07-10-2026',
                'definition': {'condition_groups': [{'conditions': [{
                    'type': 'profile-metric',
                    'metric_id': 'metric-placed-order',
                    'measurement': 'count',
                    'measurement_filter': {'type': 'numeric', 'operator': 'greater-than', 'value': 0},
                    'timeframe_filter': {'type': 'date', 'operator': 'after', 'date': '2026-10-06T22:00:00Z'},
                    'metric_filters': [],
                }]}]},
            },
        }], 'links': {'next': None}})
        response, status = self.call_route(admin_klaviyo_routes.ensure_temprano_reservation_exclusion, {
            'since': '2026-10-07T00:00:00+02:00',
            'segment_name': 'Excluir reservas Temprano desde 07-10-2026',
        })
        self.assertEqual(status, 200)
        self.assertFalse(response.get_json()['created'])
        requests.post.assert_not_called()

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_refreshes_recipient_estimation_only_for_draft_campaign(self, requests):
        requests.get.side_effect = [
            self.response(200, {'data': {'attributes': {'status': 'Draft', 'audiences': {}}}}),
            self.response(200, {'data': {'attributes': {'estimated_recipient_count': 71}}}),
        ]
        requests.post.return_value = self.response(202, {
            'data': {'id': 'estimation-job-1', 'attributes': {'status': 'complete'}},
        })

        with self.app.test_request_context(method='POST'):
            response, status = admin_klaviyo_routes.refresh_klaviyo_campaign_recipient_estimation.__wrapped__.__wrapped__('campaign-1')

        self.assertEqual(status, 200)
        self.assertEqual(response.get_json()['estimated_recipient_count'], 71)
        self.assertEqual(requests.post.call_args.kwargs['json']['data'], {
            'type': 'campaign-recipient-estimation-job', 'id': 'campaign-1',
        })

    @patch('src.routes.admin_klaviyo_routes.time.sleep')
    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_recipient_estimation_retries_klaviyo_transient_out_of_date_status(self, requests, sleep):
        requests.get.side_effect = [
            self.response(200, {'data': {'attributes': {'status': 'Draft', 'audiences': {}}}}),
            self.response(404, text='No results or results were out of date. Schedule a new estimation.'),
            self.response(200, {'data': {'attributes': {'status': 'complete'}}}),
            self.response(200, {'data': {'attributes': {'estimated_recipient_count': 68}}}),
        ]
        requests.post.return_value = self.response(202, {
            'data': {'id': 'estimation-job-1', 'attributes': {'status': 'queued'}},
        })

        with self.app.test_request_context(method='POST'):
            response, status = admin_klaviyo_routes.refresh_klaviyo_campaign_recipient_estimation.__wrapped__.__wrapped__('campaign-1')

        self.assertEqual(status, 200)
        self.assertEqual(response.get_json()['estimated_recipient_count'], 68)
        self.assertEqual(sleep.call_count, 2)

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_reads_completed_recipient_estimation_without_creating_a_new_job(self, requests):
        requests.get.side_effect = [
            self.response(200, {'data': {'attributes': {'status': 'Draft', 'audiences': {}}}}),
            self.response(200, {'data': {'attributes': {'estimated_recipient_count': 68}}}),
        ]

        with self.app.test_request_context(method='GET'):
            response, status = admin_klaviyo_routes.refresh_klaviyo_campaign_recipient_estimation.__wrapped__.__wrapped__('campaign-1')

        self.assertEqual(status, 200)
        self.assertEqual(response.get_json()['status'], 'complete')
        self.assertEqual(response.get_json()['estimated_recipient_count'], 68)
        requests.post.assert_not_called()

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_imports_campaign_image_into_klaviyo_library(self, requests):
        requests.post.return_value = self.response(201, {
            'data': {
                'id': 'image-1',
                'attributes': {
                    'name': 'Temprano molino 2026/27',
                    'image_url': 'https://cdn.klaviyomail.com/company/example/images/oil.jpg',
                },
            },
        })

        response, status = self.call_route(admin_klaviyo_routes.import_campaign_image, {
            'image_url': 'https://example.com/oil.jpg',
            'name': 'Temprano molino 2026/27',
        })

        self.assertEqual(status, 201)
        self.assertEqual(response.get_json()['image_url'], 'https://cdn.klaviyomail.com/company/example/images/oil.jpg')
        payload = requests.post.call_args.kwargs['json']
        self.assertEqual(payload['data']['type'], 'image')
        self.assertFalse(payload['data']['attributes']['hidden'])

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_creates_unscheduled_campaign_with_smart_sending_and_test_exclusion(self, requests):
        requests.post.side_effect = [
            self.response(201, {'data': {'id': 'template-1'}}),
            self.response(201, {
                'data': {
                    'id': 'campaign-1',
                    'relationships': {'campaign-messages': {'data': [{'id': 'message-1', 'type': 'campaign-message'}]}},
                },
            }),
            self.response(200, {
                'data': {
                    'id': 'message-1',
                    'relationships': {'template': {'data': {'id': 'campaign-template-copy-1', 'type': 'template'}}},
                },
            }),
        ]
        payload = {
            'template_name': 'Mikel’s Fruit — Temprano 2026/27 · reserva',
            'template_html': '<html><body>Reserva</body></html>',
            'campaign_name': 'Temprano 2026/27 · reserva',
            'subject': 'La cosecha que se reserva antes de existir',
            'preview_text': 'Aceite temprano, sin filtrar. Sale a finales de octubre.',
            'included_audiences': ['segment-customers', 'list-newsletter'],
            'excluded_audiences': ['segment-test'],
            'from_email': 'jordi@mikels.es',
            'from_name': "Jordi · Mikel's Fruit",
            'reply_to_email': 'jordi@mikels.es',
        }

        response, status = self.call_route(admin_klaviyo_routes.create_klaviyo_campaign, payload)

        self.assertEqual(status, 201)
        body = response.get_json()
        self.assertEqual(body['status'], 'DRAFT')
        self.assertIsNone(body['scheduled_at'])
        self.assertTrue(body['smart_sending'])
        self.assertEqual(body['campaign_template_id'], 'campaign-template-copy-1')

        campaign_payload = requests.post.call_args_list[1].kwargs['json']['data']['attributes']
        self.assertEqual(campaign_payload['audiences']['included'], ['segment-customers', 'list-newsletter'])
        self.assertEqual(campaign_payload['audiences']['excluded'], ['segment-test'])
        self.assertTrue(campaign_payload['send_options']['use_smart_sending'])
        self.assertEqual(campaign_payload['tracking_options']['custom_tracking_params'][2], {
            'type': 'static', 'name': 'utm_campaign', 'value': 'temprano_2026_27_reserva',
        })
        content = campaign_payload['campaign-messages']['data'][0]['attributes']['definition']['content']
        self.assertEqual(content['reply_to_email'], 'jordi@mikels.es')

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_campaign_readback_returns_actual_draft_scheduling_and_smart_sending(self, requests):
        requests.get.return_value = self.response(200, {
            'data': {
                'id': 'campaign-1',
                'attributes': {
                    'name': 'Temprano 2026/27 · reserva',
                    'status': 'Draft',
                    'scheduled_at': None,
                    'send_time': None,
                    'audiences': {'included': ['segment-customers'], 'excluded': ['segment-test']},
                    'send_options': {'use_smart_sending': True},
                    'send_strategy': {'method': 'immediate'},
                    'tracking_options': {'add_tracking_params': True},
                },
                'relationships': {'campaign-messages': {'data': [{'id': 'message-1', 'type': 'campaign-message'}]}},
            },
        })

        with self.app.test_request_context():
            response, status = admin_klaviyo_routes.get_klaviyo_campaign.__wrapped__.__wrapped__('campaign-1')

        self.assertEqual(status, 200)
        body = response.get_json()
        self.assertEqual(body['status'], 'Draft')
        self.assertIsNone(body['scheduled_at'])
        self.assertTrue(body['send_options']['use_smart_sending'])
        self.assertEqual(body['campaign_messages'][0]['id'], 'message-1')
        self.assertNotIn('campaign-messages', requests.get.call_args.kwargs['params']['fields[campaign]'])

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_updates_subject_only_for_an_existing_draft_campaign_message(self, requests):
        requests.get.side_effect = [
            self.response(200, {
                'data': {
                    'id': 'message-1',
                    'attributes': {
                        'definition': {
                            'channel': 'email',
                            'label': 'Reserva Temprano',
                            'content': {
                                'subject': 'Anterior',
                                'preview_text': 'Previsualización existente',
                                'from_email': 'jordi@mikels.es',
                                'from_label': "Jordi · Mikel's Fruit",
                                'reply_to_email': 'jordi@mikels.es',
                            },
                        },
                    },
                    'relationships': {'campaign': {'data': {'id': 'campaign-1', 'type': 'campaign'}}},
                },
            }),
            self.response(200, {'data': {'attributes': {'status': 'Draft'}}}),
        ]
        requests.patch.return_value = self.response(200, {
            'data': {'attributes': {'definition': {'content': {
                'subject': 'Ya puedes reservar el temprano de este año',
                'preview_text': 'Previsualización existente',
            }}}},
        })

        with self.app.test_request_context(method='PUT', json={'subject': 'Ya puedes reservar el temprano de este año'}):
            response, status = admin_klaviyo_routes.update_klaviyo_campaign_message.__wrapped__.__wrapped__('message-1')

        self.assertEqual(status, 200)
        body = response.get_json()
        self.assertEqual(body['campaign_status'], 'Draft')
        self.assertEqual(body['subject'], 'Ya puedes reservar el temprano de este año')
        payload = requests.patch.call_args.kwargs['json']
        content = payload['data']['attributes']['definition']['content']
        self.assertEqual(content['subject'], 'Ya puedes reservar el temprano de este año')
        self.assertEqual(content['preview_text'], 'Previsualización existente')
        self.assertEqual(content['from_email'], 'jordi@mikels.es')

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_reads_campaign_message_subject_without_mutation(self, requests):
        requests.get.return_value = self.response(200, {
            'data': {
                'id': 'message-1',
                'attributes': {'definition': {
                    'channel': 'email',
                    'label': 'Reserva Temprano 2026/27',
                    'content': {
                        'subject': 'Ya puedes reservar el temprano de este año',
                        'preview_text': 'Aceite temprano, sin filtrar. Sale a finales de octubre.',
                        'from_email': 'jordi@mikels.es',
                        'from_label': "Jordi · Mikel's Fruit",
                        'reply_to_email': 'jordi@mikels.es',
                    },
                }},
                'relationships': {
                    'campaign': {'data': {'id': 'campaign-1', 'type': 'campaign'}},
                    'template': {'data': {'id': 'served-template-1', 'type': 'template'}},
                },
            },
        })

        with self.app.test_request_context(method='GET'):
            response, status = admin_klaviyo_routes.update_klaviyo_campaign_message.__wrapped__.__wrapped__('message-1')

        self.assertEqual(status, 200)
        self.assertEqual(response.get_json()['subject'], 'Ya puedes reservar el temprano de este año')
        self.assertEqual(response.get_json()['template_id'], 'served-template-1')
        requests.patch.assert_not_called()

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_refuses_subject_change_after_a_campaign_leaves_draft(self, requests):
        requests.get.side_effect = [
            self.response(200, {
                'data': {
                    'attributes': {'definition': {'channel': 'email', 'content': {'subject': 'Anterior'}}},
                    'relationships': {'campaign': {'data': {'id': 'campaign-1', 'type': 'campaign'}}},
                },
            }),
            self.response(200, {'data': {'attributes': {'status': 'Scheduled'}}}),
        ]

        with self.app.test_request_context(method='PUT', json={'subject': 'No debe cambiar'}):
            response, status = admin_klaviyo_routes.update_klaviyo_campaign_message.__wrapped__.__wrapped__('message-1')

        self.assertEqual(status, 409)
        requests.patch.assert_not_called()

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_replaces_template_and_subject_only_for_draft_campaign(self, requests):
        requests.get.side_effect = [
            self.response(200, {
                'data': {
                    'attributes': {'definition': {
                        'channel': 'email',
                        'label': 'Reserva Temprano',
                        'content': {
                            'subject': 'Anterior',
                            'preview_text': 'Previsualización existente',
                            'from_email': 'jordi@mikels.es',
                            'from_label': "Jordi · Mikel's Fruit",
                            'reply_to_email': 'jordi@mikels.es',
                        },
                    }},
                    'relationships': {
                        'campaign': {'data': {'id': 'campaign-1', 'type': 'campaign'}},
                        'template': {'data': {'id': 'old-served-copy', 'type': 'template'}},
                    },
                },
            }),
            self.response(200, {'data': {'attributes': {'status': 'Draft'}}}),
        ]
        requests.post.side_effect = [
            self.response(201, {'data': {'id': 'new-source-template'}}),
            self.response(200, {'data': {'relationships': {
                'template': {'data': {'id': 'new-served-copy', 'type': 'template'}},
            }}}),
        ]
        requests.patch.return_value = self.response(200, {'data': {'attributes': {'definition': {'content': {
            'subject': 'Ya puedes reservar el temprano de este año',
        }}}}})

        with self.app.test_request_context(json={
            'template_name': 'Temprano campaña v2',
            'template_html': '<html><body>Reserva revisada</body></html>',
            'subject': 'Ya puedes reservar el temprano de este año',
        }):
            response, status = admin_klaviyo_routes.replace_klaviyo_campaign_draft_template.__wrapped__.__wrapped__('message-1')

        self.assertEqual(status, 200)
        body = response.get_json()
        self.assertEqual(body['campaign_status'], 'Draft')
        self.assertEqual(body['source_template_id'], 'new-source-template')
        self.assertEqual(body['campaign_template_id'], 'new-served-copy')
        self.assertEqual(body['previous_campaign_template_id'], 'old-served-copy')
        created_template = requests.post.call_args_list[0].kwargs['json']
        self.assertEqual(created_template['data']['attributes']['editor_type'], 'CODE')
        assignment = requests.post.call_args_list[1].kwargs['json']
        self.assertEqual(assignment['data']['relationships']['template']['data']['id'], 'new-source-template')
        subject_payload = requests.patch.call_args.kwargs['json']
        self.assertEqual(subject_payload['data']['attributes']['definition']['content']['subject'], 'Ya puedes reservar el temprano de este año')

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_preview_send_uses_beta_template_preview_job_without_scheduling_campaign(self, requests):
        requests.post.return_value = self.response(202, {
            'data': {'id': 'preview-job-1', 'attributes': {'status': 'queued'}},
        })

        response, status = self.call_route(admin_klaviyo_routes.send_template_preview, {
            'template_id': 'template-1',
            'recipients': ['info@mikels.es'],
        })

        self.assertEqual(status, 202)
        self.assertEqual(response.get_json()['preview_job_id'], 'preview-job-1')
        self.assertEqual(requests.post.call_args.kwargs['headers']['revision'], '2026-07-15.pre')
        self.assertEqual(
            requests.post.call_args.kwargs['json']['data']['relationships']['template']['data']['id'],
            'template-1',
        )

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_preview_send_can_render_with_named_profile_without_touching_campaign(self, requests):
        requests.post.return_value = self.response(202, {
            'data': {'id': 'preview-job-2', 'attributes': {'status': 'queued'}},
        })

        response, status = self.call_route(admin_klaviyo_routes.send_template_preview, {
            'template_id': 'template-1',
            'recipients': ['info@mikels.es'],
            'profile_id': 'profile-jordi',
        })

        self.assertEqual(status, 202)
        payload = requests.post.call_args.kwargs['json']['data']
        self.assertEqual(payload['relationships']['profile']['data'], {'type': 'profile', 'id': 'profile-jordi'})
        self.assertEqual(payload['attributes']['recipients'], ['info@mikels.es'])

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_renders_template_context_without_sending_or_modifying_campaign(self, requests):
        requests.post.return_value = self.response(201, {
            'data': {'attributes': {'html': '<p>Hola Jordi,</p>', 'text': 'Hola Jordi,'}},
        })

        response, status = self.call_route(admin_klaviyo_routes.render_klaviyo_template, {
            'template_id': 'template-1',
            'context': {'person': {'first_name': 'Jordi'}},
        })

        self.assertEqual(status, 200)
        self.assertEqual(response.get_json()['html'], '<p>Hola Jordi,</p>')
        payload = requests.post.call_args.kwargs['json']['data']
        self.assertEqual(payload['id'], 'template-1')
        self.assertEqual(payload['attributes']['context'], {'person': {'first_name': 'Jordi'}})
        requests.patch.assert_not_called()


    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_schedules_only_audited_draft_with_completed_estimate(self, requests):
        requests.get.side_effect = [
            self.response(200, {
                'data': {
                    'attributes': {'definition': {'channel': 'email', 'content': {
                        'subject': 'Este año el temprano funciona distinto',
                        'preview_text': 'Solo se envasa lo que esté reservado. Te lo cuento antes de empezar.',
                    }}},
                    'relationships': {'campaign': {'data': {'id': 'campaign-1', 'type': 'campaign'}}},
                },
            }),
            self.response(200, {
                'data': {'attributes': {
                    'status': 'Draft',
                    'audiences': {'included': ['segment-customers-newsletter'], 'excluded': ['internal-exclusions']},
                    'send_options': {'use_smart_sending': True, 'ignore_unsubscribes': False},
                }},
            }),
            self.response(200, {'data': {'attributes': {'estimated_recipient_count': 72}}}),
        ]
        requests.patch.return_value = self.response(200, {'data': {'id': 'campaign-1'}})
        requests.post.return_value = self.response(202, {
            'data': {'id': 'schedule-1', 'attributes': {
                'status': 'queued',
            }},
        })

        payload = {
            'campaign_id': 'campaign-1',
            'send_at': '2099-10-07T10:00:00+02:00',
            'expected_subject': 'Este año el temprano funciona distinto',
            'expected_preview_text': 'Solo se envasa lo que esté reservado. Te lo cuento antes de empezar.',
            'expected_audience_id': 'segment-customers-newsletter',
            'required_exclusion_ids': ['internal-exclusions'],
        }
        with self.app.test_request_context(method='POST', json=payload):
            response, status = admin_klaviyo_routes.schedule_klaviyo_campaign_message.__wrapped__.__wrapped__('message-1')

        self.assertEqual(status, 201)
        body = response.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['estimated_recipient_count'], 72)
        self.assertTrue(body['smart_sending'])
        self.assertEqual(body['scheduled_send_time'], '2099-10-07T10:00:00+02:00')
        schedule_call = requests.post.call_args
        self.assertEqual(schedule_call.kwargs['headers']['revision'], '2026-07-15')
        self.assertEqual(schedule_call.kwargs['json']['data'], {
            'type': 'campaign-send-job', 'id': 'campaign-1',
        })
        strategy_call = requests.patch.call_args
        self.assertEqual(strategy_call.kwargs['json']['data'], {
            'type': 'campaign',
            'id': 'campaign-1',
            'attributes': {'send_strategy': {
                'method': 'static',
                'datetime': '2099-10-07T10:00:00+02:00',
                'options': {'is_local': False},
            }},
        })

    @patch('src.routes.admin_klaviyo_routes.requests')
    def test_refuses_scheduling_when_recipient_estimate_is_not_ready(self, requests):
        requests.get.side_effect = [
            self.response(200, {
                'data': {
                    'attributes': {'definition': {'channel': 'email', 'content': {
                        'subject': 'Este año el temprano funciona distinto',
                        'preview_text': 'Solo se envasa lo que esté reservado. Te lo cuento antes de empezar.',
                    }}},
                    'relationships': {'campaign': {'data': {'id': 'campaign-1', 'type': 'campaign'}}},
                },
            }),
            self.response(200, {
                'data': {'attributes': {
                    'status': 'Draft',
                    'audiences': {'included': ['segment-customers-newsletter'], 'excluded': ['internal-exclusions']},
                    'send_options': {'use_smart_sending': True, 'ignore_unsubscribes': False},
                }},
            }),
            self.response(404, text='No results or results were out of date.'),
        ]

        payload = {
            'campaign_id': 'campaign-1',
            'send_at': '2099-10-07T10:00:00+02:00',
            'expected_subject': 'Este año el temprano funciona distinto',
            'expected_preview_text': 'Solo se envasa lo que esté reservado. Te lo cuento antes de empezar.',
            'expected_audience_id': 'segment-customers-newsletter',
            'required_exclusion_ids': ['internal-exclusions'],
        }
        with self.app.test_request_context(method='POST', json=payload):
            response, status = admin_klaviyo_routes.schedule_klaviyo_campaign_message.__wrapped__.__wrapped__('message-1')

        self.assertEqual(status, 409)
        self.assertIn('estimación oficial', response.get_json()['error'])
        requests.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
