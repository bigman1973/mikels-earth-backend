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
from src.models.coupon import Coupon
from src.services.klaviyo_service import send_klaviyo_event
from datetime import datetime
import os
import re
import random
import string

review_bp = Blueprint('review', __name__)


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


def _generate_review_coupon_code():
    """Genera un código de cupón único para recompensar la reseña"""
    chars = string.ascii_uppercase + string.digits
    random_part = ''.join(random.choice(chars) for _ in range(6))
    return f'GRACIAS10-{random_part}'


def _validate_email(email):
    """Validar formato de email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


@review_bp.route('', methods=['POST'])
def create_review():
    """
    Crear una nueva reseña.
    Genera un cupón de agradecimiento del 10% y envía un email de agradecimiento via Klaviyo.
    
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
        
        # Generar cupón de agradecimiento
        coupon_code = _generate_review_coupon_code()
        
        # Asegurar que el código es único
        try:
            max_attempts = 10
            for _ in range(max_attempts):
                if not Coupon.query.filter_by(code=coupon_code).first():
                    break
                coupon_code = _generate_review_coupon_code()
        except Exception as e:
            print(f"⚠️ Error verificando unicidad de cupón (tabla coupons puede no existir): {e}")
            # El cupón generado se usa igualmente
        
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
            order_number=order_number if order_number else None,
            reward_coupon_code=coupon_code
        )
        
        db.session.add(review)
        
        # Crear el cupón en la tabla de cupones (para que sea validable en el checkout)
        try:
            reward_coupon = Coupon(
                code=coupon_code,
                email=f"review-{email}",  # Prefijo para distinguir de cupones newsletter
                discount_percent=10
            )
            db.session.add(reward_coupon)
        except Exception as e:
            print(f"⚠️ Error creando cupón de reseña (tabla coupons puede no existir): {e}")
        
        db.session.commit()
        
        # Enviar evento a Klaviyo para email de agradecimiento con cupón
        try:
            send_klaviyo_event(
                metric_name="Mikels Review Submitted",
                profile_email=email,
                properties={
                    "CustomerName": data['customer_name'].strip(),
                    "ProductName": data['product_name'].strip(),
                    "Rating": rating,
                    "Comment": comment,
                    "CouponCode": coupon_code,
                    "Source": "mikels-earth-website"
                },
                profile_attrs={"first_name": data['customer_name'].strip().split(' ')[0]}
            )
        except Exception as e:
            print(f"⚠️ Error enviando evento de reseña a Klaviyo: {e}")
        
        return jsonify({
            'success': True,
            'message': '¡Gracias por tu reseña! Te hemos enviado un cupón de descuento del 10% a tu email.',
            'review': review.to_public_dict(),
            'coupon_code': coupon_code
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
        if admin_key != os.environ.get('ADMIN_SECRET_KEY', 'mikels-admin-2026'):
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
        if admin_key != os.environ.get('ADMIN_SECRET_KEY', 'mikels-admin-2026'):
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
