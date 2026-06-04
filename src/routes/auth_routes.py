from flask import Blueprint, redirect, request, jsonify, session, current_app
import requests
import jwt
import os
from datetime import datetime, timedelta
from functools import wraps
from src.models.user import db
from src.models.admin_user import AdminUser

auth_bp = Blueprint('auth', __name__)

# Microsoft Entra ID Configuration
MICROSOFT_CLIENT_ID = os.environ.get('MICROSOFT_CLIENT_ID', '1f62ee00-1b24-45c7-9f21-c9166352eedf')
MICROSOFT_CLIENT_SECRET = os.environ.get('MICROSOFT_CLIENT_SECRET', '')
MICROSOFT_TENANT_ID = os.environ.get('MICROSOFT_TENANT_ID', '119a5c89-e896-4664-a916-f06cc168b2bd')
MICROSOFT_AUTHORITY = f'https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}'
MICROSOFT_TOKEN_URL = f'{MICROSOFT_AUTHORITY}/oauth2/v2.0/token'
MICROSOFT_AUTH_URL = f'{MICROSOFT_AUTHORITY}/oauth2/v2.0/authorize'
MICROSOFT_GRAPH_URL = 'https://graph.microsoft.com/v1.0/me'

# JWT Secret para tokens de sesión propios
JWT_SECRET = os.environ.get('JWT_SECRET', os.environ.get('SECRET_KEY', 'mikels-admin-secret-key'))
JWT_EXPIRATION_HOURS = 24

# Frontend URL
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'https://www.mikels.es')
BACKEND_URL = os.environ.get('BACKEND_URL', 'https://mikels-earth-backend-production.up.railway.app')


def generate_admin_token(admin_user):
    """Genera un JWT propio para la sesión del admin"""
    payload = {
        'sub': admin_user.microsoft_id,
        'email': admin_user.email,
        'name': admin_user.name,
        'role': admin_user.role,
        'iat': datetime.utcnow(),
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def admin_required(f):
    """Decorator para proteger rutas que requieren autenticación de admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Token de autenticación requerido'}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            # Verificar que el usuario sigue activo
            admin = AdminUser.query.filter_by(microsoft_id=payload['sub'], is_active=True).first()
            if not admin:
                return jsonify({'error': 'Usuario no autorizado o desactivado'}), 403
            request.admin_user = admin
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expirado, inicia sesión de nuevo'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token inválido'}), 401
        return f(*args, **kwargs)
    return decorated_function


def role_required(*roles):
    """Decorator para verificar que el admin tiene un rol específico"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not hasattr(request, 'admin_user'):
                return jsonify({'error': 'No autenticado'}), 401
            if request.admin_user.role not in roles:
                return jsonify({'error': f'Rol insuficiente. Se requiere: {", ".join(roles)}'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@auth_bp.route('/microsoft/login')
def microsoft_login():
    """Inicia el flujo OAuth2 con Microsoft Entra ID"""
    redirect_uri = f'{BACKEND_URL}/api/auth/microsoft/callback'
    scope = 'openid profile email User.Read'

    auth_url = (
        f'{MICROSOFT_AUTH_URL}?'
        f'client_id={MICROSOFT_CLIENT_ID}'
        f'&response_type=code'
        f'&redirect_uri={redirect_uri}'
        f'&response_mode=query'
        f'&scope={scope}'
        f'&state=mikels_admin_login'
    )
    return redirect(auth_url)


@auth_bp.route('/microsoft/callback')
def microsoft_callback():
    """Callback de Microsoft OAuth2 - intercambia code por token"""
    code = request.args.get('code')
    error = request.args.get('error')

    if error:
        return redirect(f'{FRONTEND_URL}/admin/login?error={error}')

    if not code:
        return redirect(f'{FRONTEND_URL}/admin/login?error=no_code')

    # Intercambiar code por access_token
    redirect_uri = f'{BACKEND_URL}/api/auth/microsoft/callback'
    token_data = {
        'client_id': MICROSOFT_CLIENT_ID,
        'client_secret': MICROSOFT_CLIENT_SECRET,
        'code': code,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
        'scope': 'openid profile email User.Read'
    }

    token_response = requests.post(MICROSOFT_TOKEN_URL, data=token_data)

    if token_response.status_code != 200:
        return redirect(f'{FRONTEND_URL}/admin/login?error=token_exchange_failed')

    tokens = token_response.json()
    access_token = tokens.get('access_token')

    # Obtener perfil del usuario de Microsoft Graph
    headers = {'Authorization': f'Bearer {access_token}'}
    profile_response = requests.get(MICROSOFT_GRAPH_URL, headers=headers)

    if profile_response.status_code != 200:
        return redirect(f'{FRONTEND_URL}/admin/login?error=profile_fetch_failed')

    profile = profile_response.json()
    email = profile.get('mail') or profile.get('userPrincipalName', '')
    name = profile.get('displayName', '')
    microsoft_id = profile.get('id', '')

    # Verificar dominio permitido
    if not AdminUser.is_allowed_domain(email):
        return redirect(f'{FRONTEND_URL}/admin/login?error=domain_not_allowed')

    # Buscar o crear usuario admin
    admin_user = AdminUser.query.filter_by(microsoft_id=microsoft_id).first()

    if not admin_user:
        # Primer login - crear usuario con rol por defecto
        admin_user = AdminUser(
            microsoft_id=microsoft_id,
            email=email.lower(),
            name=name,
            role='viewer'  # Rol por defecto, el admin puede cambiarlo después
        )
        db.session.add(admin_user)

    # Actualizar último login
    admin_user.last_login = datetime.utcnow()
    admin_user.name = name  # Actualizar nombre por si cambió en Microsoft
    db.session.commit()

    if not admin_user.is_active:
        return redirect(f'{FRONTEND_URL}/admin/login?error=user_disabled')

    # Generar token JWT propio
    token = generate_admin_token(admin_user)

    # Redirigir al frontend con el token
    return redirect(f'{FRONTEND_URL}/admin/dashboard?token={token}')


@auth_bp.route('/me')
@admin_required
def get_current_user():
    """Devuelve el perfil del usuario admin autenticado"""
    return jsonify(request.admin_user.to_dict())


@auth_bp.route('/users', methods=['GET'])
@admin_required
@role_required('admin')
def list_admin_users():
    """Lista todos los usuarios admin (solo para admins)"""
    users = AdminUser.query.all()
    return jsonify([u.to_dict() for u in users])


@auth_bp.route('/users/<int:user_id>/role', methods=['PUT'])
@admin_required
@role_required('admin')
def update_user_role(user_id):
    """Actualiza el rol de un usuario admin"""
    data = request.get_json()
    new_role = data.get('role')

    if new_role not in ['admin', 'logistics', 'sales', 'viewer']:
        return jsonify({'error': 'Rol no válido'}), 400

    user = AdminUser.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    user.role = new_role
    db.session.commit()

    return jsonify(user.to_dict())


@auth_bp.route('/users/<int:user_id>/toggle', methods=['PUT'])
@admin_required
@role_required('admin')
def toggle_user_active(user_id):
    """Activa/desactiva un usuario admin"""
    user = AdminUser.query.get(user_id)
    if not user:
        return jsonify({'error': 'Usuario no encontrado'}), 404

    user.is_active = not user.is_active
    db.session.commit()

    return jsonify(user.to_dict())
