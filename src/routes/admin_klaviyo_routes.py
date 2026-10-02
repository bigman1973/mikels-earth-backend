"""
Rutas temporales de admin para actualizar perfiles de Klaviyo.
Se usa porque Cloudflare bloquea las requests directas desde ciertos IPs.
El backend en Railway no tiene ese problema.
"""
from flask import Blueprint, request, jsonify
import os
import requests
import time
from src.routes.auth_routes import admin_required, role_required

admin_klaviyo_bp = Blueprint('admin_klaviyo', __name__)

KLAVIYO_API_URL = "https://a.klaviyo.com/api"
# Templates attached to Flow messages must be updated using the current
# Templates API contract.  The order-event sender has its own revision.
KLAVIYO_REVISION = "2026-07-15"


def _get_klaviyo_headers():
    api_key = os.getenv('KLAVIYO_API_KEY', '').strip()
    return {
        'Authorization': f'Klaviyo-API-Key {api_key}',
        'accept': 'application/vnd.api+json',
        'content-type': 'application/vnd.api+json',
        'revision': KLAVIYO_REVISION
    }


@admin_klaviyo_bp.route('/admin/klaviyo/update-profiles', methods=['POST'])
@admin_required
@role_required('admin')
def update_klaviyo_profiles():
    """
    Actualizar el campo coupon_code en múltiples perfiles de Klaviyo.
    Body: { "profiles": [{"email": "...", "coupon_code": "..."}] }
    """
    data = request.get_json()
    profiles = data.get('profiles', [])
    
    if not profiles:
        return jsonify({'error': 'No profiles provided'}), 400
    
    results = []
    headers = _get_klaviyo_headers()
    
    for p in profiles:
        email = p.get('email')
        coupon_code = p.get('coupon_code')
        
        if not email or not coupon_code:
            results.append({'email': email, 'status': 'error', 'message': 'Missing email or coupon_code'})
            continue
        
        try:
            # Usar el endpoint de crear/actualizar perfil por email
            # POST /api/profiles crea o devuelve 409 si existe
            # Usamos el endpoint de profile-import que crea o actualiza
            payload = {
                "data": {
                    "type": "profile",
                    "attributes": {
                        "email": email,
                        "properties": {
                            "coupon_code": coupon_code
                        }
                    }
                }
            }
            
            # Primero intentar crear (actualiza propiedades si ya existe con 409)
            resp = requests.post(
                f"{KLAVIYO_API_URL}/profile-import",
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if resp.status_code in [200, 201, 202, 204]:
                results.append({'email': email, 'status': 'ok'})
            else:
                # Si profile-import no funciona, intentar con profiles endpoint
                resp2 = requests.post(
                    f"{KLAVIYO_API_URL}/profiles",
                    headers=headers,
                    json=payload,
                    timeout=10
                )
                if resp2.status_code in [200, 201, 202, 204, 409]:
                    results.append({'email': email, 'status': 'ok'})
                else:
                    results.append({'email': email, 'status': 'error', 'message': f'{resp2.status_code}: {resp2.text[:100]}'})
        except Exception as e:
            results.append({'email': email, 'status': 'error', 'message': str(e)})
    
    ok_count = len([r for r in results if r['status'] == 'ok'])
    error_count = len([r for r in results if r['status'] == 'error'])
    
    return jsonify({
        'total': len(profiles),
        'ok': ok_count,
        'errors': error_count,
        'results': results
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/create-campaign', methods=['POST'])
@admin_required
@role_required('admin')
def create_klaviyo_campaign():
    """
    Crear un template y una campaña en Klaviyo (en estado DRAFT).
    Body: {
        "template_name": "...",
        "template_html": "...",
        "campaign_name": "...",
        "subject": "...",
        "preview_text": "...",
        "list_id": "WWPsb2",
        "from_email": "jordi@mikels.es",
        "from_name": "MIKEL'S EARTH"
    }
    """
    data = request.get_json()
    headers = _get_klaviyo_headers()
    
    try:
        # Paso 1: Crear template
        template_payload = {
            "data": {
                "type": "template",
                "attributes": {
                    "name": data['template_name'],
                    "html": data['template_html'],
                    "editor_type": "CODE"
                }
            }
        }
        
        resp = requests.post(
            f"{KLAVIYO_API_URL}/templates",
            headers=headers,
            json=template_payload,
            timeout=15
        )
        
        if resp.status_code not in [200, 201]:
            return jsonify({
                'error': f'Error creating template: {resp.status_code}',
                'detail': resp.text[:300]
            }), 500
        
        template_id = resp.json()['data']['id']
        
        # Paso 2: Crear campaña
        campaign_payload = {
            "data": {
                "type": "campaign",
                "attributes": {
                    "name": data['campaign_name'],
                    "audiences": {
                        "included": [data['list_id']],
                        "excluded": []
                    },
                    "send_strategy": {
                        "method": "immediate"
                    },
                    "campaign-messages": {
                        "data": [{
                            "type": "campaign-message",
                            "attributes": {
                                "channel": "email",
                                "label": "Email",
                                "content": {
                                    "subject": data['subject'],
                                    "preview_text": data.get('preview_text', ''),
                                    "from_email": data.get('from_email', 'jordi@mikels.es'),
                                    "from_label": data.get('from_name', "MIKEL'S EARTH")
                                },
                                "render_options": {
                                    "shorten_links": True,
                                    "add_org_prefix": True,
                                    "add_info_link": True,
                                    "add_opt_out_link": True
                                }
                            },
                            "relationships": {
                                "template": {
                                    "data": {
                                        "type": "template",
                                        "id": template_id
                                    }
                                }
                            }
                        }]
                    }
                }
            }
        }
        
        resp2 = requests.post(
            f"{KLAVIYO_API_URL}/campaigns",
            headers=headers,
            json=campaign_payload,
            timeout=15
        )
        
        if resp2.status_code not in [200, 201]:
            return jsonify({
                'error': f'Error creating campaign: {resp2.status_code}',
                'detail': resp2.text[:500],
                'template_id': template_id
            }), 500
        
        campaign_data = resp2.json()['data']
        
        return jsonify({
            'success': True,
            'template_id': template_id,
            'campaign_id': campaign_data['id'],
            'campaign_name': data['campaign_name'],
            'status': 'DRAFT',
            'message': 'Campaña creada en estado DRAFT. Ve a Klaviyo para revisarla y enviarla.'
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_klaviyo_bp.route('/admin/klaviyo/list-templates', methods=['GET'])
@admin_required
@role_required('admin')
def list_klaviyo_templates():
    """Listar todos los templates de Klaviyo"""
    headers = _get_klaviyo_headers()
    # The Templates endpoint returns oldest-first unless an explicit sort is
    # supplied.  The newest revision is the one an administrator needs when
    # recovering an interrupted Flow setup.
    resp = requests.get(
        f"{KLAVIYO_API_URL}/templates",
        headers=headers,
        params={'sort': '-updated', 'page[size]': 50},
        timeout=20,
    )
    
    if resp.status_code == 200:
        data = resp.json()
        templates = []
        for t in data.get('data', []):
            templates.append({
                'id': t['id'],
                'name': t.get('attributes', {}).get('name', ''),
                'updated': t.get('attributes', {}).get('updated', '')
            })
        return jsonify({'templates': templates}), 200
    else:
        return jsonify({'error': resp.text}), resp.status_code


@admin_klaviyo_bp.route('/admin/klaviyo/get-template/<template_id>', methods=['GET'])
@admin_required
@role_required('admin')
def get_klaviyo_template(template_id):
    """Obtener el HTML de un template específico"""
    headers = _get_klaviyo_headers()
    resp = requests.get(f"{KLAVIYO_API_URL}/templates/{template_id}", headers=headers)
    
    if resp.status_code == 200:
        data = resp.json()
        attrs = data.get('data', {}).get('attributes', {})
        return jsonify({
            'id': template_id,
            'name': attrs.get('name', ''),
            'html': attrs.get('html', ''),
            'text': attrs.get('text', '')
        }), 200
    else:
        return jsonify({'error': resp.text}), resp.status_code


@admin_klaviyo_bp.route('/admin/klaviyo/update-template/<template_id>', methods=['PUT'])
@admin_required
@role_required('admin')
def update_klaviyo_template(template_id):
    """Actualizar el HTML de un template"""
    body = request.get_json()
    new_html = body.get('html')
    new_name = body.get('name')
    
    if not new_html:
        return jsonify({'error': 'html field required'}), 400
    
    headers = _get_klaviyo_headers()
    
    payload = {
        "data": {
            "type": "template",
            "id": template_id,
            "attributes": {
                "html": new_html
            }
        }
    }
    
    if new_name:
        payload["data"]["attributes"]["name"] = new_name
    
    resp = requests.patch(f"{KLAVIYO_API_URL}/templates/{template_id}", headers=headers, json=payload)
    
    if resp.status_code in [200, 204]:
        return jsonify({'success': True, 'message': 'Template updated'}), 200
    else:
        return jsonify({'error': resp.text, 'status': resp.status_code}), resp.status_code


@admin_klaviyo_bp.route('/admin/klaviyo/create-template', methods=['POST'])
@admin_required
@role_required('admin')
def create_klaviyo_template():
    """Crear un nuevo template en Klaviyo"""
    body = request.get_json()
    name = body.get('name')
    html = body.get('html')
    
    if not name or not html:
        return jsonify({'error': 'name and html fields required'}), 400
    
    headers = _get_klaviyo_headers()
    
    payload = {
        "data": {
            "type": "template",
            "attributes": {
                "name": name,
                "editor_type": "CODE",
                "html": html
            }
        }
    }
    
    resp = requests.post(f"{KLAVIYO_API_URL}/templates", headers=headers, json=payload)
    
    if resp.status_code in [200, 201]:
        data = resp.json()
        template_id = data.get('data', {}).get('id')
        return jsonify({'success': True, 'template_id': template_id}), 200
    else:
        return jsonify({'error': resp.text, 'status': resp.status_code}), resp.status_code


@admin_klaviyo_bp.route('/admin/klaviyo/list-flows', methods=['GET'])
@admin_required
@role_required('admin')
def list_klaviyo_flows():
    """Listar todos los flows de Klaviyo"""
    headers = _get_klaviyo_headers()
    resp = requests.get(f"{KLAVIYO_API_URL}/flows", headers=headers)
    
    if resp.status_code == 200:
        data = resp.json()
        flows = []
        for f in data.get('data', []):
            flows.append({
                'id': f['id'],
                'name': f.get('attributes', {}).get('name', ''),
                'status': f.get('attributes', {}).get('status', ''),
                'trigger_type': f.get('attributes', {}).get('trigger_type', '')
            })
        return jsonify({'flows': flows}), 200
    else:
        return jsonify({'error': resp.text}), resp.status_code


@admin_klaviyo_bp.route('/admin/klaviyo/setup-cancellation-flow', methods=['POST'])
@admin_required
@role_required('admin')
def setup_cancellation_flow():
    """Create one live transactional Flow for full-refund cancellation events.

    Klaviyo creates API metrics when it first accepts an event. If this metric
    has not yet existed, this route seeds it against the owner profile before
    creating the Flow, so the first real customer refund cannot be lost during
    metric discovery. The bootstrap event is emitted before the Flow exists,
    therefore it cannot send email.
    """
    body = request.get_json() or {}
    template_id = str(body.get('template_id') or '').strip()
    if not template_id:
        return jsonify({'error': 'template_id field required'}), 400

    metric_name = 'Mikels Order Cancelled'
    flow_name = "Anulación de pedido · Mikel's Fruit"
    headers = _get_klaviyo_headers()

    # Klaviyo does not expose Flow ``name`` as a server-side filterable
    # attribute.  Retrieve the first page and compare the stable exact name
    # locally to keep setup idempotent.
    existing_flow = requests.get(
        f'{KLAVIYO_API_URL}/flows',
        headers=headers,
        params={'page[size]': 50},
        timeout=20,
    )
    if existing_flow.status_code == 200:
        matches = [
            flow for flow in existing_flow.json().get('data', [])
            if flow.get('attributes', {}).get('name') == flow_name
        ]
        if matches:
            flow = matches[0]
            return jsonify({
                'success': True,
                'created': False,
                'flow_id': flow.get('id'),
                'flow_name': flow.get('attributes', {}).get('name', flow_name),
                'status': flow.get('attributes', {}).get('status'),
                'message': 'El Flow de anulación ya existe; no se creó un duplicado.',
            }), 200
    else:
        return jsonify({'error': existing_flow.text, 'status': existing_flow.status_code}), existing_flow.status_code

    metric_id = None
    # Keep this local too: filtering by name varies by API revision and a
    # rejected filter must never prevent a cancellation notice from being set
    # up before the first real refund.
    metrics = requests.get(
        f'{KLAVIYO_API_URL}/metrics',
        headers=headers,
        params={'page[size]': 50},
        timeout=20,
    )
    if metrics.status_code == 200:
        matches = [
            metric for metric in metrics.json().get('data', [])
            if metric.get('attributes', {}).get('name') == metric_name
        ]
        if matches:
            metric_id = matches[0].get('id')
    else:
        return jsonify({'error': metrics.text, 'status': metrics.status_code}), metrics.status_code

    metric_bootstrapped = False
    if not metric_id:
        # No customer address is used. This only registers the API metric in
        # Klaviyo's metric catalog before a live transactional Flow exists.
        from src.services.klaviyo_service import send_klaviyo_event
        owner_email = os.getenv('OWNER_EMAIL', 'info@mikels.es').strip() or 'info@mikels.es'
        accepted, error = send_klaviyo_event(
            metric_name=metric_name,
            profile_email=owner_email,
            properties={'setup_only': True, 'Source': 'mikels-earth-backend'},
            unique_id='mikels-order-cancelled-metric-bootstrap-v1',
            return_result=True,
        )
        if not accepted:
            return jsonify({'error': error or 'Klaviyo no aceptó el evento de registro de métrica'}), 502
        metric_bootstrapped = True
        for _ in range(10):
            time.sleep(1)
            metrics = requests.get(
                f'{KLAVIYO_API_URL}/metrics',
                headers=headers,
                params={'page[size]': 50},
                timeout=20,
            )
            if metrics.status_code == 200:
                matches = [
                    metric for metric in metrics.json().get('data', [])
                    if metric.get('attributes', {}).get('name') == metric_name
                ]
                if matches:
                    metric_id = matches[0].get('id')
                    break
        if not metric_id:
            return jsonify({'error': 'La métrica de anulación no apareció en Klaviyo tras el registro'}), 502

    definition = {
        'triggers': [{'type': 'metric', 'id': metric_id, 'trigger_filter': None}],
        'profile_filter': None,
        'actions': [{
            'temporary_id': 'cancellation-email',
            'type': 'send-email',
            'data': {
                'status': 'live',
                'message': {
                    'name': 'Aviso de anulación',
                    'from_email': 'info@mikels.es',
                    'from_label': "Mikel's Fruit",
                    'reply_to_email': 'info@mikels.es',
                    'subject_line': "{{ event.Receipt.heading|default:'Pedido anulado' }} · {{ event.Receipt.order_number }}",
                    'preview_text': "{{ event.Receipt.cancellation.refunded_amount_display }} devueltos.",
                    'template_id': template_id,
                    'smart_sending_enabled': False,
                    'transactional': True,
                    'add_tracking_params': False,
                    'additional_filters': None,
                },
            },
        }],
        'entry_action_id': 'cancellation-email',
        # Each refund is unique per order; re-entry permits a returning
        # customer to receive a later, different order's cancellation.
        'reentry_criteria': {'duration': 0, 'unit': 'alltime'},
    }
    payload = {
        'data': {
            'type': 'flow',
            'attributes': {'name': flow_name, 'definition': definition},
        },
    }
    response = requests.post(
        f'{KLAVIYO_API_URL}/flows', headers=headers, json=payload, timeout=30,
    )
    if response.status_code not in [200, 201, 202]:
        return jsonify({'error': response.text, 'status': response.status_code}), response.status_code

    flow = response.json().get('data', {})
    flow_attrs = flow.get('attributes', {})
    actions = (flow_attrs.get('definition') or {}).get('actions') or []
    action = actions[0] if actions else {}
    message = (action.get('data') or {}).get('message') or {}
    return jsonify({
        'success': True,
        'created': True,
        'metric_name': metric_name,
        'metric_id': metric_id,
        'metric_bootstrapped': metric_bootstrapped,
        'flow_id': flow.get('id'),
        'flow_name': flow_attrs.get('name', flow_name),
        'flow_status': flow_attrs.get('status'),
        'action_id': action.get('id'),
        'message_id': message.get('id'),
        'transactional': message.get('transactional'),
        'template_id': template_id,
    }), 201


@admin_klaviyo_bp.route('/admin/klaviyo/flow-actions/<flow_id>', methods=['GET'])
@admin_required
@role_required('admin')
def get_flow_actions(flow_id):
    """Obtener las acciones de un flow específico"""
    headers = _get_klaviyo_headers()
    resp = requests.get(f"{KLAVIYO_API_URL}/flows/{flow_id}/flow-actions", headers=headers)
    
    if resp.status_code == 200:
        data = resp.json()
        actions = []
        for a in data.get('data', []):
            actions.append({
                'id': a['id'],
                'type': a.get('attributes', {}).get('action_type', ''),
                'settings': a.get('attributes', {}).get('settings', {})
            })
        return jsonify({'actions': actions}), 200
    else:
        return jsonify({'error': resp.text}), resp.status_code


@admin_klaviyo_bp.route('/admin/klaviyo/flow-action/<action_id>', methods=['GET'])
@admin_required
@role_required('admin')
def get_flow_action(action_id):
    """Read the complete settings of one Flow action before a safe update."""
    headers = _get_klaviyo_headers()
    resp = requests.get(f"{KLAVIYO_API_URL}/flow-actions/{action_id}", headers=headers)
    if resp.status_code != 200:
        return jsonify({'error': resp.text, 'status': resp.status_code}), resp.status_code

    data = resp.json().get('data', {})
    attrs = data.get('attributes', {})
    return jsonify({
        'id': data.get('id'),
        'action_type': attrs.get('action_type'),
        'definition': attrs.get('definition'),
        'settings': attrs.get('settings') or {},
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/flow-message/<message_id>/template', methods=['GET'])
@admin_required
@role_required('admin')
def get_flow_message_template(message_id):
    """Resolve the template currently attached to a Flow message."""
    headers = _get_klaviyo_headers()
    resp = requests.get(
        f"{KLAVIYO_API_URL}/flow-messages/{message_id}/template",
        headers=headers,
    )
    if resp.status_code != 200:
        return jsonify({'error': resp.text, 'status': resp.status_code}), resp.status_code

    data = resp.json().get('data', {})
    attrs = data.get('attributes', {})
    return jsonify({
        'id': data.get('id'),
        'name': attrs.get('name', ''),
        'html': attrs.get('html', ''),
        'text': attrs.get('text', ''),
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/update-flow-action/<action_id>', methods=['PUT'])
@admin_required
@role_required('admin')
def update_flow_action(action_id):
    """Replace a full Flow action definition after an authenticated readback."""
    body = request.get_json()
    definition = body.get('definition')
    if not definition:
        return jsonify({'error': 'definition field required'}), 400
    
    headers = _get_klaviyo_headers()
    
    payload = {
        "data": {
            "type": "flow-action",
            "id": action_id,
            "attributes": {
                "definition": definition
            }
        }
    }
    
    resp = requests.patch(f"{KLAVIYO_API_URL}/flow-actions/{action_id}", headers=headers, json=payload)
    
    if resp.status_code in [200, 204]:
        return jsonify({'success': True}), 200
    else:
        return jsonify({'error': resp.text, 'status': resp.status_code}), resp.status_code
