"""Authoritative delivery-destination rules for paid web checkout.

The browser may show the policy early, but the checkout endpoint is the final
boundary.  No destination, postal-code rule or Baleares minimum can be bypassed
by calling the API directly.
"""
from __future__ import annotations

from decimal import Decimal
import re
import unicodedata
from typing import Any

from src.services.money import as_eur

BALEARES_PREFIX = "07"
EXCLUDED_SPANISH_PREFIXES = ("35", "38", "51", "52")
BALEARES_MINIMUM_ORDER = Decimal("59.00")


class DeliveryPolicyError(ValueError):
    """A customer-facing delivery-policy failure with a stable API code."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _normalized_text(value: Any) -> str:
    raw = str(value or "").strip().casefold()
    return "".join(
        character
        for character in unicodedata.normalize("NFD", raw)
        if unicodedata.category(character) != "Mn"
    )


def normalize_destination_country(value: Any) -> str | None:
    """Normalize the checkout country to its supported two-letter code."""
    country = _normalized_text(value)
    if country in {"espana", "spain", "es"}:
        return "ES"
    if country in {"portugal", "pt"}:
        return "PT"
    return None


def normalized_postal_code(value: Any) -> str:
    """Return the alphanumeric postal-code representation used for policy checks."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "").upper())


def validate_delivery_destination(
    *, country: Any, postal_code: Any, order_total: Any,
) -> str:
    """Validate an order against the published destination policy.

    Returns the normalized country code on success.  The amount is the final
    payable products total, after any cart discounts and before the always-free
    shipping line, so a discount cannot be used to bypass the Baleares minimum.
    """
    country_code = normalize_destination_country(country)
    if country_code is None:
        raise DeliveryPolicyError(
            "DESTINATION_NOT_SERVED",
            "Solo enviamos a España (península y Baleares) y Portugal.",
        )

    postal = normalized_postal_code(postal_code)
    if country_code == "ES" and postal.startswith(EXCLUDED_SPANISH_PREFIXES):
        raise DeliveryPolicyError(
            "DESTINATION_NOT_SERVED",
            "No enviamos a Canarias, Ceuta ni Melilla.",
        )

    payable_total = as_eur(order_total, field="total del pedido")
    if country_code == "ES" and postal.startswith(BALEARES_PREFIX) and payable_total < BALEARES_MINIMUM_ORDER:
        raise DeliveryPolicyError(
            "BALEARES_MINIMUM_ORDER",
            "En Baleares el pedido mínimo es de 59,00 €.",
        )

    return country_code
