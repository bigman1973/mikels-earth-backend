"""
Modelo BlogPost para el sistema de blog de Mikel's Earth
"""
from datetime import datetime
from src.models.user import db


class BlogPost(db.Model):
    """Modelo para los posts del blog"""
    __tablename__ = 'blog_posts'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    content = db.Column(db.Text, nullable=False)
    excerpt = db.Column(db.Text, nullable=True)
    author = db.Column(db.String(100), default="Mikel's Earth")
    featured_image = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), default='published')  # 'draft' o 'published'
    category = db.Column(db.String(50), nullable=True)
    tags = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = db.Column(db.DateTime, nullable=True)
    
    def __repr__(self):
        return f'<BlogPost {self.title}>'
    
    def to_dict(self):
        """Convierte el post a diccionario para la API"""
        return {
            'id': self.id,
            'title': self.title,
            'slug': self.slug,
            'content': self.content,
            'excerpt': self.excerpt,
            'author': self.author,
            'featured_image': self.featured_image,
            'status': self.status,
            'category': self.category,
            'tags': self.tags.split(',') if self.tags else [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'published_at': self.published_at.isoformat() if self.published_at else None
        }
    
    def to_summary(self):
        """Versión resumida para listados"""
        return {
            'id': self.id,
            'title': self.title,
            'slug': self.slug,
            'excerpt': self.excerpt,
            'author': self.author,
            'featured_image': self.featured_image,
            'status': self.status,
            'category': self.category,
            'published_at': self.published_at.isoformat() if self.published_at else None
        }
    
    @staticmethod
    def generate_slug(title):
        """Genera un slug URL-friendly a partir del título"""
        import re
        import unicodedata
        
        # Normalizar caracteres unicode
        slug = unicodedata.normalize('NFKD', title)
        slug = slug.encode('ascii', 'ignore').decode('ascii')
        
        # Convertir a minúsculas
        slug = slug.lower()
        
        # Reemplazar espacios y caracteres especiales con guiones
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        
        # Eliminar guiones al inicio y final
        slug = slug.strip('-')
        
        # Eliminar guiones duplicados
        slug = re.sub(r'-+', '-', slug)
        
        return slug
    
    @staticmethod
    def generate_excerpt(content, max_length=200):
        """Genera un extracto del contenido"""
        from bs4 import BeautifulSoup
        
        # Eliminar HTML
        soup = BeautifulSoup(content, 'html.parser')
        text = soup.get_text()
        
        # Limpiar espacios
        text = ' '.join(text.split())
        
        # Truncar
        if len(text) > max_length:
            text = text[:max_length].rsplit(' ', 1)[0] + '...'
        
        return text
