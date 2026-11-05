"""Server-owned cart coupon exclusions; never infer promotion from browser flags."""
from src.models.user import db
from src.models.web_product import WebProduct
from src.services.protected_catalogue import is_promotion_protected

PROMOTION_COUPON_MESSAGE = (
    'Este código no se puede aplicar porque el pedido incluye productos en promoción.'
)


def cart_blocks_coupon(items, *, preserve_legacy_sku=False):
    """Resolve every supplied identity independently before any sale side effect.

    A forged ordinary identifier cannot hide another promoted DB identifier.
    Hidden add-ons are ordinary cart lines and receive exactly the same check.
    Legacy coupon previews also retain their existing protected-SKU-only veto;
    browser in_promotion/inPromotion flags are never read.
    """
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        if preserve_legacy_sku and is_promotion_protected(item):
            return True
        products = []
        if item.get('id'):
            products.append(WebProduct.query.get(item['id']))
        if item.get('slug'):
            products.append(WebProduct.query.filter_by(slug=item['slug']).first())
        if item.get('sku'):
            products.append(WebProduct.query.filter(
                db.func.upper(WebProduct.sku) == str(item['sku']).strip().upper()
            ).first())
        if any(
            product is not None
            and (product.in_promotion is True or is_promotion_protected(product))
            for product in products
        ):
            return True
    return False
