"""SKU-specific catalogue protections for the current Early Harvest oil.

These rules deliberately apply only to the protected SKU.  They are evaluated
at public serialisation and checkout boundaries so persisted legacy catalogue
fields cannot create a discount or mixed promotional sale.
"""
from __future__ import annotations

from typing import Any, Iterable


FULL_PRICE_ONLY_SKUS = frozenset({'MIKVET500'})
# MIKPACKF is the confirmed Holded recipe containing MIKVET500. MIKPACKTP is
# the existing single-oil web pack. Both remain normally sellable at their
# published price, but neither can receive a promotion. MIKPACKCO is
# intentionally not used as a recipe source.
PROTECTED_COMPONENT_PACK_SKUS = frozenset({'MIKPACKF', 'MIKPACKTP'})


def sku_of(product_or_sku: Any) -> str:
    """Read a SKU from a model-like object or a direct identifier."""
    if isinstance(product_or_sku, str):
        return product_or_sku.strip().upper()
    return str(getattr(product_or_sku, 'sku', '') or '').strip().upper()


def is_full_price_only(product_or_sku: Any) -> bool:
    """Whether this is the SKU that must never receive a promotion."""
    return sku_of(product_or_sku) in FULL_PRICE_ONLY_SKUS


def is_protected_component_pack(product_or_sku: Any) -> bool:
    """Whether this is a known mixed pack containing the protected SKU."""
    return sku_of(product_or_sku) in PROTECTED_COMPONENT_PACK_SKUS


def is_promotion_protected(product_or_sku: Any) -> bool:
    """Whether this item or its known recipe must be sold without promotion."""
    return is_full_price_only(product_or_sku) or is_protected_component_pack(product_or_sku)


def contains_full_price_only_product(products: Iterable[Any]) -> bool:
    return any(is_full_price_only(product) for product in products)


def contains_promotion_protected_product(products: Iterable[Any]) -> bool:
    return any(is_promotion_protected(product) for product in products)


def is_mixed_with_full_price_only_product(products: Iterable[Any]) -> bool:
    """Whether a cart combines MIKVET500 with another independently sold SKU."""
    product_list = list(products)
    return (
        contains_full_price_only_product(product_list)
        and any(not is_full_price_only(product) for product in product_list)
    )
