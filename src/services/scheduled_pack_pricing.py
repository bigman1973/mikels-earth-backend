"""Authoritative fixed-pack pricing with a date-bounded reservation price.

A pack is a published commercial option, not a checkout discount.  The only
source of truth is ``WebProduct.tiered_discount``.  A tier becomes a fixed
pack when it has both ``actualQuantity`` and either ``reservationPrice`` or
``regularPrice``.  The reservation price is active through ``priceEndsAt``
(UTC ISO-8601); after that instant, the regular price applies automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

from src.services.money import CENT, MoneyValueError, as_eur


def _decimal(value: Any, *, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return as_eur(value, field=field)
    except MoneyValueError:
        return None


def _quantity(tier: dict[str, Any]) -> int | None:
    try:
        value = int(tier.get("actualQuantity", tier.get("minQuantity")))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _ends_at(tier: dict[str, Any]) -> datetime | None:
    value = tier.get("priceEndsAt")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def is_fixed_pack_tier(tier: Any) -> bool:
    """Return whether a tier represents a published fixed-price pack."""
    return (
        isinstance(tier, dict)
        and _quantity(tier) is not None
        and (_decimal(tier.get("reservationPrice"), field="precio de reserva") is not None
             or _decimal(tier.get("regularPrice"), field="precio normal") is not None)
    )


def fixed_pack_tiers(product: Any) -> list[dict[str, Any]]:
    """Read valid fixed packs in ascending physical-bottle quantity."""
    tiers = getattr(product, "tiered_discount", None) or []
    if not isinstance(tiers, list):
        return []
    valid = [dict(tier) for tier in tiers if is_fixed_pack_tier(tier)]
    return sorted(valid, key=lambda tier: _quantity(tier) or 0)


def resolve_fixed_pack_price(tier: dict[str, Any], *, now: datetime | None = None) -> Decimal:
    """Return the live, non-discountable price of one fixed pack."""
    if not is_fixed_pack_tier(tier):
        raise ValueError("El tramo no es un paquete de precio fijo válido.")

    reservation_price = _decimal(tier.get("reservationPrice"), field="precio de reserva")
    regular_price = _decimal(tier.get("regularPrice"), field="precio normal")
    deadline = _ends_at(tier)

    if reservation_price is not None and (deadline is None or _now(now) <= deadline):
        return reservation_price
    if regular_price is not None:
        return regular_price
    if reservation_price is not None:
        return reservation_price
    raise ValueError("El paquete no tiene un precio válido.")


def fixed_pack_tier_for_quantity(product: Any, quantity: int) -> dict[str, Any] | None:
    """Find a pack only when its physical quantity is selected exactly."""
    try:
        expected_quantity = int(quantity)
    except (TypeError, ValueError):
        return None
    for tier in fixed_pack_tiers(product):
        if _quantity(tier) == expected_quantity:
            return tier
    return None


def active_catalogue_price(product: Any, *, now: datetime | None = None) -> Decimal:
    """Return the live one-bottle catalogue price for product payloads."""
    one_bottle = fixed_pack_tier_for_quantity(product, 1)
    if one_bottle is not None:
        return resolve_fixed_pack_price(one_bottle, now=now)
    return as_eur(getattr(product, "price", None), field="precio maestro")


def serialize_fixed_pack_tier(tier: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Expose the active and reference prices to the storefront without math."""
    quantity = _quantity(tier)
    if quantity is None:
        raise ValueError("El paquete no tiene cantidad válida.")
    active_price = resolve_fixed_pack_price(tier, now=now)
    regular_price = _decimal(tier.get("regularPrice"), field="precio normal")
    reservation_price = _decimal(tier.get("reservationPrice"), field="precio de reserva")
    deadline = _ends_at(tier)
    result = dict(tier)
    result["actualQuantity"] = quantity
    result["minQuantity"] = quantity
    result["price"] = float(active_price)
    result["regularPrice"] = float(regular_price or active_price)
    if reservation_price is not None:
        result["reservationPrice"] = float(reservation_price)
    if deadline is not None:
        result["priceEndsAt"] = deadline.isoformat().replace("+00:00", "Z")
        result["reservationPriceActive"] = active_price == reservation_price
    result["fixedPack"] = True
    return result


def serialize_tiered_pricing(product: Any, *, now: datetime | None = None) -> list[dict[str, Any]] | None:
    """Serialize existing tiers, resolving only fixed-pack schedules."""
    tiers = getattr(product, "tiered_discount", None)
    if not isinstance(tiers, list) or not tiers:
        return None
    serialized: list[dict[str, Any]] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        serialized.append(
            serialize_fixed_pack_tier(tier, now=now)
            if is_fixed_pack_tier(tier)
            else dict(tier)
        )
    return serialized or None


def fixed_pack_quantity_options(product: Any) -> list[int]:
    return [quantity for tier in fixed_pack_tiers(product) if (quantity := _quantity(tier)) is not None]
