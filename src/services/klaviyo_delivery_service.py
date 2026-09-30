"""Auditable, idempotent delivery of Klaviyo events with bounded retries."""
import hashlib
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4

import requests
from sqlalchemy import and_

from src.models.klaviyo_delivery import KlaviyoDelivery
from src.models.user import db

KLAVIYO_API_URL = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-10-15"
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=8))
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1


def _api_key():
    return os.getenv("KLAVIYO_API_KEY", "").strip().replace("\n", "").replace("\r", "").replace(" ", "")


def _headers():
    return {
        "Authorization": f"Klaviyo-API-Key {_api_key()}",
        "accept": "application/vnd.api+json",
        "content-type": "application/vnd.api+json",
        "revision": KLAVIYO_REVISION,
    }


def _safe_summary(value):
    return (value or "").replace("\n", " ").replace("\r", " ")[:1000]


def _event_payload(delivery):
    profile_attributes = {"email": delivery.profile_email}
    profile_attributes.update(delivery.profile_attributes or {})
    attributes = {
        "properties": delivery.properties or {},
        "metric": {"data": {"type": "metric", "attributes": {"name": delivery.event_name}}},
        "profile": {"data": {"type": "profile", "attributes": profile_attributes}},
        "time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unique_id": delivery.idempotency_key,
    }
    if delivery.value is not None:
        attributes["value"] = delivery.value
    return {"data": {"type": "event", "attributes": attributes}}


def _derived_key(event_name, profile_email, properties, value, profile_attrs):
    payload = json.dumps(
        {
            "event_name": event_name,
            "profile_email": profile_email,
            "properties": properties,
            "value": value,
            "profile_attrs": profile_attrs,
        },
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return f"event-{hashlib.sha256(payload.encode("utf-8")).hexdigest()}"


def _send_critical_alert(delivery):
    """Send an independent operational alert; it never uses Klaviyo itself."""
    webhook_url = os.getenv("DELIVERY_ALERT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        delivery.alert_status = "not_configured"
        delivery.alert_error = "DELIVERY_ALERT_WEBHOOK_URL no configurada"
        print(
            "CRITICAL KLAVIYO DELIVERY FAILURE: "
            f"id={delivery.id} event={delivery.event_name} status={delivery.status}; "
            "independent alert channel is not configured"
        )
        return

    payload = {
        "text": (
            "FALLO CRÍTICO DE ENTREGA KLAVIYO\n"
            f"Evento: {delivery.event_name}\n"
            f"Destinatario: {delivery.profile_email}\n"
            f"Entrega: {delivery.id}\n"
            f"Motivo: {delivery.failure_reason or 'sin detalle'}\n"
            f"Intentos: {delivery.attempts}"
        )
    }
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if 200 <= response.status_code < 300:
            delivery.alert_status = "accepted"
            delivery.alerted_at = datetime.utcnow()
            delivery.alert_error = None
        else:
            delivery.alert_status = "failed"
            delivery.alert_error = _safe_summary(response.text) or f"HTTP {response.status_code}"
    except requests.RequestException as exc:
        delivery.alert_status = "failed"
        delivery.alert_error = _safe_summary(str(exc))


def _attempt(delivery):
    """Attempt one delivery and persist the exact provider outcome."""
    delivery.attempts += 1
    delivery.status = "retrying"
    delivery.next_attempt_at = None
    now = datetime.utcnow()

    if not _api_key():
        delivery.http_status = None
        delivery.failure_reason = "KLAVIYO_API_KEY no configurada"
        delivery.response_summary = None
    else:
        try:
            response = requests.post(
                f"{KLAVIYO_API_URL}/events",
                headers=_headers(),
                json=_event_payload(delivery),
                timeout=15,
            )
            delivery.http_status = response.status_code
            delivery.response_summary = _safe_summary(response.text)
            if response.status_code == 202:
                delivery.status = "accepted"
                delivery.accepted_at = now
                delivery.failure_reason = None
                delivery.next_attempt_at = None
                db.session.commit()
                print(f"[KLAVIYO] accepted delivery={delivery.id} event={delivery.event_name}")
                return True
            delivery.failure_reason = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            delivery.http_status = None
            delivery.response_summary = None
            delivery.failure_reason = _safe_summary(str(exc)) or exc.__class__.__name__

    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "failed"
        delivery.next_attempt_at = None
    else:
        delivery.status = "retrying"
        delivery.next_attempt_at = now + RETRY_DELAYS[delivery.attempts - 1]

    if delivery.critical:
        _send_critical_alert(delivery)
    db.session.commit()
    print(
        f"[KLAVIYO] failed delivery={delivery.id} event={delivery.event_name} "
        f"attempt={delivery.attempts} reason={delivery.failure_reason}"
    )
    return False


def queue_and_send_event(event_name, profile_email, properties, value=None, unique_id=None, profile_attrs=None, critical=False):
    """Persist an outbound event, then deliver it once synchronously.

    The durable row is committed before HTTP starts. A server restart therefore
    cannot make a completed request disappear from the retry ledger.
    """
    if not profile_email:
        raise ValueError("Klaviyo event requires a profile email")

    idempotency_key = unique_id or _derived_key(event_name, profile_email, properties, value, profile_attrs)
    existing = KlaviyoDelivery.query.filter_by(idempotency_key=idempotency_key).first()
    if existing:
        if existing.status == "accepted":
            return True
        # A previous request is already queued for retry. Do not create a
        # duplicate event or reset its retry budget.
        return False

    delivery = KlaviyoDelivery(
        event_name=event_name,
        profile_email=profile_email,
        properties=properties or {},
        profile_attributes=profile_attrs or None,
        value=value,
        idempotency_key=idempotency_key,
        critical=critical,
        status="pending",
    )
    db.session.add(delivery)
    db.session.commit()
    return _attempt(delivery)


def retry_due_deliveries(limit=100):
    """Retry due events exactly once each; returns a summary for cron logs."""
    now = datetime.utcnow()
    deliveries = (
        KlaviyoDelivery.query.filter(
            and_(
                KlaviyoDelivery.status.in_(("pending", "retrying")),
                KlaviyoDelivery.next_attempt_at.isnot(None),
                KlaviyoDelivery.next_attempt_at <= now,
            )
        )
        .order_by(KlaviyoDelivery.next_attempt_at.asc(), KlaviyoDelivery.id.asc())
        .limit(limit)
        .all()
    )
    accepted = 0
    for delivery in deliveries:
        accepted += int(_attempt(delivery))
    return {"processed": len(deliveries), "accepted": accepted, "failed": len(deliveries) - accepted}


def run_retry_job():
    """Application-independent cron entry point for a Railway cron service."""
    from flask import Flask

    app = Flask(__name__)
    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url or "sqlite:////tmp/app.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
        result = retry_due_deliveries()
        print(json.dumps(result, sort_keys=True))
        db.session.remove()
    return result
