"""Authoritative quantity-tier pricing for one-time Stripe Checkout orders.

The browser presents tiers from ``WebProduct.tiered_discount``. Checkout must
apply exactly that persisted configuration again before accepting an amount.
This module intentionally calculates whole-line totals in Decimal because a
discounted unit price can have more than two decimal places (for example,
17.15 € less 15% is 14.5775 €).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from src.services.money import CENT, MoneyValueError, as_eur

HUNDRED = Decimal("100")


@dataclass(frozen=True)
class CheckoutLinePrice:
    """Validated, server-derived price data for one cart line."""

    base_unit_price: Decimal
    base_line_total: Decimal
    tier_discount_percent: Decimal
    expected_line_total: Decimal
    volume_discount_amount: Decimal


def _decimal(value: Any, *, field: str) -> Decimal:
    """Parse an input amount without prematurely rounding a unit price."""
    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise MoneyValueError(f"El {field} no es un importe válido.") from exc
    if not amount.is_finite() or amount < 0:
        raise MoneyValueError(f"El {field} no es un importe válido.")
    return amount


def _nonnegative_decimal(value: Any) -> Decimal | None:
    try:
        parsed = _decimal(value, field="descuento por cantidad")
    except MoneyValueError:
        return None
    return parsed


def volume_discount_percent(product: Any, quantity: int) -> Decimal:
    """Return the greatest applicable persisted tier discount percentage.

    ``tiered_discount`` is authoritative when it exists. The older single
    ``volume_discount`` remains supported for products that still use it.
    Malformed tiers are ignored rather than becoming a customer-controlled
    price path.
    """
    try:
        normalized_quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("La cantidad debe ser un entero positivo.") from exc
    if normalized_quantity < 1:
        raise ValueError("La cantidad debe ser un entero positivo.")

    tiers = getattr(product, "tiered_discount", None)
    if isinstance(tiers, list):
        applicable: list[tuple[int, Decimal]] = []
        for tier in tiers:
            if not isinstance(tier, dict):
                continue
            try:
                minimum = int(tier.get("minQuantity"))
            except (TypeError, ValueError):
                continue
            discount = _nonnegative_decimal(tier.get("discount", 0))
            if minimum > 0 and discount is not None and normalized_quantity >= minimum:
                applicable.append((minimum, discount))
        if applicable:
            # The highest qualifying threshold wins even if the stored array is
            # reordered in the panel.
            return max(applicable, key=lambda tier: tier[0])[1]

    simple_tier = getattr(product, "volume_discount", None)
    if isinstance(simple_tier, dict):
        try:
            minimum = int(simple_tier.get("minQuantity"))
        except (TypeError, ValueError):
            minimum = 0
        discount = _nonnegative_decimal(simple_tier.get("discount", 0))
        if minimum > 0 and discount is not None and normalized_quantity >= minimum:
            return discount

    return Decimal("0")


def calculate_checkout_line_price(product: Any, quantity: int) -> CheckoutLinePrice:
    """Calculate base and quantity-discounted totals for a cart line."""
    try:
        normalized_quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("La cantidad debe ser un entero positivo.") from exc
    if normalized_quantity < 1:
        raise ValueError("La cantidad debe ser un entero positivo.")

    base_unit_price = as_eur(getattr(product, "price", None), field="precio maestro")
    base_line_total = (base_unit_price * normalized_quantity).quantize(CENT, rounding=ROUND_HALF_UP)
    discount_percent = volume_discount_percent(product, normalized_quantity)
    if discount_percent > HUNDRED:
        raise ValueError("El descuento por cantidad configurado no es válido.")
    expected_line_total = (
        base_line_total * (HUNDRED - discount_percent) / HUNDRED
    ).quantize(CENT, rounding=ROUND_HALF_UP)

    return CheckoutLinePrice(
        base_unit_price=base_unit_price,
        base_line_total=base_line_total,
        tier_discount_percent=discount_percent,
        expected_line_total=expected_line_total,
        volume_discount_amount=(base_line_total - expected_line_total).quantize(CENT),
    )


def sent_line_total(unit_price: Any, quantity: int) -> Decimal:
    """Derive a comparable full-line amount from the browser's unit price."""
    try:
        normalized_quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("La cantidad debe ser un entero positivo.") from exc
    if normalized_quantity < 1:
        raise ValueError("La cantidad debe ser un entero positivo.")
    return (_decimal(unit_price, field="precio") * normalized_quantity).quantize(
        CENT,
        rounding=ROUND_HALF_UP,
    )
