"""Catalogue protections for the three current full-price-only unit SKUs.

MIKVET500 (Temprano 2026/27), MIKVE5LP (single 5 L bottle) and MIKPARA450
(720 g jar) never receive promotions, subscriptions or discounted add-ons.
All independently sold SKUs may share a cart without a checkout-wide discount.
Known legacy component packs remain normally sellable at their master price.
These are exact SKU rules: separately identified fixed-price containers, including
Black Friday draft packs, do not inherit exclusions from their internal units.
"""
from __future__ import annotations

from typing import Any, Iterable


FULL_PRICE_ONLY_SKUS = frozenset({'MIKVET500', 'MIKVE5LP', 'MIKPARA450'})
# The confirmed MIKPACKF recipe contains MIKVET500 and MIKVE5LP (and can
# contain other components). MIKPACKTP is the existing single-oil web pack.
# Both remain normally sellable at their published price, without promotions.
# MIKPACKCO is intentionally not used as a source for the MIKPACKF recipe.
PROTECTED_COMPONENT_PACK_SKUS = frozenset({'MIKPACKF', 'MIKPACKTP'})

PROTECTED_PUBLIC_PROMOTION_FIELDS = frozenset({
    'tieredDiscount', 'volumeDiscount', 'subscriptionDiscount',
    'subscriptionFrequencies', 'subscriptionTerms', 'addons', 'originalPrice',
})


def sku_of(product_or_sku: Any) -> str:
    """Read a SKU from a model-like object, serialized item or identifier."""
    if isinstance(product_or_sku, str):
        return product_or_sku.strip().upper()
    if isinstance(product_or_sku, dict):
        return str(product_or_sku.get('sku') or '').strip().upper()
    return str(getattr(product_or_sku, 'sku', '') or '').strip().upper()


def is_full_price_only(product_or_sku: Any) -> bool:
    """Whether this is one of the three protected standalone unit SKUs."""
    return sku_of(product_or_sku) in FULL_PRICE_ONLY_SKUS


def is_protected_component_pack(product_or_sku: Any) -> bool:
    """Whether this is a known legacy pack containing protected units."""
    return sku_of(product_or_sku) in PROTECTED_COMPONENT_PACK_SKUS


def is_promotion_protected(product_or_sku: Any) -> bool:
    """Whether this item or its known recipe must be sold without promotion."""
    return is_full_price_only(product_or_sku) or is_protected_component_pack(product_or_sku)


def contains_full_price_only_product(products: Iterable[Any]) -> bool:
    return any(is_full_price_only(product) for product in products)


def contains_promotion_protected_product(products: Iterable[Any]) -> bool:
    return any(is_promotion_protected(product) for product in products)


def protect_public_properties(product: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Enforce protected public fields without mutating persisted catalogue data.

    The model and public routes share this boundary for protected units and
    component packs. The master-price override also prevents a stale one-unit
    fixed-pack tier from leaking into either serialization path.
    """
    if not is_promotion_protected(product):
        return data
    from src.services.money import as_eur

    result = dict(data)
    for key in PROTECTED_PUBLIC_PROMOTION_FIELDS:
        result.pop(key, None)
    result['subscriptionAvailable'] = False
    result['price'] = float(as_eur(product.price, field='precio maestro'))
    return result
