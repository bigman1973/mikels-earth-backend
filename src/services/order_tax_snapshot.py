"""Tax snapshots for paid web-order receipts.

Holded's product master is read before a Stripe Checkout session opens. The
resolved tax rules are stored against a one-time checkout token, then applied
to the exact gross line amounts returned by Stripe after payment. This avoids
name-based tax inference and prevents the customer receipt from recalculating
amounts in the browser or email template.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from src.services.holded_tax_service import (
    FISCAL_EXPANSION_PACK_SKUS,
    FiscalValidationError,
    _fiscal_pack_components,
    _largest_remainder_cents,
    _reference_pvp_with_vat,
    build_holded_master_index,
    resolve_tax_profile,
)
from src.services.money import as_eur


CENT = Decimal("0.01")


def _as_decimal(value: Any, field: str) -> Decimal:
    return as_eur(value, field=field)


def build_tax_rule_snapshot(
    order_items: list[dict[str, Any]],
    holded_products: list[dict[str, Any]],
    web_reference_prices: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Resolve every ordered SKU from the current Holded master catalogue.

    The result is compact, JSON-compatible and contains no prices. It records
    the tax rates and approved relative allocation weights required to process
    the exact charged Stripe line amounts later.
    """
    index = build_holded_master_index(holded_products)
    rules: dict[str, dict[str, Any]] = {}

    for item in order_items:
        sku = str(item.get("sku") or "").strip()
        if not sku:
            raise FiscalValidationError("Una línea de checkout no tiene SKU; no se puede validar su IVA.")
        if sku in rules:
            continue
        product = index.by_sku.get(sku)
        if not product:
            raise FiscalValidationError(f"El SKU {sku} no existe en el catálogo maestro de Holded.")

        profile = resolve_tax_profile(product, index)
        if sku not in FISCAL_EXPANSION_PACK_SKUS:
            if profile.iva_rate is None:
                raise FiscalValidationError(f"El producto {sku} no tiene un IVA único para el recibo.")
            rules[sku] = {"kind": "single", "rate": str(profile.iva_rate)}
            continue

        components = []
        for component in _fiscal_pack_components(product, index):
            component_profile = resolve_tax_profile(component.product, index)
            if component_profile.iva_rate is None:
                raise FiscalValidationError(f"El escandallo de {sku} contiene un pack mixto anidado no permitido.")
            component_sku = str(component.product.get("sku") or "").strip()
            weight = _reference_pvp_with_vat(component_sku, web_reference_prices) * component.units
            components.append({
                "rate": str(component_profile.iva_rate),
                "weight": str(weight),
            })
        if not components:
            raise FiscalValidationError(f"El pack {sku} no tiene componentes fiscales para el recibo.")
        rules[sku] = {"kind": "mixed", "components": components}

    return rules


def calculate_tax_totals_from_snapshot(
    order_items: list[dict[str, Any]], tax_rules: Mapping[str, dict[str, Any]],
) -> tuple[Decimal, Decimal]:
    """Return (tax base, tax) using exact, already-charged Stripe line totals."""
    total_gross = Decimal("0")
    total_base_exact = Decimal("0")

    for item in order_items:
        sku = str(item.get("sku") or "").strip()
        rule = tax_rules.get(sku)
        if not rule:
            raise FiscalValidationError(f"Falta la instantánea de IVA para el SKU {sku or 'vacío'}.")
        gross = _as_decimal(item.get("gross_total", _as_decimal(item.get("price", 0), "precio") * Decimal(str(item.get("quantity", 1) or 1))), "importe de línea")
        if gross < 0:
            raise FiscalValidationError(f"El importe cobrado para {sku} no es válido.")
        total_gross += gross

        if rule.get("kind") == "single":
            rate = Decimal(str(rule.get("rate")))
            total_base_exact += gross / (Decimal("1") + rate)
            continue

        if rule.get("kind") == "mixed":
            components = rule.get("components") or []
            if not components:
                raise FiscalValidationError(f"La instantánea fiscal del pack {sku} no tiene componentes.")
            weights = [Decimal(str(component["weight"])) for component in components]
            allocated_cents = _largest_remainder_cents(gross, weights)
            for component, cents in zip(components, allocated_cents):
                rate = Decimal(str(component["rate"]))
                component_gross = Decimal(cents) / Decimal("100")
                total_base_exact += component_gross / (Decimal("1") + rate)
            continue

        raise FiscalValidationError(f"La instantánea fiscal del SKU {sku} no es válida.")

    tax_base = total_base_exact.quantize(CENT, rounding=ROUND_HALF_UP)
    tax_total = (total_gross - tax_base).quantize(CENT, rounding=ROUND_HALF_UP)
    return tax_base, tax_total
