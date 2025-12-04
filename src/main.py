import os
import sys
# DON'T CHANGE THIS !!!
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
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
        "origins": allowed_origins,
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

app.register_blueprint(user_bp, url_prefix='/api')
app.register_blueprint(stripe_bp)
app.register_blueprint(notification_bp)
app.register_blueprint(newsletter_bp, url_prefix='/api/newsletter')
app.register_blueprint(experience_bp, url_prefix='/api/experience')
app.register_blueprint(contact_bp, url_prefix='/api/contact')
app.register_blueprint(coupon_bp, url_prefix='/api/coupon')
app.register_blueprint(horeca_bp, url_prefix='/api/horeca')

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

# Create database tables
with app.app_context():
    try:
        db.create_all()
        print("Database tables created successfully")
    except Exception as e:
        print(f"Warning: Could not create tables: {e}")

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
