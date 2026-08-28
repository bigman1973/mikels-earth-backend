"""Canonical commerce pricing for checkout.

The browser may send stale display data, but every amount returned by this module is
reconstructed from the current database state before a Stripe session is created.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from src.models.coupon import Coupon
from src.models.web_product import WebProduct
from src.models.user import db


CENT = Decimal("0.01")
HUNDRED = Decimal("100")


class PricingError(ValueError):
    """Actionable pricing error safe to expose to the storefront."""

    def __init__(self, code, message, status_code=400, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self):
        payload = {"error": self.code, "message": self.message}
        payload.update(self.details)
        return payload


def as_money(value):
    """Convert a database value to a deterministic two-decimal Decimal."""
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingError("INVALID_PRICE", "El producto no tiene un precio válido.") from exc


def to_cents(value):
    return int((as_money(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def normalize_quantity(value):
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise PricingError("INVALID_QUANTITY", "La cantidad del producto no es válida.") from exc
    if quantity < 1 or quantity > 999:
        raise PricingError("INVALID_QUANTITY", "La cantidad debe estar entre 1 y 999.")
    return quantity


def resolve_product(item):
    """Resolve an active product by stable identity; never by translated name."""
    product = None
    product_id = item.get("product_id", item.get("id"))
    slug = item.get("slug")
    sku = item.get("sku")

    if product_id not in (None, ""):
        try:
            product = db.session.get(WebProduct, int(product_id))
        except (TypeError, ValueError):
            product = None
    if product is None and sku:
        product = WebProduct.query.filter_by(sku=sku).first()
    if product is None and slug:
        product = WebProduct.query.filter_by(slug=slug).first()

    if product is None or not product.active:
        raise PricingError(
            "PRODUCT_UNAVAILABLE",
            "Uno de los productos del carrito ya no está disponible.",
            409,
            {"product_id": product_id, "slug": slug},
        )
    if product.sold_out:
        raise PricingError(
            "PRODUCT_SOLD_OUT",
            f"El producto «{product.name}» está agotado.",
            409,
            {"product_id": product.id, "slug": product.slug},
        )
    return product


def _discount_percent_for_quantity(product, quantity):
    """Mirror the existing storefront rule: tiered discount wins over simple volume."""
    applicable = Decimal("0")
    tiers = product.tiered_discount or []
    if tiers:
        ordered = sorted(tiers, key=lambda tier: int(tier.get("minQuantity", 0)))
        for tier in ordered:
            if quantity >= int(tier.get("minQuantity", 0)):
                applicable = Decimal(str(tier.get("discount", 0)))
        return applicable

    volume = product.volume_discount or {}
    if volume and quantity >= int(volume.get("minQuantity", 0)):
        return Decimal(str(volume.get("discount", 0)))
    return applicable


def _localized_product(product, locale):
    lang = "en" if str(locale).lower().startswith("en") else "es"
    return product.to_frontend_dict(lang=lang)


def price_one_time_item(item, locale="es"):
    product = resolve_product(item)
    quantity = normalize_quantity(item.get("quantity", 1))
    base_price = as_money(product.price)
    discount_percent = _discount_percent_for_quantity(product, quantity)
    unit_price = (base_price * (HUNDRED - discount_percent) / HUNDRED).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    localized = _localized_product(product, locale)

    return {
        "product": product,
        "product_id": product.id,
        "sku": product.sku,
        "slug": product.slug,
        "name": localized["name"],
        "weight": product.weight,
        "image": product.image,
        "quantity": quantity,
        "base_unit_amount": to_cents(base_price),
        "unit_amount": to_cents(unit_price),
        "discount_percent": float(discount_percent),
        "volume_discount": product.volume_discount,
        "tiered_discount": product.tiered_discount,
        "line_total": to_cents(unit_price) * quantity,
    }


def price_subscription_item(item, locale="es"):
    product = resolve_product(item)
    if not product.subscription_available:
        raise PricingError(
            "SUBSCRIPTION_UNAVAILABLE",
            f"El producto «{product.name}» no admite suscripción.",
            409,
        )

    quantity = normalize_quantity(item.get("quantity", 1))
    frequency_value = item.get("subscription_frequency") or item.get("subscriptionFrequency")
    frequency = next(
        (
            option
            for option in (product.subscription_frequencies or [])
            if option.get("value") == frequency_value
        ),
        None,
    )
    if frequency is None:
        raise PricingError("INVALID_SUBSCRIPTION_FREQUENCY", "Frecuencia de suscripción no válida.")

    discount_percent = Decimal(str(frequency.get("discount", 0)))
    base_price = as_money(product.price)
    unit_price = (base_price * (HUNDRED - discount_percent) / HUNDRED).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    localized = _localized_product(product, locale)

    return {
        "product": product,
        "product_id": product.id,
        "sku": product.sku,
        "slug": product.slug,
        "name": localized["name"],
        "quantity": quantity,
        "frequency": frequency_value,
        "base_unit_amount": to_cents(base_price),
        "unit_amount": to_cents(unit_price),
        "discount_percent": float(discount_percent),
        "line_total": to_cents(unit_price) * quantity,
    }


def price_coupon(code, customer_email, subtotal_cents):
    """Validate and calculate a coupon from current DB state, never client totals."""
    if not code:
        return None, 0

    coupon = Coupon.query.filter(
        db.func.lower(Coupon.code) == str(code).lower().strip()
    ).first()
    if coupon is None:
        raise PricingError("INVALID_COUPON", "El cupón ya no es válido.", 409)

    subtotal = (Decimal(subtotal_cents) / 100).quantize(CENT)
    valid, message = coupon.is_valid(
        order_amount=float(subtotal), customer_email=customer_email
    )
    if not valid:
        raise PricingError("INVALID_COUPON", message, 409)

    discount = as_money(coupon.calculate_discount(float(subtotal)))
    return coupon, min(to_cents(discount), subtotal_cents)


def catalog_version(products):
    timestamps = [product.updated_at for product in products if product.updated_at]
    if not timestamps:
        return "unknown"
    latest = max(timestamps)
    if not isinstance(latest, datetime):
        return str(latest)
    return latest.isoformat(timespec="microseconds") + "Z"
