"""
Servicio de notificaciones por email para el blog de Mikel's Earth
Envía confirmaciones cuando se publica o elimina un post
Usa Klaviyo primero, con fallback a Brevo
"""
import os
import requests

BREVO_API_KEY = os.getenv('BREVO_API_KEY', '')
NOTIFICATION_EMAIL = os.getenv('BLOG_NOTIFICATION_EMAIL', 'info@mikels.es')


def send_blog_notification(action, post_title, post_slug, recipient_email=None):
    """
    Envía una notificación por email cuando se realiza una acción en el blog
    
    Args:
        action: 'created', 'updated', 'deleted', 'published'
        post_title: Título del post
        post_slug: Slug del post
        recipient_email: Email del destinatario (opcional)
    """
    recipient = recipient_email or NOTIFICATION_EMAIL
    
    # Intentar primero con Klaviyo
    klaviyo_ok = _send_via_klaviyo(action, post_title, post_slug, recipient)
    
    if klaviyo_ok:
        return True
    
    # Fallback a Brevo
    return _send_via_brevo(action, post_title, post_slug, recipient)


def _send_via_klaviyo(action, post_title, post_slug, recipient):
    """Envía notificación de blog via Klaviyo Events API"""
    try:
        from src.services.klaviyo_service import send_klaviyo_event
        
        post_url = f"https://www.mikels.es/blog/{post_slug}"
        
        properties = {
            "Action": action,
            "PostTitle": post_title,
            "PostSlug": post_slug,
            "PostUrl": post_url,
            "Source": "mikels-earth-blog"
        }
        
        return send_klaviyo_event(
            metric_name=f"Mikels Blog {action.capitalize()}",
            profile_email=recipient,
            properties=properties
        )
    except Exception as e:
        print(f"⚠️ [BLOG] Error Klaviyo: {e}")
        return False


def _send_via_brevo(action, post_title, post_slug, recipient):
    """Envía notificación de blog via Brevo (fallback)"""
    if not BREVO_API_KEY:
        print("BREVO_API_KEY no configurada, saltando notificación")
        return False
    
    # Configurar mensaje según la acción
    subjects = {
        'created': f'✅ Nuevo post publicado: {post_title}',
        'updated': f'📝 Post actualizado: {post_title}',
        'deleted': f'🗑️ Post eliminado: {post_title}',
        'published': f'🚀 Borrador publicado: {post_title}',
        'draft': f'📋 Borrador guardado: {post_title}'
    }
    
    post_url = f"https://www.mikels.es/blog/{post_slug}"
    
    html_templates = {
        'created': f'''
            <div style="font-family: Montserrat, sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #CD545B; font-family: Georgia, serif; font-style: italic;">
                    ✅ Post Publicado
                </h1>
                <p>Se ha publicado un nuevo post en el blog de Mikel's Earth:</p>
                <h2 style="color: #4a4d3d;">{post_title}</h2>
                <p>
                    <a href="{post_url}" style="background-color: #CD545B; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        Ver Post
                    </a>
                </p>
                <hr style="border: 1px solid #ececec; margin: 20px 0;">
                <p style="color: #797764; font-size: 12px;">
                    Este email fue enviado automáticamente por el sistema de blog de Mikel's Earth.
                </p>
            </div>
        ''',
        'updated': f'''
            <div style="font-family: Montserrat, sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #B7BF10; font-family: Georgia, serif; font-style: italic;">
                    📝 Post Actualizado
                </h1>
                <p>Se ha actualizado el siguiente post:</p>
                <h2 style="color: #4a4d3d;">{post_title}</h2>
                <p>
                    <a href="{post_url}" style="background-color: #B7BF10; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        Ver Post
                    </a>
                </p>
            </div>
        ''',
        'deleted': f'''
            <div style="font-family: Montserrat, sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #CD545B; font-family: Georgia, serif; font-style: italic;">
                    🗑️ Post Eliminado
                </h1>
                <p>Se ha eliminado el siguiente post del blog:</p>
                <h2 style="color: #4a4d3d;">{post_title}</h2>
                <p style="color: #797764;">
                    El post ya no está disponible en el blog.
                </p>
            </div>
        ''',
        'published': f'''
            <div style="font-family: Montserrat, sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #B7BF10; font-family: Georgia, serif; font-style: italic;">
                    🚀 Borrador Publicado
                </h1>
                <p>El siguiente borrador ha sido publicado:</p>
                <h2 style="color: #4a4d3d;">{post_title}</h2>
                <p>
                    <a href="{post_url}" style="background-color: #B7BF10; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        Ver Post
                    </a>
                </p>
            </div>
        ''',
        'draft': f'''
            <div style="font-family: Montserrat, sans-serif; max-width: 600px; margin: 0 auto;">
                <h1 style="color: #797764; font-family: Georgia, serif; font-style: italic;">
                    📋 Borrador Guardado
                </h1>
                <p>Se ha guardado un nuevo borrador:</p>
                <h2 style="color: #4a4d3d;">{post_title}</h2>
                <p style="color: #797764;">
                    El borrador está pendiente de publicación. Puedes publicarlo desde el panel admin.
                </p>
            </div>
        '''
    }
    
    subject = subjects.get(action, f'Blog Mikel\'s Earth: {post_title}')
    html_content = html_templates.get(action, html_templates['created'])
    
    try:
        response = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={
                'api-key': BREVO_API_KEY,
                'Content-Type': 'application/json'
            },
            json={
                'sender': {
                    'name': "Mikel's Earth Blog",
                    'email': 'noreply@mikels.es'
                },
                'to': [{'email': recipient}],
                'subject': subject,
                'htmlContent': html_content
            }
        )
        
        if response.status_code in [200, 201]:
            print(f"Notificación enviada: {action} - {post_title}")
            return True
        else:
            print(f"Error enviando notificación: {response.text}")
            return False
    
    except Exception as e:
        print(f"Error en servicio de email: {str(e)}")
        return False
