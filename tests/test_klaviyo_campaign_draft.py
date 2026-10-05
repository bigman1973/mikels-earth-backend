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

        with self.app.test_request_context(json={'subject': 'Ya puedes reservar el temprano de este año'}):
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

        with self.app.test_request_context(json={'subject': 'No debe cambiar'}):
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


if __name__ == '__main__':
    unittest.main()
