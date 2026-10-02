"""Canonical order-receipt data shared by the storefront and Klaviyo events.

The order receipt is deliberately a snapshot assembled when payment is saved.
Neither the confirmation page nor email templates calculate money or infer
customer fields. They render this persisted structure instead.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from src.services.money import as_eur


CENT = Decimal("0.01")
RECEIPT_VERSION = 1
# The approved 360 px brand asset is hosted in the Klaviyo library. This URL is
# intentionally stable, unlike Vite's hashed build assets, and is used by the
# customer and internal email templates.
RECEIPT_LOGO_URL = "https://cdn.klaviyomail.com/company/R53vEW/images/8039af27-bab3-4421-b130-b7c73836d581.png"


def _amount(value: Any) -> Decimal:
    return as_eur(value if value is not None else 0, field="importe del pedido")


def format_eur(value: Any) -> str:
    """Render a saved EUR amount in the approved Spanish format."""
    amount = _amount(value)
    rendered = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{rendered} €"


def format_receipt_datetime(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d/%m/%Y %H:%M")


def _item_total(item: dict[str, Any]) -> Decimal:
    if item.get("gross_total") is not None:
        return _amount(item["gross_total"])
    return _amount(item.get("price", 0)) * Decimal(str(item.get("quantity", 1) or 1))


def build_receipt_snapshot(order: Any) -> dict[str, Any]:
    """Build the immutable presentation snapshot from one persisted order.

    The numeric fields are included for the storefront. The matching `display`
    fields are sent to Klaviyo so its templates never perform money arithmetic.
    """
    lines = []
    for item in order.items or []:
        quantity = int(item.get("quantity", 1) or 1)
        line_total = _item_total(item).quantize(CENT, rounding=ROUND_HALF_UP)
        lines.append({
            "name": str(item.get("name") or "Producto"),
            "quantity": quantity,
            "amount": float(line_total),
            "amount_display": format_eur(line_total),
        })

    subtotal = _amount(getattr(order, "tax_base", None) if getattr(order, "tax_base", None) is not None else order.subtotal)
    shipping = _amount(order.shipping_cost)
    tax = _amount(getattr(order, "tax_total", None) if getattr(order, "tax_total", None) is not None else 0)
    total = _amount(order.total)

    shipping_lines = [
        value for value in [
            order.shipping_address,
            " ".join(value for value in [order.shipping_postal_code, order.shipping_city] if value),
            order.shipping_country,
        ] if value
    ]
    billing_lines = [
        value for value in [
            order.fiscal_name,
            f"NIF/CIF: {order.fiscal_nif}" if order.fiscal_nif else None,
            order.fiscal_address,
            " ".join(value for value in [order.fiscal_postal_code, order.fiscal_city] if value),
        ] if value
    ]

    return {
        "version": RECEIPT_VERSION,
        "brand": {
            "name": "Mikel's Fruit",
            "logo_url": RECEIPT_LOGO_URL,
        },
        "heading": "Pedido confirmado",
        "order_number": order.order_number,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "paid_at_display": format_receipt_datetime(order.paid_at),
        "lines": lines,
        "totals": {
            "subtotal": float(subtotal),
            "subtotal_display": format_eur(subtotal),
            "shipping": float(shipping),
            "shipping_display": "GRATIS" if shipping == 0 else format_eur(shipping),
            "tax": float(tax),
            "tax_display": format_eur(tax),
            "total": float(total),
            "total_display": format_eur(total),
        },
        "shipping": {
            "lines": shipping_lines,
            "phone": order.customer_phone or "",
        },
        "billing": {
            "requested": bool(order.needs_invoice),
            "lines": billing_lines,
        },
        "confirmation": {
            "email": order.customer_email,
            "sent": bool(order.email_sent),
        },
        "next_steps": [
            "Preparamos tu pedido.",
            "Cuando salga, te enviaremos el seguimiento por correo.",
        ],
    }
