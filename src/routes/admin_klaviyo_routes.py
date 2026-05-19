"""
Rutas temporales de admin para actualizar perfiles de Klaviyo.
Se usa porque Cloudflare bloquea las requests directas desde ciertos IPs.
El backend en Railway no tiene ese problema.
"""
from flask import Blueprint, request, jsonify
import os
import requests

admin_klaviyo_bp = Blueprint('admin_klaviyo', __name__)

KLAVIYO_API_URL = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-10-15"
ADMIN_KEY = "mikels-admin-2026"


def _get_klaviyo_headers():
    api_key = os.getenv('KLAVIYO_API_KEY', '').strip()
    return {
        'Authorization': f'Klaviyo-API-Key {api_key}',
        'accept': 'application/vnd.api+json',
        'content-type': 'application/vnd.api+json',
        'revision': KLAVIYO_REVISION
    }


@admin_klaviyo_bp.route('/admin/klaviyo/update-profiles', methods=['POST'])
def update_klaviyo_profiles():
    """
    Actualizar el campo coupon_code en múltiples perfiles de Klaviyo.
    Body: { "profiles": [{"email": "...", "coupon_code": "..."}] }
    Header: X-Admin-Key: mikels-admin-2026
    """
    # Verificar admin key
    admin_key = request.headers.get('X-Admin-Key', '')
    if admin_key != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
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
    admin_key = request.headers.get('X-Admin-Key', '')
    if admin_key != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
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
def list_klaviyo_templates():
    """Listar todos los templates de Klaviyo"""
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
    headers = _get_klaviyo_headers()
    resp = requests.get(f"{KLAVIYO_API_URL}/templates", headers=headers)
    
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
def get_klaviyo_template(template_id):
    """Obtener el HTML de un template específico"""
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
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
def update_klaviyo_template(template_id):
    """Actualizar el HTML de un template"""
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
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
def create_klaviyo_template():
    """Crear un nuevo template en Klaviyo"""
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
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
def list_klaviyo_flows():
    """Listar todos los flows de Klaviyo"""
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
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


@admin_klaviyo_bp.route('/admin/klaviyo/flow-actions/<flow_id>', methods=['GET'])
def get_flow_actions(flow_id):
    """Obtener las acciones de un flow específico"""
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
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


@admin_klaviyo_bp.route('/admin/klaviyo/update-flow-action/<action_id>', methods=['PUT'])
def update_flow_action(action_id):
    """Actualizar los settings de un flow action (para cambiar template)"""
    if request.headers.get('X-Admin-Key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    
    body = request.get_json()
    
    headers = _get_klaviyo_headers()
    
    payload = {
        "data": {
            "type": "flow-action",
            "id": action_id,
            "attributes": {
                "settings": body.get('settings', {})
            }
        }
    }
    
    resp = requests.patch(f"{KLAVIYO_API_URL}/flow-actions/{action_id}", headers=headers, json=payload)
    
    if resp.status_code in [200, 204]:
        return jsonify({'success': True}), 200
    else:
        return jsonify({'error': resp.text, 'status': resp.status_code}), resp.status_code
