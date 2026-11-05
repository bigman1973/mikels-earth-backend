"""Server-owned coupon policy, independent of catalogue price/tier protections."""
from src.models.user import db
from src.models.coupon import Coupon
from src.models.web_product import WebProduct
from src.services.cart_promotions import cart_item_references, resolve_product_references

PROMOTION_COUPON_MESSAGE = (
    'Este código no se puede aplicar porque el pedido incluye productos en promoción.'
)
# Exact stored codes confirmed by the owner. Incoming lookup remains case-insensitive;
# descriptions, historical collaborators and friends codes cannot grant permission.
PROMOTION_COLLABORATOR_CODES = frozenset({'IRVIANCESTRAL', 'dr.gemmavalls'})
# These six campaign containers remain promotional even with a stale persisted
# flag or a forged ordinary ID/slug. Ordinary full-price SKUs are NOT coupon vetoes.
PROMOTION_CAMPAIGN_SKUS = frozenset({
    'BF26-TEMP6', 'BF26-TEMP12', 'BF26-ECO6',
    'BF26-PARA3', 'BF26-PARA6', 'BF26-5L3',
    # Retain reserved legacy campaign identities; never turn them into ordinary units.
    'MIKVET500BF3', 'MIKVET500BF6', 'MIKBIO19BF6',
    'MIKPARA450BF3', 'MIKPARA450BF6', 'MIKVE5LPBF3',
})


def coupon_allows_promotion(coupon):
    """A persisted, currently valid allowlisted coupon must explicitly opt in.

    This is a capability, not coupon validation for a particular order: callers
    must still enforce the existing order minimum and customer-email checks.
    Transient models, dictionaries and client metadata are never capabilities.
    """
    if not isinstance(coupon, Coupon) or not db.inspect(coupon).persistent:
        return False
    if coupon.valid_in_promotion is not True or coupon.code not in PROMOTION_COLLABORATOR_CODES:
        return False
    valid, _ = coupon.is_valid()
    return bool(valid)


def _is_campaign_sku(sku):
    return str(sku or '').strip().upper() in PROMOTION_CAMPAIGN_SKUS


def cart_blocks_coupon(items, *, preserve_legacy_sku=False, discount_code=None):
    """Resolve every supplied identity independently before any sale side effect.

    All ordinary products accept otherwise valid coupons, including the five
    catalogue-protected SKUs. Pricing, volume, subscription and mixed-cart gates
    are separate and unchanged. A promotion anywhere rejects the WHOLE coupon
    unless the stored, currently valid collaborator capability allows it.
    Browser promotion/coupon flags and metadata are never read. Hidden add-ons
    supplied as cart lines receive exactly the same checks. The legacy keyword
    is retained for callers, but no longer vetoes ordinary full-price raw SKUs.
    """
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return False
    allows_promotion = None
    for item in cart_item_references(items):
        if not isinstance(item, dict):
            continue
        # Retain productId/productSlug aliases and all independent identities
        # used by this branch; the case-insensitive SKU lookup remains intact.
        products = resolve_product_references(item)
        if item.get('sku'):
            products.append(WebProduct.query.filter(
                db.func.upper(WebProduct.sku) == str(item['sku']).strip().upper()
            ).first())
        contains_promotion = _is_campaign_sku(item.get('sku')) or any(
            product is not None
            and (product.in_promotion is True or _is_campaign_sku(product.sku))
            for product in products
        )
        if contains_promotion:
            if allows_promotion is None:
                coupon = None
                if isinstance(discount_code, str) and discount_code.strip():
                    coupon = Coupon.query.filter(
                        db.func.lower(Coupon.code) == discount_code.strip().lower()
                    ).first()
                allows_promotion = coupon_allows_promotion(coupon)
            if not allows_promotion:
                return True
    return False
