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
from src.routes.product_routes import product_bp  # Catálogo público de productos
from src.models.blog import BlogPost  # Modelo del blog
from src.models.review import Review  # Modelo de reseñas
from src.models.abandoned_cart import AbandonedCart  # Modelo de carrito abandonado
from src.models.product_notification import ProductNotification  # Modelo notificación producto
from src.models.admin_user import AdminUser  # Modelo usuarios admin
from src.models.web_product import WebProduct  # Catálogo de productos web

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

# Error handler global para que los 500 incluyan CORS headers
@app.errorhandler(500)
def handle_500(e):
    response = jsonify({'error': f'Error interno del servidor: {str(e)}'})
    response.status_code = 500
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    traceback.print_exc()
    response = jsonify({'error': f'Error interno: {str(e)}'})
    response.status_code = 500
    return response

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
            # Migración: añadir campos de facturación y Holded a orders
            try:
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS needs_invoice BOOLEAN DEFAULT FALSE'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_name VARCHAR(200)'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_nif VARCHAR(20)'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_address VARCHAR(200)'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_city VARCHAR(100)'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_postal_code VARCHAR(20)'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS holded_id VARCHAR(100)'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS holded_invoice_id VARCHAR(100)'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS holded_doc_number VARCHAR(50)'))
                db.session.execute(db.text('ALTER TABLE orders ADD COLUMN IF NOT EXISTS email_sent BOOLEAN DEFAULT FALSE'))
                db.session.commit()
                print("Migration: invoice/holded fields added to orders")
            except Exception as mig_err2:
                db.session.rollback()
                print(f"Migration invoice fields (non-critical): {mig_err2}")
            # Migración: añadir campos de costes logísticos a web_products
            try:
                db.session.execute(db.text('ALTER TABLE web_products ADD COLUMN IF NOT EXISTS shipping_cost FLOAT DEFAULT 0.0'))
                db.session.execute(db.text('ALTER TABLE web_products ADD COLUMN IF NOT EXISTS preparation_cost FLOAT DEFAULT 0.0'))
                db.session.commit()
                print("Migration: shipping_cost/preparation_cost added to web_products")
            except Exception as mig_err3:
                db.session.rollback()
                print(f"Migration costs fields (non-critical): {mig_err3}")
            # Migración: quitar NOT NULL de email en coupons (para cupones públicos sin email)
            try:
                db.session.execute(db.text('ALTER TABLE coupons ALTER COLUMN email DROP NOT NULL'))
                db.session.commit()
                print("Migration: email column now nullable in coupons")
            except Exception as mig_err_email:
                db.session.rollback()
                print(f"Migration email nullable (non-critical): {mig_err_email}")
            # Migración: añadir nuevos campos a coupons para sistema completo de cupones
            try:
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS description VARCHAR(500)'))
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS discount_type VARCHAR(20) DEFAULT \'percentage\''))
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS discount_value FLOAT DEFAULT 10'))
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS min_order_amount FLOAT DEFAULT 0'))
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS max_uses INTEGER'))
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS current_uses INTEGER DEFAULT 0'))
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS max_uses_per_customer INTEGER'))
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE'))
                db.session.execute(db.text('ALTER TABLE coupons ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP'))
                # Migrar datos existentes: copiar discount_percent a discount_value
                db.session.execute(db.text('UPDATE coupons SET discount_value = discount_percent WHERE discount_value IS NULL AND discount_percent IS NOT NULL'))
                db.session.commit()
                print("Migration: coupon management fields added")
            except Exception as mig_err4:
                db.session.rollback()
                print(f"Migration coupons fields (non-critical): {mig_err4}")
            # Migración: añadir campos de traducción EN a web_products
            try:
                db.session.execute(db.text('ALTER TABLE web_products ADD COLUMN IF NOT EXISTS name_en VARCHAR(200)'))
                db.session.execute(db.text('ALTER TABLE web_products ADD COLUMN IF NOT EXISTS description_en TEXT'))
                db.session.execute(db.text('ALTER TABLE web_products ADD COLUMN IF NOT EXISTS long_description_en TEXT'))
                db.session.commit()
                print("Migration: translation fields (name_en, description_en, long_description_en) added to web_products")
            except Exception as mig_err_i18n:
                db.session.rollback()
                print(f"Migration i18n fields (non-critical): {mig_err_i18n}")
            # Seed de cupones manuales (idempotente - no duplica)
            try:
                from src.models.coupon import Coupon
                manual_coupons = [
                    {'code': 'dr.gemmavalls', 'description': 'Cupón colaborador - Dr. Gemma Valls', 'discount_value': 10},
                    {'code': 'ME2025', 'description': 'Cupón manual - Evento ME2025', 'discount_value': 10},
                    {'code': 'MIKELSFRIENDS', 'description': 'Cupón manual - Friends & Family (amigos)', 'discount_value': 10},
                    {'code': 'MIKELSFAMILY', 'description': 'Cupón manual - Friends & Family (familia)', 'discount_value': 20},
                    {'code': 'BIENVENIDA10', 'description': 'Cupón manual - Bienvenida genérica', 'discount_value': 10},
                    {'code': 'IRVIANCESTRAL', 'description': 'Cupón colaborador - Irvi Ancestral', 'discount_value': 10},
                ]
                created_count = 0
                for mc in manual_coupons:
                    existing = Coupon.query.filter(db.func.lower(Coupon.code) == mc['code'].lower()).first()
                    if not existing:
                        new_coupon = Coupon(
                            code=mc['code'],
                            description=mc['description'],
                            discount_type='percentage',
                            discount_value=mc['discount_value'],
                            active=True
                        )
                        db.session.add(new_coupon)
                        created_count += 1
                if created_count > 0:
                    db.session.commit()
                    print(f"Seed: {created_count} cupones manuales creados")
                else:
                    print("Seed: cupones manuales ya existen")
            except Exception as seed_mc_err:
                db.session.rollback()
                print(f"Seed manual coupons (non-critical): {seed_mc_err}")
            
            # Fix: desactivar cupones que ya fueron usados pero siguen como active=True
            try:
                used_but_active = Coupon.query.filter(
                    Coupon.active == True,
                    Coupon.used == True
                ).all()
                fixed_count = 0
                for c in used_but_active:
                    c.active = False
                    fixed_count += 1
                # También desactivar los que tienen max_uses alcanzado
                maxed_out = Coupon.query.filter(
                    Coupon.active == True,
                    Coupon.max_uses != None,
                    Coupon.current_uses >= Coupon.max_uses
                ).all()
                for c in maxed_out:
                    c.active = False
                    c.used = True
                    fixed_count += 1
                if fixed_count > 0:
                    db.session.commit()
                    print(f"Fix: {fixed_count} cupones usados desactivados")
            except Exception as fix_err:
                db.session.rollback()
                print(f"Fix used coupons (non-critical): {fix_err}")
            # Seed de productos: solo si la tabla web_products está vacía
            try:
                product_count = WebProduct.query.count()
                if product_count == 0:
                    from seed_products import PRODUCTS
                    for p_data in PRODUCTS:
                        product = WebProduct(
                            id=p_data['id'],
                            name=p_data['name'],
                            slug=p_data['slug'],
                            sku=p_data.get('sku'),
                            description=p_data.get('description'),
                            long_description=p_data.get('long_description'),
                            price=p_data['price'],
                            original_price=p_data.get('original_price'),
                            currency=p_data.get('currency', 'EUR'),
                            image=p_data.get('image'),
                            images=p_data.get('images'),
                            category=p_data['category'],
                            tags=p_data.get('tags'),
                            stock=p_data.get('stock', 0),
                            weight=p_data.get('weight'),
                            sold_out=p_data.get('sold_out', False),
                            sold_out_message=p_data.get('sold_out_message'),
                            ingredients=p_data.get('ingredients'),
                            nutritional_info=p_data.get('nutritional_info'),
                            subscription_available=p_data.get('subscription_available', False),
                            subscription_discount=p_data.get('subscription_discount'),
                            subscription_frequencies=p_data.get('subscription_frequencies'),
                            subscription_terms=p_data.get('subscription_terms'),
                            volume_discount=p_data.get('volume_discount'),
                            tiered_discount=p_data.get('tiered_discount'),
                            addons=p_data.get('addons'),
                            variants=p_data.get('variants'),
                            includes=p_data.get('includes'),
                            related_products=p_data.get('related_products'),
                            claims=p_data.get('claims'),
                            badges=p_data.get('badges'),
                            featured=p_data.get('featured', False),
                            free_shipping=p_data.get('free_shipping', False),
                            limited_edition=p_data.get('limited_edition', False),
                            award=p_data.get('award'),
                            active=True,
                            display_order=p_data.get('display_order', 0)
                        )
                        db.session.add(product)
                    db.session.commit()
                    print(f"Seed: {len(PRODUCTS)} productos insertados en web_products")
                else:
                    print(f"web_products ya tiene {product_count} productos, skip seed")
            except Exception as seed_err:
                db.session.rollback()
                print(f"Seed products (non-critical): {seed_err}")
            # Fix: resetear secuencia de IDs de web_products para evitar conflictos
            try:
                db.session.execute(db.text("SELECT setval('web_products_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM web_products), false)"))
                db.session.commit()
            except Exception as seq_err:
                db.session.rollback()
                print(f"Sequence fix (non-critical): {seq_err}")

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
app.register_blueprint(product_bp, url_prefix='/api')  # Catálogo público de productos

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
        'version': 'v2.5-coupon-seed',
        'timestamp': datetime.now().isoformat()
    }), 200




@app.route('/api/test-cloudinary', methods=['GET'])
def test_cloudinary():
    """Test endpoint para verificar configuración de Cloudinary."""
    import cloudinary
    import cloudinary.uploader
    try:
        cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
        api_key = os.environ.get('CLOUDINARY_API_KEY', '')
        api_secret = os.environ.get('CLOUDINARY_API_SECRET', '')
        
        if not all([cloud_name, api_key, api_secret]):
            return jsonify({
                'error': 'Cloudinary no configurado',
                'cloud_name': bool(cloud_name),
                'api_key': bool(api_key),
                'api_secret': bool(api_secret)
            }), 500
        
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True
        )
        
        # Test upload with a tiny 1x1 PNG (raw bytes, no PIL needed)
        import base64, io
        # Minimal valid 1x1 PNG
        png_1x1 = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
        buffer = io.BytesIO(png_1x1)
        
        result = cloudinary.uploader.upload(
            buffer,
            public_id='test/connection_test',
            overwrite=True,
            resource_type='image'
        )
        
        return jsonify({
            'success': True,
            'url': result['secure_url'],
            'cloud_name': cloud_name,
            'message': 'Cloudinary funciona correctamente'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/manual-coupons-info', methods=['GET'])
def manual_coupons_info():
    """Endpoint temporal para ver datos detallados de cupones manuales incluyendo datos de Stripe."""
    try:
        import stripe
        import os
        stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
        from src.models.coupon import Coupon
        codes = ['dr.gemmavalls', 'ME2025', 'MIKELSFRIENDS', 'MIKELSFAMILY', 'BIENVENIDA10', 'IRVIANCESTRAL']
        results = []
        
        # Get all Stripe promotion codes
        stripe_promos = {}
        stripe_coupons = {}
        try:
            # List all promotion codes from Stripe
            promos = stripe.PromotionCode.list(limit=100)
            for promo in promos.auto_paging_iter():
                stripe_promos[promo.code.upper()] = {
                    'id': promo.id,
                    'code': promo.code,
                    'times_redeemed': promo.times_redeemed,
                    'active': promo.active,
                    'coupon_id': promo.coupon.id if promo.coupon else None,
                    'coupon_percent_off': promo.coupon.percent_off if promo.coupon else None,
                    'coupon_amount_off': promo.coupon.amount_off if promo.coupon else None,
                    'coupon_times_redeemed': promo.coupon.times_redeemed if promo.coupon else 0,
                    'created': promo.created,
                    'max_redemptions': promo.max_redemptions,
                }
        except Exception as stripe_err:
            stripe_promos = {'error': str(stripe_err)}
        
        # Search Stripe coupons by 'name' field (the original discount code is stored as name)
        # Group by name and sum times_redeemed
        stripe_usage_by_name = {}  # {code_name: {total_redeemed, total_amount_off, coupons_count, details}}
        try:
            coupons_list = stripe.Coupon.list(limit=100)
            for coup in coupons_list.auto_paging_iter():
                coup_name = (coup.name or '').upper()
                if coup_name:
                    if coup_name not in stripe_usage_by_name:
                        stripe_usage_by_name[coup_name] = {
                            'total_redeemed': 0,
                            'total_amount_off_cents': 0,
                            'coupons_count': 0,
                            'details': []
                        }
                    stripe_usage_by_name[coup_name]['total_redeemed'] += coup.times_redeemed
                    stripe_usage_by_name[coup_name]['coupons_count'] += 1
                    if coup.amount_off:
                        stripe_usage_by_name[coup_name]['total_amount_off_cents'] += coup.amount_off * coup.times_redeemed
                    stripe_usage_by_name[coup_name]['details'].append({
                        'id': coup.id,
                        'times_redeemed': coup.times_redeemed,
                        'amount_off': coup.amount_off,
                        'percent_off': coup.percent_off,
                        'created': coup.created,
                        'valid': coup.valid,
                    })
        except Exception as stripe_err2:
            stripe_usage_by_name = {'error': str(stripe_err2)}
        
        for code in codes:
            c = Coupon.query.filter(db.func.lower(Coupon.code) == code.lower()).first()
            entry = {
                'code': code,
                'db_data': None,
                'stripe_promo_data': stripe_promos.get(code.upper(), None),
                'stripe_usage': stripe_usage_by_name.get(code.upper(), None),
            }
            if c:
                entry['db_data'] = {
                    'id': c.id,
                    'discount_type': c.discount_type if hasattr(c, 'discount_type') else 'percentage',
                    'discount_value': c.discount_value if hasattr(c, 'discount_value') else None,
                    'active': c.active if hasattr(c, 'active') else True,
                    'current_uses': c.current_uses if hasattr(c, 'current_uses') else 0,
                    'max_uses': c.max_uses if hasattr(c, 'max_uses') else None,
                    'used': c.used if hasattr(c, 'used') else False,
                    'created_at': c.created_at.isoformat() if hasattr(c, 'created_at') and c.created_at else None,
                    'description': c.description if hasattr(c, 'description') else None,
                }
            results.append(entry)
        # Also list all unique coupon names found in Stripe
        all_names = [k for k in stripe_usage_by_name.keys() if k != 'error'] if isinstance(stripe_usage_by_name, dict) else []
        return jsonify({'success': True, 'coupons': results, 'all_stripe_coupon_names': all_names}), 200
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/seed-manual-coupons', methods=['POST'])
def seed_manual_coupons_endpoint():
    """Endpoint temporal para crear cupones manuales. Eliminar después de usar."""
    try:
        from src.models.coupon import Coupon
        manual_coupons = [
            {'code': 'dr.gemmavalls', 'description': 'Cupón colaborador - Dr. Gemma Valls', 'discount_value': 10},
            {'code': 'ME2025', 'description': 'Cupón manual - Evento ME2025', 'discount_value': 10},
            {'code': 'MIKELSFRIENDS', 'description': 'Cupón manual - Friends & Family (amigos)', 'discount_value': 10},
            {'code': 'MIKELSFAMILY', 'description': 'Cupón manual - Friends & Family (familia)', 'discount_value': 20},
            {'code': 'BIENVENIDA10', 'description': 'Cupón manual - Bienvenida genérica', 'discount_value': 10},
            {'code': 'IRVIANCESTRAL', 'description': 'Cupón colaborador - Irvi Ancestral', 'discount_value': 10},
        ]
        results = []
        for mc in manual_coupons:
            existing = Coupon.query.filter(db.func.lower(Coupon.code) == mc['code'].lower()).first()
            if existing:
                results.append({'code': mc['code'], 'status': 'already_exists', 'id': existing.id})
            else:
                try:
                    new_coupon = Coupon(
                        code=mc['code'],
                        discount_type='percentage',
                        discount_value=mc['discount_value'],
                        active=True
                    )
                    db.session.add(new_coupon)
                    db.session.flush()
                    results.append({'code': mc['code'], 'status': 'created', 'id': new_coupon.id})
                except Exception as inner_err:
                    db.session.rollback()
                    results.append({'code': mc['code'], 'status': 'error', 'error': str(inner_err)})
        db.session.commit()
        return jsonify({'success': True, 'results': results}), 200
    except Exception as e:
        db.session.rollback()
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/debug-clients', methods=['GET'])
def debug_clients():
    """Endpoint temporal de diagnóstico para clientes"""
    try:
        from src.models.order import Order
        from sqlalchemy import func
        
        # Contar pedidos totales
        total_orders = Order.query.count()
        
        # Obtener emails únicos
        emails = db.session.query(Order.customer_email).filter(
            Order.customer_email.isnot(None),
            Order.customer_email != ''
        ).distinct().all()
        
        email_list = [e[0] for e in emails]
        
        # Intentar el group by
        try:
            web_clients_query = db.session.query(
                Order.customer_email,
                Order.customer_name,
                func.count(Order.id).label('order_count'),
                func.sum(Order.total).label('total_spent'),
                func.max(Order.created_at).label('last_order')
            ).filter(
                Order.customer_email.isnot(None),
                Order.customer_email != ''
            ).group_by(
                Order.customer_email,
                Order.customer_name
            ).all()
            group_by_count = len(web_clients_query)
            group_by_error = None
        except Exception as gbe:
            group_by_count = 0
            group_by_error = str(gbe)
        
        # Intentar holded contacts
        try:
            from src.services.holded_service import holded_get_contacts
            contacts = holded_get_contacts()
            holded_count = len(contacts)
            holded_clients = len([c for c in contacts if c.get('type') == 'client'])
            holded_error = None
        except Exception as he:
            holded_count = 0
            holded_clients = 0
            holded_error = str(he)
        
        return jsonify({
            'total_orders': total_orders,
            'unique_emails': len(email_list),
            'email_sample': email_list[:5],
            'group_by_count': group_by_count,
            'group_by_error': group_by_error,
            'holded_contacts_total': holded_count,
            'holded_clients': holded_clients,
            'holded_error': holded_error
        })
    except Exception as e:
        return jsonify({'error': str(e), 'type': type(e).__name__}), 500


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
