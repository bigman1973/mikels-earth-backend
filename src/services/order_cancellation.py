"""Canonical full-refund notification helpers.

A cancellation email is a presentation of the paid Order receipt, not a new
calculation. Stripe tells us the refunded amount in cents; everything else
comes from the immutable receipt saved at payment confirmation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.models.user import db
from src.services.money import as_eur
from src.services.order_receipt import build_receipt_snapshot, format_eur


def is_reservation_order(order: Any, receipt: dict[str, Any]) -> bool:
    """Identify a reservation from the saved receipt, with an items fallback."""
    if receipt.get("heading") == "Reserva confirmada":
        return True
    return bool(order.items) and all(
        bool(item.get("reservation_only"))
        for item in order.items
        if isinstance(item, dict)
    )


def build_cancellation_order_data(order: Any, refunded_amount: Any) -> dict[str, Any]:
    """Build the Klaviyo payload from the paid receipt plus cancellation data.

    This intentionally never recalculates product lines, discounts, shipping or
    total. The copied receipt is augmented solely with the Stripe-confirmed
    refund amount and the approved cancellation wording.
    """
    receipt = deepcopy(order.receipt_snapshot or build_receipt_snapshot(order))
    refund = as_eur(refunded_amount, field="importe reembolsado")
    reservation = is_reservation_order(order, receipt)

    noun = "reserva" if reservation else "pedido"
    receipt["heading"] = "Reserva anulada" if reservation else "Pedido anulado"
    receipt["cancellation"] = {
        "refunded_amount": float(refund),
        "refunded_amount_display": format_eur(refund),
        "message": (
            f"Hemos anulado tu {noun} y te hemos devuelto {format_eur(refund)}. "
            "Según tu banco, el ingreso puede tardar unos días en aparecer."
        ),
        "support_message": (
            "Si no lo has pedido tú o crees que es un error, responde a este correo."
        ),
    }
    # An explicit cancellation replaces fulfilment steps; it must never promise
    # dispatch or tracking after the order has been refunded.
    receipt["next_steps"] = []

    shipping_address = ", ".join(
        value for value in [
            order.shipping_address,
            " ".join(value for value in [order.shipping_postal_code, order.shipping_city] if value),
            order.shipping_country,
        ] if value
    )
    invoice_data = {
        "name": order.fiscal_name or "",
        "nif": order.fiscal_nif or "",
        "address": order.fiscal_address or "",
        "city": order.fiscal_city or "",
        "postal_code": order.fiscal_postal_code or "",
    }
    return {
        "order_number": order.order_number,
        "customer_email": order.customer_email,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone or "",
        "items": order.items or [],
        "subtotal": Decimal(str(order.subtotal or 0)),
        "total": Decimal(str(order.total or 0)),
        "shipping_address": shipping_address or "No especificada",
        "discount_code": order.discount_code or "",
        "discount_amount": Decimal(str(order.discount_amount or 0)),
        "needs_invoice": bool(order.needs_invoice),
        "invoice_data": invoice_data if order.needs_invoice else {},
        "refund_amount": refund,
        "receipt": receipt,
    }


def _coerce_delivery_result(result: Any) -> tuple[bool, str | None]:
    if isinstance(result, tuple) and len(result) == 2:
        return bool(result[0]), result[1]
    return bool(result), None


def dispatch_full_refund_cancellation(order: Any, refunded_amount: Any) -> tuple[bool, str | None, bool]:
    """Emit one idempotent customer cancellation event and persist its outcome.

    Returns ``(accepted, error, attempted)``. Once Klaviyo has accepted the
    idempotent event, webhook retries do not send another cancellation notice.
    Failed attempts remain retryable on a later Stripe webhook or panel retry.
    """
    if (order.cancellation_delivery_status or "pending") == "accepted":
        return True, None, False

    from src.services.email_dispatcher import (
        dispatch_order_cancellation,
        dispatch_order_delivery_alert,
    )

    order_data = build_cancellation_order_data(order, refunded_amount)
    accepted, error = _coerce_delivery_result(
        dispatch_order_cancellation(order_data, return_result=True)
    )
    attempted_at = datetime.utcnow()

    try:
        order.cancellation_refund_amount = float(as_eur(refunded_amount, field="importe reembolsado"))
        order.cancellation_attempted_at = attempted_at
        order.cancellation_delivery_status = "accepted" if accepted else "failed"
        order.cancellation_delivery_error = None if accepted else (
            error or "Klaviyo no aceptó el aviso de anulación"
        )
        order.cancellation_sent_at = attempted_at if accepted else None
        db.session.commit()
    except Exception as persistence_error:
        db.session.rollback()
        accepted = False
        error = f"No se pudo guardar el estado del aviso de anulación: {persistence_error}"
        print(f"⚠️ Error guardando aviso de anulación para {order.order_number}: {persistence_error}")

    if not accepted:
        try:
            alert_sent = dispatch_order_delivery_alert(
                order_data,
                {"Aviso de anulación al cliente": error},
            )
            order.cancellation_alert_sent = bool(alert_sent)
            db.session.commit()
        except Exception as alert_error:
            db.session.rollback()
            print(f"⚠️ Error enviando alarma de anulación para {order.order_number}: {alert_error}")

    return accepted, error, True
