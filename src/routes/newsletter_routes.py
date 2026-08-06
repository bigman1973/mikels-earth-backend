# PostgreSQL coupons table ready - 2025-12-01 22:45
from flask import Blueprint, request, jsonify
from src.services.email_dispatcher import dispatch_newsletter_subscription_notification, dispatch_newsletter_welcome, dispatch_add_contact
from src.models.coupon import Coupon
import re
import time
from collections import defaultdict

newsletter_bp = Blueprint('newsletter', __name__)

# Anti-spam
_newsletter_rate_store = defaultdict(list)


def _is_gibberish(text):
    if not text or len(text) < 4:
        return False
    clean = re.sub(r'[\s\-\'\.]', '', text.lower())
    if re.findall(r'[bcdfghjklmnpqrstvwxyz]{5,}', clean):
        return True
    if len(clean) > 6:
        vowels = sum(1 for c in clean if c in 'aeiou\u00e1\u00e9\u00ed\u00f3\u00fa\u00e0\u00e8\u00ec\u00f2\u00f9')
        if vowels / len(clean) < 0.15:
            return True
    if len(text) > 6:
        upper_count = sum(1 for c in text[1:] if c.isupper())
        if upper_count > len(text) * 0.35:
            return True
    return False


def _is_newsletter_rate_limited(ip):
    now = time.time()
    _newsletter_rate_store[ip] = [t for t in _newsletter_rate_store[ip] if now - t < 3600]
    if len(_newsletter_rate_store[ip]) >= 5:
        return True
    _newsletter_rate_store[ip].append(now)
    return False


@newsletter_bp.route('/subscribe', methods=['POST'])
def subscribe_newsletter():
    """
    Endpoint para suscribirse al newsletter.
    Acepta: email (obligatorio), first_name, last_name (obligatorios desde frontend), phone (opcional)
    PROTECCIÓN: Un email solo puede suscribirse UNA vez. Si ya tiene cupón (usado o no), se rechaza.
    """
    try:
        # Anti-spam: rate limiting
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip:
            client_ip = client_ip.split(',')[0].strip()
        if _is_newsletter_rate_limited(client_ip):
            print(f"\u26a0\ufe0f Newsletter rate limited: {client_ip}")
            return jsonify({'success': True, 'message': 'Subscription successful', 'coupon_code': 'BIENVENIDA10'}), 200

        data = request.get_json()
        email = data.get('email')
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()
        phone = data.get('phone', '').strip()
        coupon_code = data.get('coupon_code')  # Cupón generado por el microservicio
        source = data.get('source', 'website')
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400

        # Anti-spam: gibberish name check
        if _is_gibberish(first_name) or _is_gibberish(last_name):
            print(f"\ud83d\udeab Newsletter spam blocked (gibberish): {first_name} {last_name} / {email} / IP={client_ip}")
            return jsonify({'success': True, 'message': 'Subscription successful', 'coupon_code': 'BIENVENIDA10'}), 200
        
        # ===== PROTECCIÓN CONTRA SUSCRIPCIONES DUPLICADAS =====
        # Verificar si este email ya tiene CUALQUIER cupón de newsletter (usado o no)
        existing_coupon = Coupon.query.filter(
            Coupon.email == email.lower().strip()
        ).first()
        
        if existing_coupon:
            # Ya se suscribió antes - NO generar nuevo cupón
            print(f"⚠️ Email {email} ya tiene cupón newsletter: {existing_coupon.code} (used={existing_coupon.used})")
            return jsonify({
                'success': False,
                'already_subscribed': True,
                'message': '¡Ya estás suscrito/a! Revisa tu email original para encontrar tu cupón de bienvenida.'
            }), 200
        # ===== FIN PROTECCIÓN =====
        
        # Si no viene cupón del frontend, generar uno usando PostgreSQL
        if not coupon_code:
            try:
                # Crear cupón único usando el modelo Coupon (PostgreSQL)
                coupon = Coupon.create_coupon(email.lower().strip(), discount_percent=10)
                coupon_code = coupon.code
                print(f"Coupon created successfully: {coupon_code} for {email}")
            except Exception as e:
                print(f"Error creating coupon: {str(e)}")
                # Si falla, usar código genérico como fallback
                coupon_code = "BIENVENIDA10"
        
        # Añadir contacto a Klaviyo con nombre, apellidos y teléfono
        contact_result = dispatch_add_contact(
            email, 
            first_name=first_name, 
            last_name=last_name, 
            phone=phone,
            source=source
        )
        
        # Enviar notificación a info@mikels.es (Klaviyo + Brevo fallback)
        dispatch_newsletter_subscription_notification(email, coupon_code, first_name=first_name, last_name=last_name, phone=phone)
        
        # Enviar email de bienvenida al suscriptor con código de descuento único (Klaviyo + Brevo fallback)
        dispatch_newsletter_welcome(email, coupon_code)
        
        return jsonify({
            'success': True,
            'message': 'Subscription successful',
            'coupon_code': coupon_code,
            'contact_id': contact_result.get('id') if contact_result else None
        }), 200
        
    except Exception as e:
        print(f"Error in newsletter subscription: {str(e)}")
        return jsonify({'error': str(e)}), 500
