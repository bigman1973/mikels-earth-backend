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


def _klaviyo_detail(response, limit=500):
    """Return a bounded upstream diagnostic without exposing credentials."""
    return getattr(response, 'text', '')[:limit]


def _get_draft_campaign_or_error(campaign_id, headers):
    """Return a Klaviyo Draft campaign or a Flask error response tuple."""
    try:
        response = requests.get(
            f"{KLAVIYO_API_URL}/campaigns/{campaign_id}",
            headers=headers,
            params={'fields[campaign]': 'status,audiences'},
            timeout=20,
        )
    except requests.RequestException as exc:
        return None, (jsonify({'error': f'No se pudo comprobar el estado de la campaña: {exc}'}), 502)
    if response.status_code != 200:
        return None, (jsonify({
            'error': f'Klaviyo no devolvió la campaña: {response.status_code}',
            'detail': _klaviyo_detail(response),
        }), 502)
    campaign = response.json().get('data', {})
    status = str((campaign.get('attributes', {}) or {}).get('status', '')).lower()
    if status != 'draft':
        return None, (jsonify({
            'error': f'La campaña debe estar en Draft para editarse; estado actual: {status or "desconocido"}',
        }), 409)
    return campaign, None


@admin_klaviyo_bp.route('/admin/klaviyo/profiles', methods=['GET'])
@admin_required
@role_required('admin')
def lookup_klaviyo_profiles():
    """Read only the minimal profile fields needed for an admin preview or exclusion."""
    raw_emails = request.args.get('emails', '')
    emails = [item.strip().lower() for item in raw_emails.split(',') if item.strip()]
    if not emails or len(emails) > 10 or any('@' not in email for email in emails):
        return jsonify({'error': 'emails debe contener entre 1 y 10 direcciones válidas'}), 400

    headers = _get_klaviyo_headers()
    profiles = []
    for email in emails:
        try:
            response = requests.get(
                f"{KLAVIYO_API_URL}/profiles/",
                headers=headers,
                params={
                    'filter': f"equals(email,'{email}')",
                    'fields[profile]': 'email,first_name,last_name',
                    'page[size]': 1,
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            return jsonify({'error': f'No se pudo leer el perfil: {exc}'}), 502
        if response.status_code != 200:
            return jsonify({
                'error': f'Klaviyo no devolvió el perfil: {response.status_code}',
                'detail': _klaviyo_detail(response),
            }), 502
        data = response.json().get('data', [])
        if not data:
            profiles.append({'email': email, 'found': False})
            continue
        profile = data[0]
        attributes = profile.get('attributes', {}) or {}
        profiles.append({
            'id': profile.get('id'),
            'email': attributes.get('email', email),
            'first_name': attributes.get('first_name'),
            'last_name': attributes.get('last_name'),
            'found': True,
        })
    return jsonify({'profiles': profiles}), 200


@admin_klaviyo_bp.route('/admin/klaviyo/campaign-audiences', methods=['GET'])
@admin_required
@role_required('admin')
def list_campaign_audiences():
    """Read available Klaviyo lists and segments before choosing campaign scope."""
    headers = _get_klaviyo_headers()
    # Klaviyo's lists and segments endpoints cap a single page at ten items.
    # This account has a small set of campaign audiences; the explicit limit
    # keeps the read valid and avoids guessing a recipient group.
    params = {'page[size]': 10, 'sort': 'name'}
    try:
        lists_response = requests.get(f"{KLAVIYO_API_URL}/lists", headers=headers, params=params, timeout=20)
        segments_response = requests.get(f"{KLAVIYO_API_URL}/segments", headers=headers, params=params, timeout=20)
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudieron leer las audiencias: {exc}'}), 502

    if lists_response.status_code != 200 or segments_response.status_code != 200:
        return jsonify({
            'error': 'No se pudieron leer las audiencias de Klaviyo',
            'lists_status': lists_response.status_code,
            'segments_status': segments_response.status_code,
            'detail': _klaviyo_detail(lists_response if lists_response.status_code != 200 else segments_response),
        }), 502

    def serialize(items, audience_type):
        return [{
            'id': item.get('id'),
            'name': item.get('attributes', {}).get('name', ''),
            'type': audience_type,
        } for item in items]

    return jsonify({
        'lists': serialize(lists_response.json().get('data', []), 'list'),
        'segments': serialize(segments_response.json().get('data', []), 'segment'),
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/campaign/<campaign_id>/internal-exclusions', methods=['POST'])
@admin_required
@role_required('admin')
def set_klaviyo_campaign_internal_exclusions(campaign_id):
    """Exclude specified internal profiles from one Draft campaign without changing consent."""
    body = request.get_json(silent=True) or {}
    emails = [str(item).strip().lower() for item in (body.get('emails') or []) if str(item).strip()]
    list_name = str(body.get('list_name') or 'Excluir campaña Temprano 2026/27').strip()
    if not emails or len(emails) > 25 or len(set(emails)) != len(emails) or any('@' not in email for email in emails):
        return jsonify({'error': 'emails debe contener direcciones únicas y válidas'}), 400
    if not list_name:
        return jsonify({'error': 'list_name es obligatorio'}), 400

    headers = _get_klaviyo_headers()
    campaign, campaign_error = _get_draft_campaign_or_error(campaign_id, headers)
    if campaign_error:
        return campaign_error

    profiles = []
    missing_emails = []
    for email in emails:
        try:
            profile_response = requests.get(
                f"{KLAVIYO_API_URL}/profiles/",
                headers=headers,
                params={'filter': f"equals(email,'{email}')", 'fields[profile]': 'email', 'page[size]': 1},
                timeout=20,
            )
        except requests.RequestException as exc:
            return jsonify({'error': f'No se pudo leer un perfil de exclusión: {exc}'}), 502
        if profile_response.status_code != 200:
            return jsonify({
                'error': f'Klaviyo no devolvió un perfil de exclusión: {profile_response.status_code}',
                'detail': _klaviyo_detail(profile_response),
            }), 502
        found = profile_response.json().get('data', [])
        if not found:
            missing_emails.append(email)
        else:
            profiles.append(found[0])
    if missing_emails:
        return jsonify({
            'error': 'No se ha cambiado la campaña porque faltan perfiles internos en Klaviyo',
            'missing_emails': missing_emails,
        }), 422

    try:
        lists_response = requests.get(
            f"{KLAVIYO_API_URL}/lists", headers=headers, params={'page[size]': 10, 'sort': 'name'}, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudieron leer las listas de exclusión: {exc}'}), 502
    if lists_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo no devolvió las listas: {lists_response.status_code}',
            'detail': _klaviyo_detail(lists_response),
        }), 502
    matching_list = next((item for item in lists_response.json().get('data', [])
                          if (item.get('attributes', {}) or {}).get('name') == list_name), None)
    if matching_list:
        exclusion_list_id = matching_list.get('id')
        list_created = False
    else:
        list_payload = {'data': {'type': 'list', 'attributes': {'name': list_name}}}
        try:
            create_list_response = requests.post(f"{KLAVIYO_API_URL}/lists", headers=headers, json=list_payload, timeout=20)
        except requests.RequestException as exc:
            return jsonify({'error': f'No se pudo crear la lista de exclusión: {exc}'}), 502
        if create_list_response.status_code not in (200, 201):
            return jsonify({
                'error': f'Klaviyo rechazó la lista de exclusión: {create_list_response.status_code}',
                'detail': _klaviyo_detail(create_list_response),
            }), 502
        exclusion_list_id = create_list_response.json().get('data', {}).get('id')
        list_created = True

    member_payload = {'data': [{'type': 'profile', 'id': profile.get('id')} for profile in profiles]}
    try:
        members_response = requests.post(
            f"{KLAVIYO_API_URL}/lists/{exclusion_list_id}/relationships/profiles",
            headers=headers,
            json=member_payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudieron añadir los perfiles a la exclusión: {exc}'}), 502
    if members_response.status_code not in (200, 204):
        return jsonify({
            'error': f'Klaviyo rechazó los perfiles de exclusión: {members_response.status_code}',
            'detail': _klaviyo_detail(members_response),
            'exclusion_list_id': exclusion_list_id,
        }), 502

    attributes = campaign.get('attributes', {}) or {}
    audiences = attributes.get('audiences') or {}
    excluded = list(audiences.get('excluded') or [])
    if exclusion_list_id not in excluded:
        excluded.append(exclusion_list_id)
    campaign_payload = {
        'data': {
            'type': 'campaign',
            'id': campaign_id,
            'attributes': {'audiences': {'excluded': excluded}},
        },
    }
    try:
        campaign_update_response = requests.patch(
            f"{KLAVIYO_API_URL}/campaigns/{campaign_id}", headers=headers, json=campaign_payload, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo actualizar la exclusión de la campaña: {exc}'}), 502
    if campaign_update_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo rechazó la exclusión de la campaña: {campaign_update_response.status_code}',
            'detail': _klaviyo_detail(campaign_update_response),
            'exclusion_list_id': exclusion_list_id,
        }), 502
    return jsonify({
        'success': True,
        'campaign_id': campaign_id,
        'campaign_status': 'Draft',
        'exclusion_list_id': exclusion_list_id,
        'exclusion_list_created': list_created,
        'excluded_emails': emails,
        'message': 'Perfiles internos excluidos sin modificar sus estados de suscripción.',
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/campaign/<campaign_id>/recipient-estimation', methods=['POST'])
@admin_required
@role_required('admin')
def refresh_klaviyo_campaign_recipient_estimation(campaign_id):
    """Refresh and read the estimated audience for a Draft campaign without sending it."""
    headers = _get_klaviyo_headers()
    _, campaign_error = _get_draft_campaign_or_error(campaign_id, headers)
    if campaign_error:
        return campaign_error

    payload = {'data': {'type': 'campaign-recipient-estimation-job', 'id': campaign_id}}
    try:
        job_response = requests.post(f"{KLAVIYO_API_URL}/campaign-recipient-estimation-jobs", headers=headers, json=payload, timeout=20)
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo solicitar la estimación: {exc}'}), 502
    if job_response.status_code != 202:
        return jsonify({
            'error': f'Klaviyo rechazó la estimación: {job_response.status_code}',
            'detail': _klaviyo_detail(job_response),
        }), 502
    job_id = job_response.json().get('data', {}).get('id')
    job_status = job_response.json().get('data', {}).get('attributes', {}).get('status')
    # Klaviyo can accept this job before its status resource exists; its 404
    # specifically means the estimation is still out of date, not that the
    # campaign was changed or sent. Wait briefly and read again.
    for _ in range(15):
        if job_status == 'complete':
            break
        time.sleep(1)
        try:
            job_read_response = requests.get(
                f"{KLAVIYO_API_URL}/campaign-recipient-estimation-jobs/{job_id}", headers=headers, timeout=20,
            )
        except requests.RequestException as exc:
            return jsonify({'error': f'No se pudo leer la estimación: {exc}', 'job_id': job_id}), 502
        if job_read_response.status_code == 404:
            continue
        if job_read_response.status_code != 200:
            return jsonify({
                'error': f'Klaviyo no devolvió el estado de estimación: {job_read_response.status_code}',
                'detail': _klaviyo_detail(job_read_response),
                'job_id': job_id,
            }), 502
        job_status = job_read_response.json().get('data', {}).get('attributes', {}).get('status')
        if job_status in ('cancelled', 'failed'):
            return jsonify({'error': f'La estimación no se completó: {job_status}', 'job_id': job_id}), 502
    if job_status != 'complete':
        return jsonify({'error': 'La estimación sigue en proceso; vuelve a consultar después', 'job_id': job_id}), 202
    try:
        estimation_response = requests.get(
            f"{KLAVIYO_API_URL}/campaign-recipient-estimations/{campaign_id}", headers=headers, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo leer el total estimado: {exc}', 'job_id': job_id}), 502
    if estimation_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo no devolvió el total estimado: {estimation_response.status_code}',
            'detail': _klaviyo_detail(estimation_response),
            'job_id': job_id,
        }), 502
    count = (estimation_response.json().get('data', {}).get('attributes', {}) or {}).get('estimated_recipient_count')
    return jsonify({
        'campaign_id': campaign_id,
        'campaign_status': 'Draft',
        'job_id': job_id,
        'estimated_recipient_count': count,
        'message': 'Estimación actualizada; no se ha programado ni enviado la campaña.',
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/import-campaign-image', methods=['POST'])
@admin_required
@role_required('admin')
def import_campaign_image():
    """Import a public image into Klaviyo's own asset library for a campaign."""
    data = request.get_json(silent=True) or {}
    image_url = str(data.get('image_url', '')).strip()
    image_name = str(data.get('name', '')).strip()
    if not image_url.startswith('https://'):
        return jsonify({'error': 'image_url debe ser una URL pública HTTPS'}), 400

    payload = {
        'data': {
            'type': 'image',
            'attributes': {
                'import_from_url': image_url,
                'name': image_name or 'Mikel’s Fruit campaign image',
                'hidden': False,
            },
        },
    }
    try:
        response = requests.post(f"{KLAVIYO_API_URL}/images", headers=_get_klaviyo_headers(), json=payload, timeout=30)
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo importar la imagen: {exc}'}), 502

    if response.status_code not in (200, 201):
        return jsonify({
            'error': f'Klaviyo rechazó la imagen: {response.status_code}',
            'detail': _klaviyo_detail(response),
        }), 502

    image = response.json().get('data', {})
    attributes = image.get('attributes', {})
    return jsonify({
        'success': True,
        'image_id': image.get('id'),
        'image_url': attributes.get('image_url'),
        'name': attributes.get('name'),
    }), 201


@admin_klaviyo_bp.route('/admin/klaviyo/create-campaign', methods=['POST'])
@admin_required
@role_required('admin')
def create_klaviyo_campaign():
    """Create a fully configured Klaviyo email campaign as a non-scheduled draft."""
    data = request.get_json(silent=True) or {}
    required_fields = ('template_name', 'template_html', 'campaign_name', 'subject')
    missing = [field for field in required_fields if not str(data.get(field, '')).strip()]
    included = data.get('included_audiences') or ([data['list_id']] if data.get('list_id') else [])
    excluded = data.get('excluded_audiences') or []

    if missing:
        return jsonify({'error': f'Campos obligatorios ausentes: {", ".join(missing)}'}), 400
    if not isinstance(included, list) or not included or not all(isinstance(item, str) and item.strip() for item in included):
        return jsonify({'error': 'included_audiences debe contener al menos una lista o segmento de Klaviyo'}), 400
    if not isinstance(excluded, list) or not all(isinstance(item, str) and item.strip() for item in excluded):
        return jsonify({'error': 'excluded_audiences debe contener solo IDs de listas o segmentos de Klaviyo'}), 400

    headers = _get_klaviyo_headers()
    sender_email = data.get('from_email', 'jordi@mikels.es')
    sender_name = data.get('from_name', "Jordi · Mikel's Fruit")
    reply_to_email = data.get('reply_to_email', sender_email)
    tracking = data.get('tracking_options') or {
        'add_tracking_params': True,
        'custom_tracking_params': [
            {'type': 'static', 'name': 'utm_source', 'value': 'klaviyo'},
            {'type': 'static', 'name': 'utm_medium', 'value': 'email'},
            {'type': 'static', 'name': 'utm_campaign', 'value': 'temprano_2026_27_reserva'},
        ],
        'is_tracking_clicks': True,
        'is_tracking_opens': True,
    }

    template_payload = {
        'data': {
            'type': 'template',
            'attributes': {
                'name': data['template_name'],
                'html': data['template_html'],
                'editor_type': 'CODE',
            },
        },
    }

    try:
        template_response = requests.post(
            f"{KLAVIYO_API_URL}/templates", headers=headers, json=template_payload, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo crear el template: {exc}'}), 502

    if template_response.status_code not in (200, 201):
        return jsonify({
            'error': f'Klaviyo rechazó el template: {template_response.status_code}',
            'detail': _klaviyo_detail(template_response),
        }), 502
    template_id = template_response.json()['data']['id']

    campaign_payload = {
        'data': {
            'type': 'campaign',
            'attributes': {
                'name': data['campaign_name'],
                'audiences': {'included': included, 'excluded': excluded},
                'send_strategy': {'method': 'immediate'},
                'send_options': {'use_smart_sending': data.get('use_smart_sending', True)},
                'tracking_options': tracking,
                'campaign-messages': {
                    'data': [{
                        'type': 'campaign-message',
                        'attributes': {
                            'definition': {
                                'channel': 'email',
                                'label': data.get('message_label', 'Email'),
                                'content': {
                                    'subject': data['subject'],
                                    'preview_text': data.get('preview_text', ''),
                                    'from_email': sender_email,
                                    'from_label': sender_name,
                                    'reply_to_email': reply_to_email,
                                },
                            },
                        },
                    }],
                },
            },
        },
    }

    try:
        campaign_response = requests.post(
            f"{KLAVIYO_API_URL}/campaigns", headers=headers, json=campaign_payload, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo crear la campaña: {exc}', 'template_id': template_id}), 502

    if campaign_response.status_code not in (200, 201):
        return jsonify({
            'error': f'Klaviyo rechazó la campaña: {campaign_response.status_code}',
            'detail': _klaviyo_detail(campaign_response),
            'template_id': template_id,
        }), 502

    campaign = campaign_response.json().get('data', {})
    campaign_id = campaign.get('id')
    messages = campaign.get('relationships', {}).get('campaign-messages', {}).get('data', [])
    if not messages:
        relationship_response = requests.get(
            f"{KLAVIYO_API_URL}/campaigns/{campaign_id}/relationships/campaign-messages/", headers=headers, timeout=20,
        )
        if relationship_response.status_code != 200:
            return jsonify({
                'error': 'La campaña se creó, pero no se pudo identificar su mensaje para asignar el template',
                'campaign_id': campaign_id,
                'template_id': template_id,
                'detail': _klaviyo_detail(relationship_response),
            }), 502
        messages = relationship_response.json().get('data', [])
    if not messages:
        return jsonify({
            'error': 'La campaña se creó sin mensaje asignable',
            'campaign_id': campaign_id,
            'template_id': template_id,
        }), 502

    message_id = messages[0].get('id')
    assignment_payload = {
        'data': {
            'type': 'campaign-message',
            'id': message_id,
            'relationships': {
                'template': {'data': {'type': 'template', 'id': template_id}},
            },
        },
    }
    try:
        assignment_response = requests.post(
            f"{KLAVIYO_API_URL}/campaign-message-assign-template",
            headers=headers,
            json=assignment_payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({
            'error': f'No se pudo asignar el template a la campaña: {exc}',
            'campaign_id': campaign_id,
            'template_id': template_id,
            'campaign_message_id': message_id,
        }), 502

    if assignment_response.status_code not in (200, 201):
        return jsonify({
            'error': f'Klaviyo rechazó la asignación del template: {assignment_response.status_code}',
            'detail': _klaviyo_detail(assignment_response),
            'campaign_id': campaign_id,
            'template_id': template_id,
            'campaign_message_id': message_id,
        }), 502

    assigned_template = (
        assignment_response.json().get('data', {}).get('relationships', {}).get('template', {}).get('data', {}).get('id')
    )
    return jsonify({
        'success': True,
        'template_id': template_id,
        'campaign_id': campaign_id,
        'campaign_message_id': message_id,
        'campaign_template_id': assigned_template,
        'campaign_name': data['campaign_name'],
        'status': 'DRAFT',
        'scheduled_at': None,
        'smart_sending': data.get('use_smart_sending', True),
        'message': 'Campaña creada en estado DRAFT, sin programación ni envío a destinatarios finales.',
    }), 201


@admin_klaviyo_bp.route('/admin/klaviyo/campaign/<campaign_id>', methods=['GET'])
@admin_required
@role_required('admin')
def get_klaviyo_campaign(campaign_id):
    """Read the actual draft status, timing, audience and tracking from Klaviyo."""
    fields = ','.join([
        'name', 'status', 'scheduled_at', 'send_time', 'audiences', 'send_options',
        'send_strategy', 'tracking_options',
    ])
    try:
        response = requests.get(
            f"{KLAVIYO_API_URL}/campaigns/{campaign_id}",
            headers=_get_klaviyo_headers(),
            params={'fields[campaign]': fields},
            timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo leer la campaña: {exc}'}), 502

    if response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo no devolvió la campaña: {response.status_code}',
            'detail': _klaviyo_detail(response),
        }), 502

    campaign = response.json().get('data', {})
    attributes = campaign.get('attributes', {})
    messages = campaign.get('relationships', {}).get('campaign-messages', {}).get('data', [])
    return jsonify({
        'id': campaign.get('id'),
        'name': attributes.get('name'),
        'status': attributes.get('status'),
        'scheduled_at': attributes.get('scheduled_at'),
        'send_time': attributes.get('send_time'),
        'audiences': attributes.get('audiences') or {},
        'send_options': attributes.get('send_options') or {},
        'send_strategy': attributes.get('send_strategy') or {},
        'tracking_options': attributes.get('tracking_options') or {},
        'campaign_messages': messages,
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/campaign-message/<message_id>', methods=['GET', 'PUT'])
@admin_required
@role_required('admin')
def update_klaviyo_campaign_message(message_id):
    """Read or update email subject/preview while preserving the parent draft."""
    headers = _get_klaviyo_headers()
    try:
        message_response = requests.get(
            f"{KLAVIYO_API_URL}/campaign-messages/{message_id}", headers=headers, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo leer el mensaje de campaña: {exc}'}), 502
    if message_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo no devolvió el mensaje de campaña: {message_response.status_code}',
            'detail': _klaviyo_detail(message_response),
        }), 502

    message = message_response.json().get('data', {})
    definition = (message.get('attributes', {}) or {}).get('definition', {}) or {}
    campaign = (message.get('relationships', {}) or {}).get('campaign', {}).get('data', {}) or {}
    template = (message.get('relationships', {}) or {}).get('template', {}).get('data', {}) or {}
    campaign_id = campaign.get('id')
    content = definition.get('content', {}) or {}
    if request.method == 'GET':
        return jsonify({
            'id': message.get('id', message_id),
            'campaign_id': campaign_id,
            'template_id': template.get('id'),
            'channel': definition.get('channel'),
            'label': definition.get('label'),
            'subject': content.get('subject'),
            'preview_text': content.get('preview_text'),
            'from_email': content.get('from_email'),
            'from_label': content.get('from_label'),
            'reply_to_email': content.get('reply_to_email'),
        }), 200

    subject = str((request.get_json(silent=True) or {}).get('subject', '')).strip()
    if not subject:
        return jsonify({'error': 'subject es obligatorio'}), 400
    if definition.get('channel') != 'email' or not campaign_id:
        return jsonify({'error': 'El mensaje no es un email de campaña actualizable'}), 400

    try:
        campaign_response = requests.get(
            f"{KLAVIYO_API_URL}/campaigns/{campaign_id}",
            headers=headers,
            params={'fields[campaign]': 'status'},
            timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo comprobar el estado de la campaña: {exc}'}), 502
    if campaign_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo no devolvió el estado de la campaña: {campaign_response.status_code}',
            'detail': _klaviyo_detail(campaign_response),
        }), 502
    status = str((campaign_response.json().get('data', {}).get('attributes', {}) or {}).get('status', '')).lower()
    if status != 'draft':
        return jsonify({'error': f'La campaña debe estar en Draft para editarse; estado actual: {status or "desconocido"}'}), 409

    updated_definition = dict(definition)
    updated_content = dict(content)
    updated_content['subject'] = subject
    body = request.get_json(silent=True) or {}
    if 'preview_text' in body:
        updated_content['preview_text'] = str(body.get('preview_text') or '')
    updated_definition['content'] = updated_content
    payload = {
        'data': {
            'type': 'campaign-message',
            'id': message_id,
            'attributes': {'definition': updated_definition},
        },
    }
    try:
        update_response = requests.patch(
            f"{KLAVIYO_API_URL}/campaign-messages/{message_id}", headers=headers, json=payload, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo actualizar el mensaje de campaña: {exc}'}), 502
    if update_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo rechazó el mensaje de campaña: {update_response.status_code}',
            'detail': _klaviyo_detail(update_response),
        }), 502

    result_definition = (update_response.json().get('data', {}).get('attributes', {}) or {}).get('definition', {}) or {}
    return jsonify({
        'success': True,
        'campaign_id': campaign_id,
        'campaign_status': 'Draft',
        'message_id': message_id,
        'subject': (result_definition.get('content', {}) or {}).get('subject'),
        'preview_text': (result_definition.get('content', {}) or {}).get('preview_text'),
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/campaign-message/<message_id>/replace-template', methods=['POST'])
@admin_required
@role_required('admin')
def replace_klaviyo_campaign_draft_template(message_id):
    """Replace a Klaviyo campaign template only for its unscheduled draft message.

    Klaviyo creates a protected served copy when a template is assigned to a
    campaign, so updating that copy in place returns 404.  The safe supported
    path is to create a fresh source template and reassign it while the parent
    campaign is still a Draft. The former served copy is intentionally kept as
    a reversible backup.
    """
    body = request.get_json(silent=True) or {}
    template_name = str(body.get('template_name', '')).strip()
    template_html = str(body.get('template_html', '')).strip()
    subject = str(body.get('subject', '')).strip()
    if not template_name or not template_html or not subject:
        return jsonify({'error': 'template_name, template_html y subject son obligatorios'}), 400

    headers = _get_klaviyo_headers()
    try:
        message_response = requests.get(
            f"{KLAVIYO_API_URL}/campaign-messages/{message_id}", headers=headers, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo leer el mensaje de campaña: {exc}'}), 502
    if message_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo no devolvió el mensaje de campaña: {message_response.status_code}',
            'detail': _klaviyo_detail(message_response),
        }), 502

    message = message_response.json().get('data', {})
    original_definition = (message.get('attributes', {}) or {}).get('definition', {}) or {}
    campaign = (message.get('relationships', {}) or {}).get('campaign', {}).get('data', {}) or {}
    original_template = (message.get('relationships', {}) or {}).get('template', {}).get('data', {}) or {}
    campaign_id = campaign.get('id')
    if original_definition.get('channel') != 'email' or not campaign_id:
        return jsonify({'error': 'El mensaje no es un email de campaña actualizable'}), 400

    try:
        campaign_response = requests.get(
            f"{KLAVIYO_API_URL}/campaigns/{campaign_id}",
            headers=headers,
            params={'fields[campaign]': 'status'},
            timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo comprobar el estado de la campaña: {exc}'}), 502
    if campaign_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo no devolvió el estado de la campaña: {campaign_response.status_code}',
            'detail': _klaviyo_detail(campaign_response),
        }), 502
    campaign_status = str((campaign_response.json().get('data', {}).get('attributes', {}) or {}).get('status', '')).lower()
    if campaign_status != 'draft':
        return jsonify({'error': f'La campaña debe estar en Draft para editarse; estado actual: {campaign_status or "desconocido"}'}), 409

    create_template_payload = {
        'data': {
            'type': 'template',
            'attributes': {
                'name': template_name,
                'html': template_html,
                'editor_type': 'CODE',
            },
        },
    }
    try:
        create_template_response = requests.post(
            f"{KLAVIYO_API_URL}/templates", headers=headers, json=create_template_payload, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo crear el nuevo template: {exc}'}), 502
    if create_template_response.status_code not in (200, 201):
        return jsonify({
            'error': f'Klaviyo rechazó el nuevo template: {create_template_response.status_code}',
            'detail': _klaviyo_detail(create_template_response),
        }), 502
    source_template_id = create_template_response.json().get('data', {}).get('id')

    updated_definition = dict(original_definition)
    updated_content = dict(original_definition.get('content', {}) or {})
    updated_content['subject'] = subject
    if 'preview_text' in body:
        updated_content['preview_text'] = str(body.get('preview_text') or '')
    updated_definition['content'] = updated_content
    subject_payload = {
        'data': {
            'type': 'campaign-message',
            'id': message_id,
            'attributes': {'definition': updated_definition},
        },
    }
    try:
        subject_response = requests.patch(
            f"{KLAVIYO_API_URL}/campaign-messages/{message_id}", headers=headers, json=subject_payload, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({
            'error': f'No se pudo cambiar el asunto del borrador: {exc}',
            'source_template_id': source_template_id,
        }), 502
    if subject_response.status_code != 200:
        return jsonify({
            'error': f'Klaviyo rechazó el asunto del borrador: {subject_response.status_code}',
            'detail': _klaviyo_detail(subject_response),
            'source_template_id': source_template_id,
        }), 502

    assignment_payload = {
        'data': {
            'type': 'campaign-message',
            'id': message_id,
            'relationships': {'template': {'data': {'type': 'template', 'id': source_template_id}}},
        },
    }
    try:
        assignment_response = requests.post(
            f"{KLAVIYO_API_URL}/campaign-message-assign-template",
            headers=headers,
            json=assignment_payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        assignment_response = None
        assignment_error = str(exc)
    else:
        assignment_error = None

    if assignment_response is None or assignment_response.status_code not in (200, 201):
        rollback_payload = {
            'data': {
                'type': 'campaign-message',
                'id': message_id,
                'attributes': {'definition': original_definition},
            },
        }
        try:
            rollback_response = requests.patch(
                f"{KLAVIYO_API_URL}/campaign-messages/{message_id}", headers=headers, json=rollback_payload, timeout=20,
            )
            rollback_status = rollback_response.status_code
        except requests.RequestException:
            rollback_status = 'network_error'
        return jsonify({
            'error': 'No se pudo reasignar el template; el asunto se restauró al anterior',
            'detail': assignment_error or _klaviyo_detail(assignment_response),
            'source_template_id': source_template_id,
            'previous_campaign_template_id': original_template.get('id'),
            'rollback_status': rollback_status,
        }), 502

    assigned_template_id = (
        assignment_response.json().get('data', {}).get('relationships', {}).get('template', {}).get('data', {}).get('id')
    )
    return jsonify({
        'success': True,
        'campaign_id': campaign_id,
        'campaign_status': 'Draft',
        'message_id': message_id,
        'subject': subject,
        'source_template_id': source_template_id,
        'campaign_template_id': assigned_template_id,
        'previous_campaign_template_id': original_template.get('id'),
        'message': 'Borrador actualizado sin programar ni enviar la campaña.',
    }), 200


@admin_klaviyo_bp.route('/admin/klaviyo/send-template-preview', methods=['POST'])
@admin_required
@role_required('admin')
def send_template_preview():
    """Send a Klaviyo preview email without scheduling or sending a campaign."""
    data = request.get_json(silent=True) or {}
    template_id = str(data.get('template_id', '')).strip()
    recipients = data.get('recipients') or []
    profile_id = str(data.get('profile_id') or '').strip()
    context = data.get('context')
    if not template_id or not isinstance(recipients, list) or not recipients or len(recipients) > 5:
        return jsonify({'error': 'template_id y entre 1 y 5 destinatarios son obligatorios'}), 400
    if not all(isinstance(address, str) and '@' in address for address in recipients):
        return jsonify({'error': 'Todos los destinatarios deben ser direcciones de email válidas'}), 400
    if context is not None and not isinstance(context, dict):
        return jsonify({'error': 'context debe ser un objeto JSON'}), 400

    headers = _get_klaviyo_headers()
    headers['revision'] = f'{KLAVIYO_REVISION}.pre'
    attributes = {'recipients': recipients}
    if context is not None:
        attributes['context'] = context
    relationships = {'template': {'data': {'type': 'template', 'id': template_id}}}
    if profile_id:
        relationships['profile'] = {'data': {'type': 'profile', 'id': profile_id}}
    preview_payload = {
        'data': {
            'type': 'template-preview-send-job',
            'attributes': attributes,
            'relationships': relationships,
        },
    }
    try:
        response = requests.post(
            f"{KLAVIYO_API_URL}/template-preview-send-jobs", headers=headers, json=preview_payload, timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({'error': f'No se pudo solicitar la prueba: {exc}'}), 502

    if response.status_code not in (200, 202):
        return jsonify({
            'error': f'Klaviyo rechazó la prueba: {response.status_code}',
            'detail': _klaviyo_detail(response),
        }), 502

    job = response.json().get('data', {})
    return jsonify({
        'success': True,
        'preview_job_id': job.get('id'),
        'status': job.get('attributes', {}).get('status'),
        'recipients': recipients,
    }), 202


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
        params={'sort': '-updated', 'page[size]': 10},
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


@admin_klaviyo_bp.route('/admin/klaviyo/activate-transactional-flow/<flow_id>', methods=['PUT'])
@admin_required
@role_required('admin')
def activate_transactional_flow(flow_id):
    """Make a Flow live only after its email action is genuinely transactional.

    Klaviyo may silently leave an action non-transactional when the account has
    not approved that classification.  This guard prevents a refund notice from
    being enabled as marketing mail, where an opted-out purchaser could miss it.
    """
    body = request.get_json() or {}
    action_id = str(body.get('action_id') or '').strip()
    if not action_id:
        return jsonify({'error': 'action_id field required'}), 400

    headers = _get_klaviyo_headers()
    action_response = requests.get(
        f'{KLAVIYO_API_URL}/flow-actions/{action_id}',
        headers=headers,
        timeout=20,
    )
    if action_response.status_code != 200:
        return jsonify({'error': action_response.text, 'status': action_response.status_code}), action_response.status_code

    action = action_response.json().get('data', {})
    message = ((action.get('attributes', {}).get('definition') or {})
               .get('data', {}).get('message') or {})
    if message.get('transactional') is not True:
        return jsonify({
            'error': 'Klaviyo no ha aprobado este mensaje como transaccional; el Flow permanece en borrador.',
            'flow_id': flow_id,
            'action_id': action_id,
            'transactional': False,
        }), 409

    payload = {
        'data': {
            'type': 'flow',
            'id': flow_id,
            'attributes': {'status': 'live'},
        },
    }
    response = requests.patch(
        f'{KLAVIYO_API_URL}/flows/{flow_id}',
        headers=headers,
        json=payload,
        timeout=20,
    )
    if response.status_code != 200:
        return jsonify({'error': response.text, 'status': response.status_code}), response.status_code

    flow = response.json().get('data', {})
    status = flow.get('attributes', {}).get('status')
    if status != 'live':
        return jsonify({'error': 'Klaviyo no confirmó que el Flow quedara live.', 'status': status}), 502
    return jsonify({
        'success': True,
        'flow_id': flow.get('id', flow_id),
        'status': status,
        'transactional': True,
    }), 200


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


def _review_request_safety_filters(cancelled_metric_id, review_submitted_metric_id):
    """Prevent review emails after a cancellation or a completed review.

    The filters are evaluated at each email action.  ``flow-start`` means a
    later refund or a review submitted during the Flow's seven-day delay stops
    the pending message without blocking a different paid order in the future.
    """
    zero_since_flow_start = {
        'type': 'numeric',
        'operator': 'equals',
        'value': 0,
    }
    return {
        'condition_groups': [{
            'conditions': [
                {
                    'type': 'profile-metric',
                    'metric_id': cancelled_metric_id,
                    'measurement': 'count',
                    'measurement_filter': zero_since_flow_start,
                    'timeframe_filter': {'type': 'date', 'operator': 'flow-start'},
                    'metric_filters': None,
                },
                {
                    'type': 'profile-metric',
                    'metric_id': review_submitted_metric_id,
                    'measurement': 'count',
                    'measurement_filter': zero_since_flow_start.copy(),
                    'timeframe_filter': {'type': 'date', 'operator': 'flow-start'},
                    'metric_filters': None,
                },
            ],
        }],
    }


def _klaviyo_metric_ids(headers, names):
    """Resolve a small exact-name set from Klaviyo's paginated metric catalog."""
    unresolved = set(names)
    found = {}
    url = f'{KLAVIYO_API_URL}/metrics'
    # The current Metrics endpoint rejects page-size query parameters; follow
    # its opaque ``links.next`` cursor instead of sending a size hint.
    params = None

    for _ in range(10):
        response = requests.get(url, headers=headers, params=params, timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f'Klaviyo metrics {response.status_code}: {response.text[:500]}')
        body = response.json()
        for metric in body.get('data', []):
            name = (metric.get('attributes') or {}).get('name')
            if name in unresolved:
                found[name] = metric.get('id')
                unresolved.discard(name)
        if not unresolved:
            return found
        next_url = (body.get('links') or {}).get('next')
        if not next_url:
            break
        url = next_url
        params = None

    missing = ', '.join(sorted(unresolved))
    raise RuntimeError(f'No se encontraron las métricas de Klaviyo: {missing}')


@admin_klaviyo_bp.route('/admin/klaviyo/configure-review-request-safety', methods=['PUT'])
@admin_required
@role_required('admin')
def configure_review_request_safety():
    """Apply cancellation/submission suppression to both review Flow emails.

    The action IDs are deliberately fixed to the two known messages in
    ``Solicitud de Reseña — 7 días post-compra``.  Every action is read first,
    then its complete definition is patched back with only
    ``additional_filters`` changed, preserving its template and metrics.
    """
    headers = _get_klaviyo_headers()
    metric_names = ('Mikels Order Cancelled', 'Mikels Review Submitted')
    try:
        metric_ids = _klaviyo_metric_ids(headers, metric_names)
    except RuntimeError as error:
        return jsonify({'error': str(error)}), 502

    filters = _review_request_safety_filters(
        metric_ids['Mikels Order Cancelled'],
        metric_ids['Mikels Review Submitted'],
    )
    action_ids = ('106877925', '106967289')
    definitions = {}

    # Preflight every action before changing either message.
    for action_id in action_ids:
        response = requests.get(
            f'{KLAVIYO_API_URL}/flow-actions/{action_id}', headers=headers, timeout=20,
        )
        if response.status_code != 200:
            return jsonify({
                'error': f'No se pudo leer la acción de reseña {action_id}',
                'status': response.status_code,
                'detail': response.text[:500],
            }), 502
        definition = (response.json().get('data') or {}).get('attributes', {}).get('definition')
        message = ((definition or {}).get('data') or {}).get('message') or {}
        if not definition or not message or message.get('transactional'):
            return jsonify({
                'error': f'La acción {action_id} no es un correo de reseña comercial válido',
            }), 409
        definitions[action_id] = definition

    updated = []
    for action_id in action_ids:
        definition = definitions[action_id]
        definition['data']['message']['additional_filters'] = filters
        payload = {
            'data': {
                'type': 'flow-action',
                'id': action_id,
                'attributes': {'definition': definition},
            },
        }
        response = requests.patch(
            f'{KLAVIYO_API_URL}/flow-actions/{action_id}',
            headers=headers,
            json=payload,
            timeout=20,
        )
        if response.status_code not in [200, 204]:
            return jsonify({
                'error': f'Klaviyo rechazó el filtro de la acción {action_id}',
                'status': response.status_code,
                'detail': response.text[:500],
                'updated_action_ids': updated,
            }), 502
        updated.append(action_id)

    return jsonify({
        'success': True,
        'action_ids': updated,
        'metric_ids': metric_ids,
        'filters': filters,
    }), 200
