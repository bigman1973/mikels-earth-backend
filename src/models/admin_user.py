from src.models.user import db
from datetime import datetime


class AdminUser(db.Model):
    __tablename__ = 'admin_users'

    id = db.Column(db.Integer, primary_key=True)
    microsoft_id = db.Column(db.String(255), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='viewer')
    # Roles: admin, logistics, sales, viewer
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Dominios permitidos
    ALLOWED_DOMAINS = ['lfgd.es', 'farmsplanet.es', 'mikels.es', 'internetoperadores.com']

    def to_dict(self):
        return {
            'id': self.id,
            'microsoft_id': self.microsoft_id,
            'email': self.email,
            'name': self.name,
            'role': self.role,
            'is_active': self.is_active,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    @staticmethod
    def is_allowed_domain(email):
        """Verifica si el email pertenece a un dominio autorizado"""
        domain = email.split('@')[-1].lower()
        return domain in AdminUser.ALLOWED_DOMAINS
