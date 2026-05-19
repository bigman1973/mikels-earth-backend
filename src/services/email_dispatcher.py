"""
Email Dispatcher - Servicio dual Klaviyo + Brevo
Intenta enviar por Klaviyo primero, si falla usa Brevo como fallback.
Cuando Klaviyo esté 100% operativo, se puede desactivar Brevo.
"""
import os


def _use_klaviyo():
    """Comprueba si Klaviyo está configurado y habilitado"""
    return bool(os.getenv('KLAVIYO_API_KEY', '').strip())


def _use_brevo():
    """Comprueba si Brevo está configurado"""
    return bool(os.getenv('BREVO_API_KEY', '').strip())


def dispatch_order_notification(order_data):
    """
    Envía notificación de nuevo pedido (email interno a info@mikels.es)
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_notify_new_order
            klaviyo_ok = klaviyo_notify_new_order(order_data)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo order notification: {e}")
    
    # Brevo como fallback si Klaviyo falla o no está configurado
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import notify_new_order_email
            return notify_new_order_email(order_data)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo order notification: {e}")
            return False
    
    return klaviyo_ok


def dispatch_order_confirmation(order_data):
    """
    Envía confirmación de pedido al cliente
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_send_order_confirmation
            klaviyo_ok = klaviyo_send_order_confirmation(order_data)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo order confirmation: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import send_customer_order_confirmation
            return send_customer_order_confirmation(order_data)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo order confirmation: {e}")
            return False
    
    return klaviyo_ok


def dispatch_subscription_notification(subscription_data):
    """
    Envía notificación de nueva suscripción (email interno)
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_notify_new_subscription
            klaviyo_ok = klaviyo_notify_new_subscription(subscription_data)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo subscription notification: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import notify_new_subscription_email
            return notify_new_subscription_email(subscription_data)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo subscription notification: {e}")
            return False
    
    return klaviyo_ok


def dispatch_newsletter_subscription_notification(email, coupon_code=None, first_name=None, last_name=None, phone=None):
    """
    Envía notificación interna de nueva suscripción al newsletter
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_notify_newsletter_subscription
            klaviyo_ok = klaviyo_notify_newsletter_subscription(email, coupon_code, first_name=first_name, last_name=last_name, phone=phone)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo newsletter notification: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import send_newsletter_subscription_notification
            return send_newsletter_subscription_notification(email)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo newsletter notification: {e}")
            return False
    
    return klaviyo_ok


def dispatch_newsletter_welcome(email, coupon_code="BIENVENIDA10"):
    """
    Envía email de bienvenida al newsletter
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_send_newsletter_welcome
            klaviyo_ok = klaviyo_send_newsletter_welcome(email, coupon_code)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo newsletter welcome: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_newsletter_welcome import send_newsletter_welcome_email
            return send_newsletter_welcome_email(email, coupon_code)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo newsletter welcome: {e}")
            return False
    
    return klaviyo_ok


def dispatch_add_contact(email, first_name=None, last_name=None, phone=None, source=None):
    """
    Añade contacto a la plataforma de email marketing (Klaviyo o Brevo)
    """
    klaviyo_result = None
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import add_contact_to_klaviyo
            klaviyo_result = add_contact_to_klaviyo(
                email, 
                first_name=first_name, 
                last_name=last_name,
                phone=phone,
                source=source or "Newsletter Website"
            )
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo add contact: {e}")
    
    # También añadir a Brevo mientras estemos en transición
    brevo_result = None
    if _use_brevo():
        try:
            from src.services.email_service import add_contact_to_brevo
            brevo_result = add_contact_to_brevo(email)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo add contact: {e}")
    
    return klaviyo_result or brevo_result or {"success": False, "error": "No email service configured"}


def dispatch_contact_notification(name, email, phone, message):
    """
    Envía notificación de mensaje de contacto (email interno)
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_notify_contact_message
            klaviyo_ok = klaviyo_notify_contact_message(name, email, phone, message)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo contact notification: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import send_contact_notification
            return send_contact_notification(name, email, phone, message)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo contact notification: {e}")
            return False
    
    return klaviyo_ok


def dispatch_contact_confirmation(name, email, message=''):
    """
    Envía confirmación de mensaje de contacto al cliente
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_send_contact_confirmation
            klaviyo_ok = klaviyo_send_contact_confirmation(name, email, message)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo contact confirmation: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import send_contact_confirmation
            return send_contact_confirmation(name, email)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo contact confirmation: {e}")
            return False
    
    return klaviyo_ok


def dispatch_workshop_visit_notification(nombre, email, telefono, interes):
    """
    Envía notificación de solicitud de visita al obrador (email interno)
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_notify_workshop_visit
            klaviyo_ok = klaviyo_notify_workshop_visit(nombre, email, telefono, interes)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo workshop notification: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import send_workshop_visit_notification
            return send_workshop_visit_notification(nombre, email, telefono, interes)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo workshop notification: {e}")
            return False
    
    return klaviyo_ok


def dispatch_workshop_visit_confirmation(nombre, email, interes='visita'):
    """
    Envía confirmación de solicitud de visita al obrador al visitante
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_send_workshop_visit_confirmation
            klaviyo_ok = klaviyo_send_workshop_visit_confirmation(nombre, email, interes)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo workshop confirmation: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import send_workshop_visit_confirmation
            return send_workshop_visit_confirmation(nombre, email)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo workshop confirmation: {e}")
            return False
    
    return klaviyo_ok


def dispatch_started_checkout(checkout_data):
    """
    Envía evento de inicio de checkout a Klaviyo para tracking de carrito abandonado.
    Solo Klaviyo (no Brevo) — es una funcionalidad exclusiva de Klaviyo.
    """
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_track_started_checkout
            return klaviyo_track_started_checkout(checkout_data)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo started checkout: {e}")
            return False
    return False


def dispatch_product_notification_request(product_name, customer_name, customer_email, customer_phone=''):
    """
    Envía notificación de solicitud de producto (email interno)
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_notify_product_request
            klaviyo_ok = klaviyo_notify_product_request(product_name, customer_name, customer_email, customer_phone)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo product notification: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import send_product_notification_request
            return send_product_notification_request(product_name, customer_name, customer_email, customer_phone)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo product notification: {e}")
            return False
    
    return klaviyo_ok


def dispatch_product_notification_confirmation(product_name, customer_name, customer_email):
    """
    Envía confirmación de solicitud de notificación de producto al cliente
    """
    klaviyo_ok = False
    
    if _use_klaviyo():
        try:
            from src.services.klaviyo_service import klaviyo_send_product_notification_confirmation
            klaviyo_ok = klaviyo_send_product_notification_confirmation(product_name, customer_name, customer_email)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Klaviyo product confirmation: {e}")
    
    if not klaviyo_ok and _use_brevo():
        try:
            from src.services.email_service import send_customer_notification_confirmation
            return send_customer_notification_confirmation(product_name, customer_name, customer_email)
        except Exception as e:
            print(f"⚠️ [DISPATCHER] Error Brevo product confirmation: {e}")
            return False
    
    return klaviyo_ok
