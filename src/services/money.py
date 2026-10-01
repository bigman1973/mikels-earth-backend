"""Exact EUR calculations shared by checkout, persistence and accounting paths."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


class MoneyValueError(ValueError):
    """Raised when an external monetary value cannot be represented as EUR."""


def as_eur(value: Any, *, field: str = "importe") -> Decimal:
    """Normalise an external value to an exact, two-decimal EUR amount."""
    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise MoneyValueError(f"El {field} no es un importe válido.") from exc
    if not amount.is_finite():
        raise MoneyValueError(f"El {field} no es un importe válido.")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def eur_to_cents(value: Any, *, field: str = "importe") -> int:
    """Convert exact EUR to Stripe's integer-cent representation."""
    return int((as_eur(value, field=field) * HUNDRED).to_integral_exact())


def cents_to_eur(cents: Any, *, field: str = "importe") -> Decimal:
    """Convert Stripe integer cents to an exact, two-decimal EUR amount."""
    try:
        integer_cents = int(cents)
    except (TypeError, ValueError) as exc:
        raise MoneyValueError(f"El {field} en céntimos no es válido.") from exc
    return (Decimal(integer_cents) / HUNDRED).quantize(CENT)


def eur_metadata(value: Any, *, field: str = "importe") -> str:
    """Return an exact dot-decimal string suitable for Stripe metadata."""
    return format(as_eur(value, field=field), ".2f")
