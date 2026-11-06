"""Approved BF26 catalogue definitions; no seed, activation, stock or API call.

One cart unit is one complete pack. Recipes must be created manually by Jordi
in Holded and confirmed before use. Retirement on 2026-11-30 is manual only:
this module must never activate/retire products by date or schedule a job.
Unit references are Jordi's declaration of 2026-11-05, not DB price history.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class CampaignPack:
    sku: str
    slug: str
    name: str
    price: Decimal
    component_sku: str
    component_units: int
    display_unit_price: Decimal
    usual_unit_price: Decimal
    display_discount_percent: int
    unit_label: str


CAMPAIGN_PACKS = (
    CampaignPack(
        'BF26-TEMP6', 'aceite-temprano-sin-filtrar-cosecha-2025-26-pack-6',
        'Aceite de Oliva Virgen Extra Temprano sin filtrar · Cosecha 2025/26 · Pack de 6',
        Decimal('59.70'), 'MIKVET5002526', 6,
        Decimal('9.95'), Decimal('19.90'), 50, 'botella',
    ),
    CampaignPack(
        'BF26-TEMP12', 'aceite-temprano-sin-filtrar-cosecha-2025-26-caja-12',
        'Aceite de Oliva Virgen Extra Temprano sin filtrar · Cosecha 2025/26 · Caja de 12',
        Decimal('119.40'), 'MIKVET5002526', 12,
        Decimal('9.95'), Decimal('19.90'), 50, 'botella',
    ),
    CampaignPack(
        'BF26-ECO6', 'black-friday-aceite-ecologico-pack-6',
        'Aceite de Oliva Virgen Extra Ecológico · Pack de 6',
        Decimal('71.40'), 'MIKBIO19', 6,
        Decimal('11.90'), Decimal('19.90'), 40, 'botella',
    ),
    CampaignPack(
        'BF26-PARA3', 'black-friday-paraguayo-720g-pack-3',
        'Paraguayo en Almíbar 720 g · Pack de 3',
        Decimal('30.90'), 'MIKPARA450', 3,
        Decimal('10.30'), Decimal('17.15'), 40, 'tarro',
    ),
    CampaignPack(
        'BF26-PARA6', 'black-friday-paraguayo-720g-pack-6',
        'Paraguayo en Almíbar 720 g · Pack de 6',
        Decimal('61.70'), 'MIKPARA450', 6,
        Decimal('10.28'), Decimal('17.15'), 40, 'tarro',
    ),
    CampaignPack(
        'BF26-5L3', 'black-friday-aceite-5l-caja-3',
        'Aceite de Oliva Virgen Extra PET 5 L · Caja de 3',
        Decimal('96.75'), 'MIKVE5LP', 3,
        Decimal('32.25'), Decimal('43.00'), 25, 'garrafa',
    ),
)
CAMPAIGN_PACKS_BY_SKU = MappingProxyType({pack.sku: pack for pack in CAMPAIGN_PACKS})
CAMPAIGN_PACKS_BY_SLUG = MappingProxyType({pack.slug: pack for pack in CAMPAIGN_PACKS})


def campaign_pack_for(product: Any) -> CampaignPack | None:
    """Match an own SKU only, never alias a pack to its internal component."""
    sku = product if isinstance(product, str) else getattr(product, 'sku', None)
    return CAMPAIGN_PACKS_BY_SKU.get(str(sku or '').strip())


def protect_campaign_pack_public_properties(product: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Publish an independent complete-pack price, never stale bottle tiers.

    The persisted master price remains authoritative. This does not change a
    row, set inPromotion, assign stock or revive any archived offer. The
    approved title also protects previous-harvest identity in English payloads.
    """
    pack = campaign_pack_for(product)
    if pack is None:
        return data
    from src.services.money import as_eur

    result = dict(data)
    for key in (
        'originalPrice', 'tieredDiscount', 'volumeDiscount', 'addons',
        'subscriptionDiscount', 'subscriptionFrequencies', 'subscriptionTerms',
        'pricingData',
    ):
        result.pop(key, None)
    result['name'] = pack.name
    result['subscriptionAvailable'] = False
    master_price = as_eur(product.price, field='precio maestro')
    result['price'] = float(master_price)
    # Presentation only: never multiply rounded PARA6 unit price into a total.
    # If the master drifts, omit the comparison instead of overriding its price.
    if master_price == pack.price:
        result['pricingData'] = {
            'campaign': 'BF26',
            'basis': 'unit',
            'unitPrice': str(pack.display_unit_price),
            'usualUnitPrice': str(pack.usual_unit_price),
            'unitLabel': pack.unit_label,
            'discountPercent': pack.display_discount_percent,
            'referenceSource': 'Jordi · orden 05-11-2026',
        }
    return result
