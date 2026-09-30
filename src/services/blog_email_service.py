"""Klaviyo-only operational notifications for blog administration."""
import os
from datetime import datetime

NOTIFICATION_EMAIL = os.getenv("BLOG_NOTIFICATION_EMAIL", "info@mikels.es")


def send_blog_notification(action, post_title, post_slug, recipient_email=None):
    """Queue a blog-administration event; no Brevo fallback exists."""
    from src.services.klaviyo_service import send_klaviyo_event

    recipient = recipient_email or NOTIFICATION_EMAIL
    return send_klaviyo_event(
        metric_name="Mikels Blog Administration",
        profile_email=recipient,
        properties={
            "Action": action,
            "PostTitle": post_title,
            "PostSlug": post_slug,
            "PostUrl": f"https://www.mikels.es/blog/{post_slug}",
            "Source": "mikels-backend",
        },
        unique_id=f"blog-{action}-{post_slug}-{datetime.utcnow().isoformat()}",
    )
