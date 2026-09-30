"""Klaviyo-only outbound event dispatcher.

This module deliberately has no Brevo fallback. Every outcome is recorded by the
Klaviyo delivery ledger; a false return means that Klaviyo has not accepted the
event and it is queued for bounded retry.
"""
import os


def _use_klaviyo():
    return bool(os.getenv("KLAVIYO_API_KEY", "").strip())


def _dispatch(function_name, *args, **kwargs):
    if not _use_klaviyo():
        print(f"[DISPATCHER] Klaviyo unavailable for {function_name}: KLAVIYO_API_KEY missing")
        return False
    try:
        from src.services import klaviyo_service

        return bool(getattr(klaviyo_service, function_name)(*args, **kwargs))
    except Exception as exc:
        print(f"[DISPATCHER] Klaviyo dispatch error function={function_name}: {exc}")
        return False


def dispatch_order_notification(order_data):
    return _dispatch("klaviyo_notify_new_order", order_data)


def dispatch_order_confirmation(order_data):
    return _dispatch("klaviyo_send_order_confirmation", order_data)


def dispatch_subscription_notification(subscription_data):
    return _dispatch("klaviyo_notify_new_subscription", subscription_data)


def dispatch_newsletter_subscription_notification(email, coupon_code=None, first_name=None, last_name=None, phone=None):
    return _dispatch(
        "klaviyo_notify_newsletter_subscription",
        email,
        coupon_code,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
    )


def dispatch_newsletter_welcome(email, coupon_code="BIENVENIDA10"):
    return _dispatch("klaviyo_send_newsletter_welcome", email, coupon_code)


def dispatch_add_contact(
    email,
    first_name=None,
    last_name=None,
    phone=None,
    source=None,
    whatsapp_marketing_accepted=False,
    subscribe_email=True,
):
    if not _use_klaviyo():
        return {"success": False, "error": "Klaviyo is not configured"}
    try:
        from src.services.klaviyo_service import add_contact_to_klaviyo

        return add_contact_to_klaviyo(
            email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            source=source or "Newsletter Website",
            whatsapp_marketing_accepted=whatsapp_marketing_accepted,
            subscribe_email=subscribe_email,
        )
    except Exception as exc:
        print(f"[DISPATCHER] Klaviyo contact sync error: {exc}")
        return {"success": False, "error": str(exc)}


def dispatch_contact_notification(name, email, phone, message):
    return _dispatch("klaviyo_notify_contact_message", name, email, phone, message)


def dispatch_contact_confirmation(name, email, message=""):
    return _dispatch("klaviyo_send_contact_confirmation", name, email, message)


def dispatch_workshop_visit_notification(nombre, email, telefono, interes):
    return _dispatch("klaviyo_notify_workshop_visit", nombre, email, telefono, interes)


def dispatch_workshop_visit_confirmation(nombre, email, interes="visita"):
    return _dispatch("klaviyo_send_workshop_visit_confirmation", nombre, email, interes)


def dispatch_started_checkout_event(email, customer_name, items, total, checkout_url, items_html, cart_token):
    return _dispatch(
        "klaviyo_track_started_checkout",
        email=email,
        customer_name=customer_name,
        items=items,
        total=total,
        checkout_url=checkout_url,
        items_html=items_html,
        cart_token=cart_token,
    )


def dispatch_post_purchase_event(order_data):
    return _dispatch("klaviyo_send_post_purchase_event", order_data)


def dispatch_review_request(customer_email, customer_name, order_number, items):
    return _dispatch("klaviyo_send_review_request", customer_email, customer_name, order_number, items)


def dispatch_horeca_request(data):
    return _dispatch("klaviyo_notify_horeca_request", data)


def dispatch_horeca_confirmation(data):
    return _dispatch("klaviyo_send_horeca_confirmation", data)


def dispatch_product_notification_request(product_name, customer_name, customer_email, customer_phone=""):
    return _dispatch("klaviyo_notify_product_request", product_name, customer_name, customer_email, customer_phone)


def dispatch_product_notification_confirmation(product_name, customer_name, customer_email):
    return _dispatch("klaviyo_send_product_notification_confirmation", product_name, customer_name, customer_email)


def dispatch_product_notify_subscribe(email, name, product_name, product_id):
    return _dispatch("klaviyo_track_product_notify_subscribe", email, name, product_name, product_id)


def dispatch_product_back_in_stock(email, name, product_name, product_id):
    return _dispatch("klaviyo_track_product_back_in_stock", email, name, product_name, product_id)
