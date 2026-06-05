import os
import sys
# DON'T CHANGE THIS !!!
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime
from src.models.user import db
from src.models.order import Order, Subscription
from src.models.coupon import Coupon
from src.routes.user import user_bp
from src.routes.stripe_routes import stripe_bp
from src.routes.notification_routes import notification_bp
from src.routes.newsletter_routes import newsletter_bp
from src.routes.experience_routes import experience_bp
from src.routes.contact_routes import contact_bp
from src.routes.coupon_routes import coupon_bp
from src.routes.horeca_routes import horeca_bp
from src.routes.blog_routes import blog_bp  # Blog automatizado con Brevo
from src.routes.review_routes import review_bp  # Sistema de reseñas
from src.routes.abandoned_cart_routes import abandoned_cart_bp  # Carrito abandonado
from src.routes.admin_klaviyo_routes import admin_klaviyo_bp  # Admin Klaviyo temporal
from src.routes.product_notify_routes import product_notify_bp  # Avísame cuando esté disponible
from src.routes.auth_routes import auth_bp  # Autenticación Microsoft Entra ID
from src.routes.admin_panel_routes import admin_panel_bp  # Panel de administración
from src.models.blog import BlogPost  # Modelo del blog
from src.models.review import Review  # Modelo de reseñas
from src.models.abandoned_cart import AbandonedCart  # Modelo de carrito abandonado
from src.models.product_notification import ProductNotification  # Modelo notificación producto
from src.models.admin_user import AdminUser  # Modelo usuarios admin

# Load environment variables
load_dotenv()

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), 'static'))
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'asdf#FGSgvasgf$5$WGT')

# Enable CORS
# Permitir múltiples orígenes: producción, Vercel preview, y desarrollo local
allowed_origins = [
    os.getenv('FRONTEND_URL', 'http://localhost:5173'),  # Producción principal
    "https://www.mikels.es",
    "https://mikels.es",
    "https://www.mikels.es",
    "http://localhost:5173",
    "http://localhost:8081"
]

# Añadir cualquier preview deployment de Vercel
if os.getenv('VERCEL_URL'):
    allowed_origins.append(f"https://{os.getenv('VERCEL_URL')}")

CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# Mover creación de tablas a la primera solicitud para evitar timeout
@app.before_request
def create_tables():
    if not hasattr(app, 'tables_created'):
        try:
            db.create_all()
            print("Database tables created successfully")
            # Migración: quitar unique constraint del email en coupons
            # para permitir múltiples cupones por email (newsletter + post-compra + reseña)
            try:
                db.session.execute(db.text('ALTER TABLE coupons DROP CONSTRAINT IF EXISTS coupons_email_key'))
                db.session.execute(db.text('ALTER TABLE coupons DROP CONSTRAINT IF EXISTS uq_coupons_email'))
                db.session.commit()
            except Exception as mig_err:
                db.session.rollback()
                print(f"Migration note (non-critical): {mig_err}")
            app.tables_created = True
        except Exception as e:
            print(f"Error creating tables: {e}")

app.register_blueprint(user_bp, url_prefix='/api')
app.register_blueprint(stripe_bp)
app.register_blueprint(notification_bp)
app.register_blueprint(newsletter_bp, url_prefix='/api/newsletter')
app.register_blueprint(experience_bp, url_prefix='/api/experience')
app.register_blueprint(contact_bp, url_prefix='/api/contact')
app.register_blueprint(coupon_bp, url_prefix='/api/coupon')
app.register_blueprint(horeca_bp, url_prefix='/api/horeca')
app.register_blueprint(blog_bp, url_prefix='/api/blog')  # Blog endpoints
app.register_blueprint(review_bp, url_prefix='/api/reviews')  # Reviews endpoints
app.register_blueprint(abandoned_cart_bp, url_prefix='/api/abandoned-cart')  # Carrito abandonado
app.register_blueprint(admin_klaviyo_bp, url_prefix='/api')  # Admin Klaviyo
app.register_blueprint(product_notify_bp, url_prefix='/api')  # Avísame cuando esté disponible
app.register_blueprint(auth_bp, url_prefix='/api/auth')  # Auth Microsoft Entra ID
app.register_blueprint(admin_panel_bp, url_prefix='/api/admin')  # Panel Admin

# Database configuration for coupons and user management
# Use DATABASE_URL from Railway (PostgreSQL) or fallback to SQLite for local dev
database_url = os.getenv('DATABASE_URL')
if database_url:
    # Railway provides DATABASE_URL for PostgreSQL
    # Fix for SQLAlchemy 1.4+ which requires postgresql:// instead of postgres://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Fallback to SQLite for local development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/app.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Las tablas se crean en la primera solicitud (ver @app.before_request)

# Health check endpoint para Railway
@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint para que Railway verifique que el servicio está activo"""
    return jsonify({
        'status': 'ok',
        'service': 'mikels-earth-backend',
        'timestamp': datetime.now().isoformat()
    }), 200


# TEMPORAL: Endpoint de diagnóstico para verificar pedidos en DB
@app.route('/api/debug/orders-count', methods=['GET'])
def debug_orders_count():
    """Endpoint temporal para diagnosticar pedidos - ELIMINAR DESPUÉS"""
    try:
        total = Order.query.count()
        latest = Order.query.order_by(Order.created_at.desc()).limit(3).all()
        return jsonify({
            'total_orders': total,
            'latest': [{
                'id': o.id,
                'order_number': o.order_number,
                'customer_name': o.customer_name,
                'total': o.total,
                'status': o.status,
                'created_at': o.created_at.isoformat() if o.created_at else None
            } for o in latest]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/debug/sync-stripe-orders', methods=['POST'])
def sync_stripe_orders():
    """Recupera todas las checkout sessions completadas de Stripe y las inserta en la DB.
    Endpoint temporal de recuperación - ELIMINAR DESPUÉS."""
    import stripe
    import json as json_lib
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    
    if not stripe.api_key:
        return jsonify({'error': 'STRIPE_SECRET_KEY no configurada'}), 500
    
    synced = 0
    skipped = 0
    errors = []
    
    try:
        # Recuperar todas las checkout sessions completadas (paginado)
        has_more = True
        starting_after = None
        
        while has_more:
            params = {'limit': 100, 'status': 'complete', 'expand': ['data.shipping_details', 'data.customer_details']}
            if starting_after:
                params['starting_after'] = starting_after
            
            sessions = stripe.checkout.Session.list(**params)
            
            for sess in sessions.data:
                # Solo procesar pagos (no suscripciones)
                if sess.mode != 'payment':
                    continue
                
                # Verificar si ya existe en la DB
                existing = Order.query.filter_by(stripe_checkout_session_id=sess.id).first()
                if existing:
                    skipped += 1
                    continue
                
                try:
                    # Convertir a dict puro de Python para evitar problemas con objetos Stripe
                    s = json_lib.loads(str(sess))
                    
                    # Obtener line items
                    line_items = stripe.checkout.Session.list_line_items(sess.id, limit=100)
                    items = []
                    for li in line_items.data:
                        items.append({
                            'name': li.description or 'Producto',
                            'quantity': li.quantity,
                            'price': li.amount_total / 100
                        })
                    
                    # Extraer metadata (siempre es un dict)
                    metadata = s.get('metadata') or {}
                    
                    # Extraer shipping_details
                    shipping = s.get('shipping_details') or {}
                    shipping_addr = shipping.get('address') or {} if isinstance(shipping, dict) else {}
                    
                    shipping_line = (shipping_addr.get('line1') or '') or metadata.get('shipping_address', 'No especificada')
                    shipping_city = (shipping_addr.get('city') or '') or metadata.get('shipping_city', 'No especificada')
                    shipping_postal = (shipping_addr.get('postal_code') or '') or metadata.get('shipping_postal_code', '00000')
                    shipping_country = (shipping_addr.get('country') or '') or metadata.get('shipping_country', 'ES')
                    
                    # Customer details
                    cust = s.get('customer_details') or {}
                    customer_email = (cust.get('email') or '') or s.get('customer_email') or metadata.get('customer_email', 'desconocido@mikels.es')
                    customer_name = (shipping.get('name') or '') or metadata.get('customer_name', 'Cliente')
                    customer_phone = (cust.get('phone') or '') or metadata.get('customer_phone', 'No proporcionado')
                    
                    order_number = metadata.get('order_number', f'MKL-SYNC-{sess.id[-8:]}')
                    
                    # Verificar si ya existe por order_number
                    existing_by_number = Order.query.filter_by(order_number=order_number).first()
                    if existing_by_number:
                        skipped += 1
                        continue
                    
                    total = (s.get('amount_total') or 0) / 100
                    subtotal_raw = metadata.get('subtotal', '')
                    try:
                        subtotal = float(subtotal_raw) if subtotal_raw else total
                    except (ValueError, TypeError):
                        subtotal = total
                    
                    new_order = Order(
                        order_number=order_number,
                        customer_email=customer_email or 'desconocido@mikels.es',
                        customer_name=customer_name or 'Cliente',
                        customer_phone=customer_phone or 'No proporcionado',
                        shipping_address=shipping_line or 'No especificada',
                        shipping_city=shipping_city or 'No especificada',
                        shipping_postal_code=shipping_postal or '00000',
                        shipping_country=shipping_country or 'ES',
                        items=items,
                        subtotal=subtotal,
                        shipping_cost=0 if total >= 40 else 4.95,
                        total=total,
                        stripe_payment_intent_id=s.get('payment_intent') or '',
                        stripe_checkout_session_id=sess.id,
                        payment_status='paid',
                        order_status='processing',
                        customer_notes=metadata.get('customer_notes', '')
                    )
                    
                    # Usar la fecha de creación de la sesión de Stripe
                    created_ts = s.get('created')
                    if created_ts:
                        new_order.created_at = datetime.utcfromtimestamp(created_ts)
                        new_order.paid_at = datetime.utcfromtimestamp(created_ts)
                    
                    db.session.add(new_order)
                    db.session.commit()
                    synced += 1
                    
                except Exception as item_err:
                    db.session.rollback()
                    errors.append(f'{sess.id}: {str(item_err)}')
            
            has_more = sessions.has_more
            if sessions.data:
                starting_after = sessions.data[-1].id
        
        return jsonify({
            'success': True,
            'synced': synced,
            'skipped': skipped,
            'errors': errors[:10],
            'total_errors': len(errors)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/test-email', methods=['GET'])
def test_email():
    """Endpoint temporal de diagnóstico para probar envío de email"""
    from src.services.email_service import send_email
    import os
    api_key = os.getenv('BREVO_API_KEY', 'NOT SET')
    key_preview = api_key[:10] + '...' if len(api_key) > 10 else api_key
    to_email = request.args.get('to', 'info@mikels.es')
    result = send_email(
        to_email,
        'TEST - Prueba de email desde backend',
        '<h1>Email de prueba</h1><p>Si recibes esto, Brevo funciona correctamente.</p><p>Fecha: ' + datetime.now().isoformat() + '</p><p>Enviado a: ' + to_email + '</p>'
    )
    return jsonify({
        'email_sent': result,
        'to': to_email,
        'brevo_key_configured': api_key != 'NOT SET' and len(api_key) > 5,
        'brevo_key_preview': key_preview,
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    static_folder_path = app.static_folder
    if static_folder_path is None:
            return "Static folder not configured", 404

    if path != "" and os.path.exists(os.path.join(static_folder_path, path)):
        return send_from_directory(static_folder_path, path)
    else:
        index_path = os.path.join(static_folder_path, 'index.html')
        if os.path.exists(index_path):
            return send_from_directory(static_folder_path, 'index.html')
        else:
            return "index.html not found", 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
