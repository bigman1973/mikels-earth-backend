"""
Rutas de Reseñas para Mikel's Earth
- POST /api/reviews: Crear una nueva reseña (genera cupón de agradecimiento)
- GET /api/reviews: Listar reseñas públicas (con filtros)
- GET /api/reviews/stats: Estadísticas de reseñas
"""
from flask import Blueprint, request, jsonify
from src.models.user import db
from src.models.review import Review
from src.models.order import Order
from src.services.klaviyo_service import send_klaviyo_event
from datetime import datetime
from collections import defaultdict
import os
import re
import time
import hmac

review_bp = Blueprint('review', __name__)

ADMIN_SECRET_KEY = os.environ['ADMIN_SECRET_KEY'].strip()
if not ADMIN_SECRET_KEY:
    raise RuntimeError('Missing required security environment variable: ADMIN_SECRET_KEY')

# Anti-spam
_review_rate_store = defaultdict(list)


def _is_gibberish(text):
    if not text or len(text) < 4:
        return False
    clean = re.sub(r'[\s\-\'\.]', '', text.lower())
    if re.findall(r'[bcdfghjklmnpqrstvwxyz]{5,}', clean):
        return True
    if len(clean) > 6:
        vowels = sum(1 for c in clean if c in 'aeiou\u00e1\u00e9\u00ed\u00f3\u00fa')
        if vowels / len(clean) < 0.15:
            return True
    if len(text) > 6:
        upper_count = sum(1 for c in text[1:] if c.isupper())
        if upper_count > len(text) * 0.35:
            return True
    return False


def _has_valid_admin_key(candidate):
    return bool(candidate) and hmac.compare_digest(candidate, ADMIN_SECRET_KEY)


@review_bp.route('/init-db', methods=['GET'])
def init_reviews_db():
    """Endpoint temporal para forzar la creación de la tabla reviews"""
    try:
        db.create_all()
        # Verificar que la tabla existe
        count = Review.query.count()
        return jsonify({'success': True, 'message': f'Tabla reviews OK. {count} reseñas existentes.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _validate_email(email):
    """Validar formato de email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


@review_bp.route('', methods=['POST'])
def create_review():
    # Anti-spam: rate limiting
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip:
        client_ip = client_ip.split(',')[0].strip()
    now = time.time()
    _review_rate_store[client_ip] = [t for t in _review_rate_store[client_ip] if now - t < 3600]
    if len(_review_rate_store[client_ip]) >= 3:
        return jsonify({'error': 'Demasiados intentos. Inténtalo más tarde.'}), 429
    _review_rate_store[client_ip].append(now)
    """
    Crear una nueva reseña y enviar su evento de confirmación a Klaviyo.
    
    Body JSON:
    {
        "customer_email": "email@example.com",
        "customer_name": "Nombre",
        "product_slug": "aceite-temprano-500ml",
        "product_name": "Aceite de Oliva Virgen Extra Temprano 500ml",
        "rating": 5,
        "title": "Título opcional",
        "comment": "Texto de la reseña",
        "order_number": "MKE-XXXX" (opcional)
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400
        
        # Validar campos obligatorios
        required_fields = ['customer_email', 'customer_name', 'product_slug', 'product_name', 'rating', 'comment']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'El campo {field} es obligatorio'}), 400
        
        # Validar email
        email = data['customer_email'].strip().lower()
        if not _validate_email(email):
            return jsonify({'error': 'Email no válido'}), 400
        
        # Anti-spam: gibberish check on name and comment
        if _is_gibberish(data.get('customer_name', '')) or _is_gibberish(data.get('comment', '')):
            print(f"\ud83d\udeab Review spam blocked: {data.get('customer_name')} / {email}")
            return jsonify({'error': 'Contenido no v\u00e1lido'}), 400
        
        # Validar rating
        rating = int(data['rating'])
        if rating < 1 or rating > 5:
            return jsonify({'error': 'La puntuación debe ser entre 1 y 5'}), 400
        
        # Validar longitud del comentario
        comment = data['comment'].strip()
        if len(comment) < 10:
            return jsonify({'error': 'El comentario debe tener al menos 10 caracteres'}), 400
        
        if len(comment) > 1000:
            return jsonify({'error': 'El comentario no puede superar los 1000 caracteres'}), 400
        
        # Verificar si es una compra verificada
        is_verified = False
        order_number = data.get('order_number', '').strip()
        try:
            if order_number:
                order = Order.query.filter_by(
                    order_number=order_number,
                    customer_email=email,
                    payment_status='paid'
                ).first()
                if order:
                    is_verified = True
            else:
                # Verificar si el email tiene algún pedido pagado
                any_order = Order.query.filter_by(
                    customer_email=email,
                    payment_status='paid'
                ).first()
                if any_order:
                    is_verified = True
        except Exception as e:
            print(f"⚠️ Error verificando pedido (tabla orders puede no existir): {e}")
            is_verified = False
        
        # Verificar si ya existe una reseña de este email para este producto
        existing_review = Review.query.filter_by(
            customer_email=email,
            product_slug=data['product_slug']
        ).first()
        
        if existing_review:
            return jsonify({'error': 'Ya has dejado una reseña para este producto'}), 409
        
        # Crear la reseña
        review = Review(
            customer_email=email,
            customer_name=data['customer_name'].strip(),
            product_slug=data['product_slug'].strip(),
            product_name=data['product_name'].strip(),
            rating=rating,
            title=data.get('title', '').strip() if data.get('title') else None,
            comment=comment,
            status='approved',  # Auto-aprobada
            is_verified_purchase=is_verified,
            order_number=order_number if order_number else None
        )
        
        db.session.add(review)
        
        db.session.commit()
        
        # Enviar evento a Klaviyo para el email de agradecimiento sin incentivo.
        try:
            send_klaviyo_event(
                metric_name="Mikels Review Submitted",
                profile_email=email,
                properties={
                    "CustomerName": data['customer_name'].strip(),
                    "ProductName": data['product_name'].strip(),
                    "Rating": rating,
                    "Comment": comment,
                    "Source": "mikels-earth-website"
                },
                profile_attrs={"first_name": data['customer_name'].strip().split(' ')[0]}
            )
        except Exception as e:
            print(f"⚠️ Error enviando evento de reseña a Klaviyo: {e}")
        
        return jsonify({
            'success': True,
            'message': '¡Gracias por compartir tu opinión!',
            'review': review.to_public_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        import traceback
        error_detail = traceback.format_exc()
        print(f"❌ Error creando reseña: {str(e)}")
        print(f"❌ Traceback: {error_detail}")
        return jsonify({'error': 'Error interno del servidor', 'detail': str(e)}), 500


@review_bp.route('', methods=['GET'])
def get_reviews():
    """
    Obtener reseñas públicas aprobadas.
    
    Query params:
    - product_slug: Filtrar por producto
    - limit: Número máximo de reseñas (default 20)
    - offset: Paginación
    - sort: 'newest' (default), 'highest', 'lowest'
    """
    try:
        product_slug = request.args.get('product_slug')
        limit = min(int(request.args.get('limit', 20)), 100)
        offset = int(request.args.get('offset', 0))
        sort = request.args.get('sort', 'newest')
        
        # Base query: solo reseñas aprobadas
        query = Review.query.filter_by(status='approved')
        
        # Filtrar por producto si se especifica
        if product_slug:
            query = query.filter_by(product_slug=product_slug)
        
        # Ordenar
        if sort == 'highest':
            query = query.order_by(Review.rating.desc(), Review.created_at.desc())
        elif sort == 'lowest':
            query = query.order_by(Review.rating.asc(), Review.created_at.desc())
        else:  # newest
            query = query.order_by(Review.created_at.desc())
        
        # Contar total
        total = query.count()
        
        # Aplicar paginación
        reviews = query.offset(offset).limit(limit).all()
        
        return jsonify({
            'reviews': [r.to_public_dict() for r in reviews],
            'total': total,
            'limit': limit,
            'offset': offset
        }), 200
        
    except Exception as e:
        print(f"❌ Error obteniendo reseñas: {str(e)}")
        return jsonify({'error': 'Error interno del servidor'}), 500


@review_bp.route('/stats', methods=['GET'])
def get_review_stats():
    """
    Obtener estadísticas de reseñas.
    
    Query params:
    - product_slug: Filtrar por producto (opcional)
    """
    try:
        product_slug = request.args.get('product_slug')
        
        # Base query: solo reseñas aprobadas
        query = Review.query.filter_by(status='approved')
        
        if product_slug:
            query = query.filter_by(product_slug=product_slug)
        
        reviews = query.all()
        total = len(reviews)
        
        if total == 0:
            return jsonify({
                'total_reviews': 0,
                'average_rating': 0,
                'rating_distribution': {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
            }), 200
        
        # Calcular estadísticas
        ratings = [r.rating for r in reviews]
        average = sum(ratings) / len(ratings)
        
        distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for r in ratings:
            distribution[r] += 1
        
        return jsonify({
            'total_reviews': total,
            'average_rating': round(average, 1),
            'rating_distribution': distribution
        }), 200
        
    except Exception as e:
        print(f"❌ Error obteniendo estadísticas: {str(e)}")
        return jsonify({'error': 'Error interno del servidor'}), 500


@review_bp.route('/featured', methods=['GET'])
def get_featured_reviews():
    """
    Obtener reseñas destacadas para el carrusel del homepage.
    Devuelve las mejores reseñas (4-5 estrellas) más recientes.
    
    Query params:
    - limit: Número máximo (default 8)
    """
    try:
        limit = min(int(request.args.get('limit', 8)), 20)
        
        reviews = Review.query.filter(
            Review.status == 'approved',
            Review.rating >= 4
        ).order_by(Review.created_at.desc()).limit(limit).all()
        
        return jsonify({
            'reviews': [r.to_public_dict() for r in reviews]
        }), 200
        
    except Exception as e:
        print(f"❌ Error obteniendo reseñas destacadas: {str(e)}")
        return jsonify({'error': 'Error interno del servidor'}), 500


@review_bp.route('/<int:review_id>', methods=['DELETE'])
def delete_review(review_id):
    """
    Eliminar una reseña por ID (admin).
    Requiere header X-Admin-Key para autorización básica.
    """
    try:
        admin_key = request.headers.get('X-Admin-Key', '')
        if not _has_valid_admin_key(admin_key):
            return jsonify({'error': 'No autorizado'}), 401
        
        review = Review.query.get(review_id)
        if not review:
            return jsonify({'error': 'Reseña no encontrada'}), 404
        
        db.session.delete(review)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Reseña {review_id} eliminada'}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@review_bp.route('/<int:review_id>', methods=['PATCH'])
def update_review(review_id):
    """
    Actualizar una reseña por ID (admin).
    Permite cambiar created_at y otros campos.
    Requiere header X-Admin-Key.
    """
    try:
        admin_key = request.headers.get('X-Admin-Key', '')
        if not _has_valid_admin_key(admin_key):
            return jsonify({'error': 'No autorizado'}), 401
        
        review = Review.query.get(review_id)
        if not review:
            return jsonify({'error': 'Reseña no encontrada'}), 404
        
        data = request.get_json()
        
        if 'created_at' in data:
            review.created_at = datetime.fromisoformat(data['created_at'])
        if 'status' in data:
            review.status = data['status']
        if 'is_verified_purchase' in data:
            review.is_verified_purchase = data['is_verified_purchase']
        if 'product_slug' in data:
            review.product_slug = data['product_slug']
        if 'product_name' in data:
            review.product_name = data['product_name']
        if 'comment' in data:
            review.comment = data['comment']
        if 'rating' in data:
            review.rating = data['rating']
        if 'customer_name' in data:
            review.customer_name = data['customer_name']
        
        db.session.commit()
        
        return jsonify({'success': True, 'review': review.to_dict()}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
