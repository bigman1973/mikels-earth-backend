"""Consent-gated Meta Conversions API delivery for paid web orders.

No event is sent unless the customer granted marketing consent before Stripe
Checkout was created. The access token only lives in Railway configuration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import os
import re
from typing import Any

import requests


META_GRAPH_VERSION = "v22.0"


def _sha256(value: str) -> str:
    return sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _normalise_phone(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _event_time(value: datetime | None) -> int:
    if value is None:
        return int(datetime.now(timezone.utc).timestamp())
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _purchase_contents(order: Any) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for item in order.items or []:
        sku = str(item.get("sku") or "").strip()
        quantity = max(1, int(item.get("quantity", 1) or 1))
        if not sku:
            raise ValueError(f"Pedido {order.order_number} sin SKU para Meta Purchase")
        line_total = item.get("gross_total", item.get("price", 0))
        unit_price = round(float(line_total) / quantity, 2)
        contents.append({
            "id": sku,
            "quantity": quantity,
            "item_price": unit_price,
        })
    return contents


def dispatch_meta_purchase_event(order: Any, *, marketing_consent: bool, frontend_url: str | None = None) -> dict[str, Any]:
    """Send one server Purchase that deduplicates with browser event_id=order number.

    The function deliberately fails soft: an analytics outage must never affect
    stock, the paid order, or its customer confirmation. Its result is suitable
    for logs and automated verification without exposing an access token.
    """
    if not marketing_consent:
        return {"sent": False, "reason": "marketing_consent_not_granted"}

    pixel_id = str(os.getenv("META_PIXEL_ID") or "").strip()
    access_token = str(os.getenv("META_CONVERSIONS_API_TOKEN") or "").strip()
    if not pixel_id or not access_token:
        return {"sent": False, "reason": "meta_not_configured"}

    try:
        contents = _purchase_contents(order)
    except ValueError as exc:
        return {"sent": False, "reason": "missing_sku", "error": str(exc)}

    email = str(getattr(order, "customer_email", "") or "").strip()
    phone = _normalise_phone(str(getattr(order, "customer_phone", "") or ""))
    user_data: dict[str, list[str]] = {}
    if email:
        user_data["em"] = [_sha256(email)]
    if phone:
        user_data["ph"] = [_sha256(phone)]

    base_url = (frontend_url or os.getenv("FRONTEND_URL") or "https://www.mikels.es").rstrip("/")
    session_id = str(getattr(order, "stripe_checkout_session_id", "") or "")
    payload = {
        "data": [{
            "event_name": "Purchase",
            "event_time": _event_time(getattr(order, "paid_at", None)),
            "event_id": str(order.order_number),
            "event_source_url": f"{base_url}/order-success?session_id={session_id}",
            "action_source": "website",
            "user_data": user_data,
            "custom_data": {
                "currency": "EUR",
                "value": round(float(order.total), 2),
                "content_type": "product",
                "content_ids": [content["id"] for content in contents],
                "contents": contents,
            },
        }],
    }

    try:
        response = requests.post(
            f"https://graph.facebook.com/{META_GRAPH_VERSION}/{pixel_id}/events",
            params={"access_token": access_token},
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        body = response.json() if response.content else {}
        return {
            "sent": True,
            "events_received": body.get("events_received"),
            "fbtrace_id": body.get("fbtrace_id"),
        }
    except requests.RequestException as exc:
        return {"sent": False, "reason": "meta_request_failed", "error": str(exc)}
