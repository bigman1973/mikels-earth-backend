"""Authoritative generic coupon exclusion; never trust browser promotion flags."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


PROMOTION_COUPON_MESSAGE = (
    'Este código no se puede aplicar porque el pedido incluye productos en promoción.'
)


def resolve_product_references(item: dict[str, Any]) -> list[Any]:
    """Resolve all supplied ID/SKU/slug independently, including addon refs.

    No customer promotion state, name or price is trusted. A benign SKU cannot
    hide a promoted ID/slug. Conflicting identities fail checkout closed.
    """
    from src.models.web_product import WebProduct
    from src.models.user import db

    resolved = {}
    for key in ('id', 'productId'):
        product_id = item.get(key)
        if product_id is None or isinstance(product_id, bool):
            continue
        try:
            numeric_id = int(str(product_id))
        except (TypeError, ValueError):
            continue
        product = db.session.get(WebProduct, numeric_id)
        if product is not None:
            resolved[product.id] = product
    sku = str(item.get('sku') or '').strip()
    if sku:
        product = WebProduct.query.filter_by(sku=sku).first()
        if product is not None:
            resolved[product.id] = product
    for key in ('slug', 'productSlug'):
        slug = item.get(key)
        if isinstance(slug, str) and slug.strip():
            product = WebProduct.query.filter_by(slug=slug.strip()).first()
            if product is not None:
                resolved[product.id] = product
    return list(resolved.values())


def cart_item_references(items: Any) -> Iterable[dict[str, Any]]:
    """Read actual cart lines and selected nested add-ons/products.

    Storefront addons are ordinary lines; nested references cannot bypass the
    veto in alternative clients. Catalogue addon *offers* are not selected and
    are deliberately not traversed: only references present in the cart count.
    """
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        yield item
        yield from cart_item_references(item.get('product'))
        yield from cart_item_references(item.get('addons'))


def contains_in_promotion(products: Iterable[Any]) -> bool:
    """Database/model state only, NOT an approved-SKU whitelist."""
    return any(bool(getattr(product, 'in_promotion', False)) for product in products)


def cart_contains_in_promotion(items: Any) -> bool:
    return any(
        contains_in_promotion(resolve_product_references(item))
        for item in cart_item_references(items)
    )
