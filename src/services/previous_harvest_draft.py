"""Idempotent catalogue seed for the non-public 2025/26 previous-harvest draft.

This seed is deliberately local to the web catalogue: it does not invoke
Holded, stock synchronisation, publication, or any external service.
"""
from __future__ import annotations

from src.models.user import db
from src.models.web_product import WebProduct


DRAFT_TITLE = 'Aceite de Oliva Virgen Extra Temprano sin filtrar · Cosecha 2025/26'
PROPOSED_SLUG = 'aceite-temprano-sin-filtrar-cosecha-2025-26'
PROPOSED_SKU = 'MIKVET5002526'
LOT = 'L-594091025'
STOREFRONT_NOTICE = 'Esta es la cosecha anterior. La cosecha 2026/27 está aquí, a 19,90 €.'
AWARD = 'Plata OLIVE JAPAN 2026 Tokio y certificado 20260170-1S'


DRAFT_VALUES = {
    'name': DRAFT_TITLE,
    'description': (
        'Aceite de oliva virgen extra temprano sin filtrar de la cosecha 2025/26. '
        'Disponible en pack de 6 y caja de 12 botellas de 500 ml, sin botella suelta. '
        'Pack de 6: 59,70 EUR; caja de 12: 119,40 EUR; 9,95 EUR por botella. '
        f'Lote {LOT}. Reconocido con {AWARD}.'
    ),
    'long_description': (
        'Aceite de oliva virgen extra temprano sin filtrar de la cosecha 2025/26.\n\n'
        'Se presenta en pack de 6 o caja de 12 botellas de 500 ml, sin botella suelta. '
        'Pack de 6: 59,70 EUR; caja de 12: 119,40 EUR; 9,95 EUR por botella. '
        f'Lote {LOT}. Reconocido con {AWARD}.'
    ),
    'name_en': 'Unfiltered Early Harvest Extra Virgin Olive Oil · 2025/26 Harvest',
    'description_en': (
        'Unfiltered early harvest extra virgin olive oil from the 2025/26 harvest. '
        'Available as a pack of 6 or a case of 12 500 ml bottles, not as single bottles. '
        'Pack of 6: EUR 59.70; case of 12: EUR 119.40; EUR 9.95 per bottle. '
        f'Lot {LOT}. Awarded {AWARD}.'
    ),
    'long_description_en': (
        'Unfiltered early harvest extra virgin olive oil from the 2025/26 harvest.\n\n'
        'Available as a pack of 6 or a case of 12 500 ml bottles, not as single bottles. '
        'Pack of 6: EUR 59.70; case of 12: EUR 119.40; EUR 9.95 per bottle. '
        f'Lot {LOT}. Awarded {AWARD}.'
    ),
    'price': 59.70,
    'original_price': None,
    'currency': 'EUR',
    # No image is assigned: the current Temprano photo has not been verified as
    # representing this specific 2025/26 lot.
    'image': None,
    'images': [],
    'category': 'Aceites',
    'tags': [],
    # This is an authorised internal opening inventory only. Public product
    # payloads intentionally expose availability, never this quantity.
    'stock': 1200,
    'weight': '500 ml',
    'sold_out': False,
    'sold_out_message': None,
    'reservation_only': False,
    'reservation_message': None,
    'reservation_stock_total': None,
    'ingredients': 'Aceite de oliva virgen extra 100% sin filtrar',
    'nutritional_info': None,
    'subscription_available': False,
    'subscription_discount': None,
    'subscription_frequencies': [],
    'subscription_terms': None,
    'volume_discount': None,
    'tiered_discount': [
        {
            'actualQuantity': 6,
            'label': 'Pack de 6 botellas de 500 ml',
            'price': 59.70,
        },
        {
            'actualQuantity': 12,
            'label': 'Caja de 12 botellas de 500 ml',
            'price': 119.40,
        }
    ],
    'addons': [],
    'variants': [],
    'includes': [],
    'related_products': [],
    'claims': [],
    'badges': [],
    'featured': False,
    # Store policy applies free shipping to served checkout destinations.
    'free_shipping': True,
    'limited_edition': False,
    'award': AWARD,
    # This must remain a completely non-public draft until expressly changed.
    'active': False,
    'visible_in_store': False,
    'display_order': 0,
    'storefront_notice': STOREFRONT_NOTICE,
}


def _candidate_identifiers(index: int) -> tuple[str, str]:
    """Return a deterministic unused identifier pair for a collision fallback."""
    if index == 0:
        return PROPOSED_SLUG, PROPOSED_SKU
    return (
        f'{PROPOSED_SLUG}-borrador-{index}',
        f'{PROPOSED_SKU}D{index}',
    )


def _is_seeded_draft(product: WebProduct) -> bool:
    """Recognise a prior run without claiming another product on a collision."""
    return (
        product.name == DRAFT_TITLE
        and product.sku is not None
        and product.sku.startswith(PROPOSED_SKU)
        and LOT in (product.description or '')
    )


def _find_existing_seeded_draft() -> WebProduct | None:
    for product in WebProduct.query.filter_by(name=DRAFT_TITLE).all():
        if _is_seeded_draft(product):
            return product
    return None


def seed_previous_harvest_draft() -> tuple[WebProduct, str, bool]:
    """Create or update the private previous-harvest draft without duplicates.

    Returns ``(product, action, used_fallback_identifiers)``.  If the proposed
    slug or SKU belongs to another product, an available deterministic fallback
    pair is selected instead; no existing product is modified in that case.
    The caller owns the transaction and is responsible for committing it.
    """
    existing = _find_existing_seeded_draft()
    if existing is not None:
        product = existing
        action = 'updated'
        used_fallback = product.slug != PROPOSED_SLUG or product.sku != PROPOSED_SKU
    else:
        product = None
        used_fallback = False
        for index in range(1000):
            slug, sku = _candidate_identifiers(index)
            slug_owner = WebProduct.query.filter_by(slug=slug).first()
            sku_owner = WebProduct.query.filter_by(sku=sku).first()
            if slug_owner is None and sku_owner is None:
                product = WebProduct(slug=slug, sku=sku, **DRAFT_VALUES)
                db.session.add(product)
                action = 'created'
                used_fallback = index > 0
                break
        if product is None:
            raise RuntimeError('No se encontró un slug y SKU seguros para el borrador 2025/26.')

    for field, value in DRAFT_VALUES.items():
        setattr(product, field, value)

    return product, action, used_fallback
