"""Presentation identity for the two independently stocked Temprano harvests.

This never aliases SKUs or modifies catalogue, stock, prices or fiscal rules.
"""
from __future__ import annotations

from src.services.previous_harvest_draft import DRAFT_TITLE, PROPOSED_SKU


TEMPRANO_NAMES_BY_SKU = {
    PROPOSED_SKU: DRAFT_TITLE,
    'MIKVET500': 'Aceite de oliva virgen extra Temprano 2026/27 sin filtrar',
}


def harvest_product_name(sku: str, name: str) -> str:
    """Return the approved exact harvest title for either confirmed SKU.

    Older web/Holded master names can omit or misstate the harvest. These two
    approved names are presentation-only rules, not a product or stock alias.
    All other SKUs keep their trusted catalogue/order names unchanged. Checkout
    captures the result on its immutable line snapshot for later order/emails.
    """
    return TEMPRANO_NAMES_BY_SKU.get(
        str(sku or '').strip(), str(name or '').strip() or 'Producto',
    )
