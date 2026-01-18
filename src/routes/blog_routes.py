"""
Rutas del Blog para Mikel's Earth
Incluye endpoints públicos, webhook de Brevo y panel admin
"""
import os
import hashlib
import hmac
import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify
from src.models.user import db
from src.models.blog import BlogPost

blog_bp = Blueprint('blog', __name__)

# Configuración
ADMIN_USERNAME = os.getenv('BLOG_ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('BLOG_ADMIN_PASSWORD', 'mikels2026')
JWT_SECRET = os.getenv('JWT_SECRET', 'mikels-blog-secret-key-2026')
BREVO_WEBHOOK_KEY = os.getenv('BREVO_WEBHOOK_KEY', '')


# ============================================
# MIDDLEWARE DE AUTENTICACIÓN
# ============================================

def token_required(f):
    """Decorador para proteger rutas admin"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        
        if not token:
            return jsonify({'error': 'Token requerido'}), 401
        
        try:
            # Remover 'Bearer ' si existe
            if token.startswith('Bearer '):
                token = token[7:]
            
            data = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            current_user = data['username']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expirado'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token inválido'}), 401
        
        return f(current_user, *args, **kwargs)
    
    return decorated


def verify_brevo_webhook(request_data, signature):
    """Verifica la firma del webhook de Brevo"""
    if not BREVO_WEBHOOK_KEY:
        # Si no hay key configurada, aceptar (para desarrollo)
        return True
    
    expected_signature = hmac.new(
        BREVO_WEBHOOK_KEY.encode(),
        request_data,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected_signature)


# ============================================
# ENDPOINTS PÚBLICOS
# ============================================

@blog_bp.route('/posts', methods=['GET'])
def get_posts():
    """Obtener lista de posts publicados (paginado)"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        category = request.args.get('category', None)
        
        # Query base: solo posts publicados
        query = BlogPost.query.filter_by(status='published')
        
        # Filtrar por categoría si se especifica
        if category:
            query = query.filter_by(category=category)
        
        # Ordenar por fecha de publicación (más recientes primero)
        query = query.order_by(BlogPost.published_at.desc())
        
        # Paginar
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        posts = [post.to_summary() for post in pagination.items]
        
        return jsonify({
            'posts': posts,
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@blog_bp.route('/posts/<slug>', methods=['GET'])
def get_post(slug):
    """Obtener un post por su slug"""
    try:
        post = BlogPost.query.filter_by(slug=slug, status='published').first()
        
        if not post:
            return jsonify({'error': 'Post no encontrado'}), 404
        
        return jsonify(post.to_dict())
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@blog_bp.route('/categories', methods=['GET'])
def get_categories():
    """Obtener lista de categorías con conteo de posts"""
    try:
        categories = db.session.query(
            BlogPost.category,
            db.func.count(BlogPost.id).label('count')
        ).filter(
            BlogPost.status == 'published',
            BlogPost.category.isnot(None)
        ).group_by(BlogPost.category).all()
        
        return jsonify({
            'categories': [{'name': cat, 'count': count} for cat, count in categories]
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================
# WEBHOOK DE BREVO
# ============================================

@blog_bp.route('/webhook/brevo', methods=['POST'])
def brevo_webhook():
    """
    Recibe emails de Brevo y los procesa:
    - Si el asunto empieza con [DELETE]: elimina el post
    - Si el asunto empieza con [DRAFT]: guarda como borrador
    - Si no: publica el post
    """
    try:
        # Verificar firma del webhook (opcional en desarrollo)
        signature = request.headers.get('X-Mailin-Signature', '')
        if BREVO_WEBHOOK_KEY and not verify_brevo_webhook(request.data, signature):
            return jsonify({'error': 'Firma inválida'}), 401
        
        data = request.json
        
        # Extraer datos del email
        subject = data.get('Subject', data.get('subject', ''))
        html_content = data.get('Html', data.get('html', ''))
        text_content = data.get('Text', data.get('text', ''))
        from_email = data.get('From', data.get('from', ''))
        attachments = data.get('Attachments', data.get('attachments', []))
        
        # Determinar el contenido a usar
        content = html_content if html_content else f'<p>{text_content}</p>'
        
        # Verificar si es una solicitud de eliminación
        if subject.upper().startswith('[DELETE]'):
            slug_to_delete = subject[8:].strip().lower()
            slug_to_delete = BlogPost.generate_slug(slug_to_delete)
            
            post = BlogPost.query.filter_by(slug=slug_to_delete).first()
            if post:
                db.session.delete(post)
                db.session.commit()
                return jsonify({
                    'success': True,
                    'action': 'deleted',
                    'slug': slug_to_delete
                })
            else:
                return jsonify({
                    'success': False,
                    'error': f'Post con slug "{slug_to_delete}" no encontrado'
                }), 404
        
        # Verificar si es un borrador
        is_draft = subject.upper().startswith('[DRAFT]')
        if is_draft:
            subject = subject[7:].strip()
        
        # Extraer categoría si existe [CATEGORIA] en el asunto
        category = None
        if '[' in subject and ']' in subject:
            start = subject.index('[')
            end = subject.index(']')
            category = subject[start+1:end].strip()
            subject = subject[end+1:].strip()
        
        # Generar slug
        slug = BlogPost.generate_slug(subject)
        
        # Verificar si ya existe un post con ese slug
        existing_post = BlogPost.query.filter_by(slug=slug).first()
        if existing_post:
            # Actualizar post existente
            existing_post.title = subject
            existing_post.content = content
            existing_post.excerpt = BlogPost.generate_excerpt(content)
            existing_post.category = category
            existing_post.updated_at = datetime.utcnow()
            
            if not is_draft and existing_post.status == 'draft':
                existing_post.status = 'published'
                existing_post.published_at = datetime.utcnow()
            
            db.session.commit()
            
            return jsonify({
                'success': True,
                'action': 'updated',
                'post': existing_post.to_summary()
            })
        
        # Crear nuevo post
        new_post = BlogPost(
            title=subject,
            slug=slug,
            content=content,
            excerpt=BlogPost.generate_excerpt(content),
            category=category,
            status='draft' if is_draft else 'published',
            published_at=None if is_draft else datetime.utcnow()
        )
        
        # Procesar imagen adjunta si existe
        if attachments and len(attachments) > 0:
            # Por ahora guardamos la URL de la primera imagen
            # En producción, subirías a S3 o similar
            first_attachment = attachments[0]
            if isinstance(first_attachment, dict):
                new_post.featured_image = first_attachment.get('url', first_attachment.get('Url', ''))
        
        db.session.add(new_post)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'action': 'created',
            'post': new_post.to_summary()
        })
    
    except Exception as e:
        print(f"Error en webhook de Brevo: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ============================================
# ENDPOINTS ADMIN
# ============================================

@blog_bp.route('/admin/login', methods=['POST'])
def admin_login():
    """Login para el panel admin"""
    try:
        data = request.json
        username = data.get('username', '')
        password = data.get('password', '')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            # Generar token JWT
            token = jwt.encode({
                'username': username,
                'exp': datetime.utcnow() + timedelta(hours=24)
            }, JWT_SECRET, algorithm='HS256')
            
            return jsonify({
                'success': True,
                'token': token,
                'username': username
            })
        
        return jsonify({'error': 'Credenciales inválidas'}), 401
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@blog_bp.route('/admin/verify', methods=['GET'])
@token_required
def verify_token(current_user):
    """Verificar si el token es válido"""
    return jsonify({
        'valid': True,
        'username': current_user
    })


@blog_bp.route('/admin/posts', methods=['GET'])
@token_required
def admin_get_posts(current_user):
    """Obtener todos los posts (incluye borradores)"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        status = request.args.get('status', None)
        
        query = BlogPost.query
        
        if status:
            query = query.filter_by(status=status)
        
        query = query.order_by(BlogPost.created_at.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        posts = [post.to_dict() for post in pagination.items]
        
        # Estadísticas
        total_posts = BlogPost.query.count()
        published_count = BlogPost.query.filter_by(status='published').count()
        draft_count = BlogPost.query.filter_by(status='draft').count()
        
        return jsonify({
            'posts': posts,
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page,
            'stats': {
                'total': total_posts,
                'published': published_count,
                'drafts': draft_count
            }
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@blog_bp.route('/admin/posts/<int:post_id>', methods=['GET'])
@token_required
def admin_get_post(current_user, post_id):
    """Obtener un post por ID (admin)"""
    try:
        post = BlogPost.query.get(post_id)
        
        if not post:
            return jsonify({'error': 'Post no encontrado'}), 404
        
        return jsonify(post.to_dict())
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@blog_bp.route('/admin/posts/<int:post_id>', methods=['PUT'])
@token_required
def admin_update_post(current_user, post_id):
    """Actualizar un post"""
    try:
        post = BlogPost.query.get(post_id)
        
        if not post:
            return jsonify({'error': 'Post no encontrado'}), 404
        
        data = request.json
        
        if 'title' in data:
            post.title = data['title']
            # Regenerar slug si cambia el título
            post.slug = BlogPost.generate_slug(data['title'])
        
        if 'content' in data:
            post.content = data['content']
            post.excerpt = BlogPost.generate_excerpt(data['content'])
        
        if 'category' in data:
            post.category = data['category']
        
        if 'tags' in data:
            post.tags = ','.join(data['tags']) if isinstance(data['tags'], list) else data['tags']
        
        if 'featured_image' in data:
            post.featured_image = data['featured_image']
        
        if 'status' in data:
            old_status = post.status
            post.status = data['status']
            
            # Si cambia de draft a published, actualizar fecha de publicación
            if old_status == 'draft' and data['status'] == 'published':
                post.published_at = datetime.utcnow()
        
        post.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'post': post.to_dict()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@blog_bp.route('/admin/posts/<int:post_id>', methods=['DELETE'])
@token_required
def admin_delete_post(current_user, post_id):
    """Eliminar un post"""
    try:
        post = BlogPost.query.get(post_id)
        
        if not post:
            return jsonify({'error': 'Post no encontrado'}), 404
        
        title = post.title
        db.session.delete(post)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Post "{title}" eliminado correctamente'
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@blog_bp.route('/admin/posts/<int:post_id>/publish', methods=['POST'])
@token_required
def admin_publish_post(current_user, post_id):
    """Publicar un borrador"""
    try:
        post = BlogPost.query.get(post_id)
        
        if not post:
            return jsonify({'error': 'Post no encontrado'}), 404
        
        post.status = 'published'
        post.published_at = datetime.utcnow()
        post.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'post': post.to_dict()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@blog_bp.route('/admin/posts', methods=['POST'])
@token_required
def admin_create_post(current_user):
    """Crear un nuevo post desde el panel admin"""
    try:
        data = request.json
        
        title = data.get('title', '')
        content = data.get('content', '')
        
        if not title or not content:
            return jsonify({'error': 'Título y contenido son requeridos'}), 400
        
        slug = BlogPost.generate_slug(title)
        
        # Verificar si ya existe
        if BlogPost.query.filter_by(slug=slug).first():
            return jsonify({'error': 'Ya existe un post con ese título'}), 400
        
        new_post = BlogPost(
            title=title,
            slug=slug,
            content=content,
            excerpt=BlogPost.generate_excerpt(content),
            category=data.get('category'),
            tags=','.join(data.get('tags', [])) if isinstance(data.get('tags'), list) else data.get('tags'),
            featured_image=data.get('featured_image'),
            status=data.get('status', 'draft'),
            published_at=datetime.utcnow() if data.get('status') == 'published' else None
        )
        
        db.session.add(new_post)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'post': new_post.to_dict()
        }), 201
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
