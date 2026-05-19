from flask import Blueprint, request, jsonify
from src.models.user import db
from src.models.product_notification import ProductNotification
from src.services.email_dispatcher import dispatch_product_notify_subscribe, dispatch_product_back_in_stock

product_notify_bp = Blueprint('product_notify', __name__)


@product_notify_bp.route('/product-notify/subscribe', methods=['POST'])
def subscribe_product_notification():
    """Registra interés de un cliente en un producto agotado y envía evento a Klaviyo"""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        name = data.get('name', '').strip()
        product_name = data.get('product_name', '').strip()
        product_id = data.get('product_id', '').strip()

        if not email or not product_name or not product_id:
            return jsonify({'error': 'Email, product_name y product_id son obligatorios'}), 400

        # Verificar si ya está suscrito a este producto
        existing = ProductNotification.query.filter_by(
            email=email,
            product_id=product_id,
            notified=False
        ).first()

        if existing:
            return jsonify({'message': 'Ya estás en la lista de espera para este producto'}), 200

        # Crear registro
        notification = ProductNotification(
            email=email,
            name=name,
            product_name=product_name,
            product_id=product_id
        )
        db.session.add(notification)
        db.session.commit()

        # Enviar evento a Klaviyo (dispara email de confirmación)
        try:
            dispatch_product_notify_subscribe(email, name, product_name, product_id)
        except Exception as e:
            print(f"Error enviando evento Klaviyo product notify: {e}")

        return jsonify({'success': True, 'message': 'Te avisaremos cuando esté disponible'}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@product_notify_bp.route('/product-notify/available', methods=['POST'])
def notify_product_available():
    """Notifica a todos los suscritos que un producto vuelve a estar disponible"""
    try:
        # Verificar admin key
        admin_key = request.headers.get('X-Admin-Key')
        if admin_key != 'mikels-admin-2026':
            return jsonify({'error': 'No autorizado'}), 401

        data = request.get_json()
        product_id = data.get('product_id', '').strip()
        product_name = data.get('product_name', '').strip()

        if not product_id or not product_name:
            return jsonify({'error': 'product_id y product_name son obligatorios'}), 400

        # Buscar todos los que esperan este producto
        subscribers = ProductNotification.query.filter_by(
            product_id=product_id,
            notified=False
        ).all()

        if not subscribers:
            return jsonify({'message': 'No hay suscriptores esperando este producto', 'notified': 0}), 200

        notified_count = 0
        errors = []

        for sub in subscribers:
            try:
                # Enviar evento a Klaviyo (dispara email "ya está disponible")
                dispatch_product_back_in_stock(
                    email=sub.email,
                    name=sub.name,
                    product_name=product_name,
                    product_id=product_id
                )
                # Marcar como notificado
                sub.notified = True
                notified_count += 1
            except Exception as e:
                errors.append({'email': sub.email, 'error': str(e)})

        db.session.commit()

        return jsonify({
            'success': True,
            'notified': notified_count,
            'errors': errors
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@product_notify_bp.route('/product-notify/subscribers', methods=['GET'])
def list_subscribers():
    """Lista los suscriptores pendientes de notificación (admin)"""
    admin_key = request.headers.get('X-Admin-Key')
    if admin_key != 'mikels-admin-2026':
        return jsonify({'error': 'No autorizado'}), 401

    product_id = request.args.get('product_id')

    query = ProductNotification.query.filter_by(notified=False)
    if product_id:
        query = query.filter_by(product_id=product_id)

    subscribers = query.order_by(ProductNotification.created_at.desc()).all()

    return jsonify({
        'total': len(subscribers),
        'subscribers': [s.to_dict() for s in subscribers]
    }), 200
