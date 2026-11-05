"""Server-owned cart coupon exclusions; never infer eligibility from browser flags."""
from src.models.user import db
from src.models.coupon import Coupon
from src.models.web_product import WebProduct
from src.services.protected_catalogue import is_promotion_protected
from src.services.cart_promotions import cart_item_references, resolve_product_references

PROMOTION_COUPON_MESSAGE = (
    'Este código no se puede aplicar porque el pedido incluye productos en promoción.'
)
# Exact stored codes confirmed by the owner. Incoming lookup remains case-insensitive;
# descriptions, historical collaborators and friends codes cannot grant permission.
PROMOTION_COLLABORATOR_CODES = frozenset({'IRVIANCESTRAL', 'dr.gemmavalls'})


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


def cart_blocks_coupon(items, *, preserve_legacy_sku=False, discount_code=None):
    """Resolve every supplied identity independently before any sale side effect.

    A forged ordinary identifier cannot hide a protected DB identifier. Hidden
    add-ons are ordinary cart lines and receive exactly the same check. The
    collaborator exception relaxes ONLY persisted in_promotion, never protected
    SKUs (including the legacy raw-SKU preview veto). Browser promotion flags,
    coupon flags, descriptions and metadata are never read.
    """
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return False
    allows_promotion = None
    for item in cart_item_references(items):
        if not isinstance(item, dict):
            continue
        # All callers retain the raw protected-SKU veto, even if a forged ID
        # resolves to an ordinary product or a protected row no longer exists.
        if is_promotion_protected(item):
            return True
        # Retain productId/productSlug aliases and all independent identities
        # used by this branch; the case-insensitive SKU lookup remains intact.
        products = resolve_product_references(item)
        if item.get('sku'):
            products.append(WebProduct.query.filter(
                db.func.upper(WebProduct.sku) == str(item['sku']).strip().upper()
            ).first())
        if any(product is not None and is_promotion_protected(product) for product in products):
            return True
        if any(product is not None and product.in_promotion is True for product in products):
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
