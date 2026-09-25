"""Master-tax resolution for Holded documents and the administration margin panel.

The Holded product master is the only source of tax information.  No caller may
infer VAT from a product name, category, or a fallback value.  The helpers in
this module are intentionally pure so document validation can complete before
any contact or accounting document is written to Holded.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable


TAX_RATE_BY_ID = {
    "s_iva_4": Decimal("0.04"),
    "s_iva_10": Decimal("0.10"),
    "s_iva_21": Decimal("0.21"),
}

# These are the only current catalogues that are intended to be invoiced as
# their underlying components.  Adding a SKU requires a reviewed allocation
# rule before document generation can be enabled for it.
EXPANDABLE_PACK_SKUS = frozenset({
    "MIKPACKF",
    "MIKPACKYPO",
    "MIKVET500R",
    "MIKPARA450R",
    "MIKNECT450R",
})

# The business has decided to withdraw these references.  They must never be
# silently invoiced using an obsolete master tax or a guessed composition.
RETIRED_PACK_SKUS = frozenset({"MIKBIO19R", "MIKPAOVE500"})


class FiscalValidationError(ValueError):
    """Raised when a document line cannot be validated against Holded master data."""


class PackAllocationStrategyRequired(FiscalValidationError):
    """Raised until the gestor approves the fiscal allocation of a mixed pack."""


@dataclass(frozen=True)
class TaxProfile:
    """The tax result shown in the panel or used to build a document line."""

    tax_ids: tuple[str, ...]
    iva_rate: Decimal | None
    status: str

    @property
    def is_mixed(self) -> bool:
        return self.status == "mixed"


@dataclass(frozen=True)
class HoldedMasterIndex:
    """Lookup tables built from one immutable Holded catalogue read."""

    by_sku: dict[str, dict[str, Any]]
    by_id: dict[str, dict[str, Any]]


def _product_label(product: dict[str, Any]) -> str:
    return product.get("sku") or product.get("name") or product.get("id") or "sin referencia"


def _extract_tax_ids(raw_taxes: Any) -> list[str]:
    """Normalise the tax formats returned by Holded API versions v1 and v2."""
    if raw_taxes is None:
        return []
    if isinstance(raw_taxes, str):
        return [raw_taxes]
    if isinstance(raw_taxes, dict):
        candidates = [raw_taxes.get(key) for key in ("tax", "id", "tax_id", "code")]
        return [str(candidate) for candidate in candidates if candidate]
    if isinstance(raw_taxes, (list, tuple, set)):
        tax_ids: list[str] = []
        for value in raw_taxes:
            tax_ids.extend(_extract_tax_ids(value))
        return tax_ids
    return []


def master_tax_id(product: dict[str, Any]) -> str:
    """Return the sole recognised product-master tax or fail closed."""
    tax_ids = _extract_tax_ids(product.get("taxes"))
    if not tax_ids:
        tax_ids = _extract_tax_ids(product.get("tax"))

    unique_tax_ids = tuple(dict.fromkeys(tax_ids))
    if not unique_tax_ids:
        raise FiscalValidationError(
            f"El producto maestro de Holded {_product_label(product)} no tiene impuesto configurado."
        )
    if len(unique_tax_ids) != 1:
        raise FiscalValidationError(
            f"El producto maestro de Holded {_product_label(product)} tiene impuestos ambiguos: "
            f"{', '.join(unique_tax_ids)}."
        )

    tax_id = unique_tax_ids[0]
    if tax_id not in TAX_RATE_BY_ID:
        raise FiscalValidationError(
            f"El producto maestro de Holded {_product_label(product)} usa el impuesto no admitido {tax_id}."
        )
    return tax_id


def build_holded_master_index(products: Iterable[dict[str, Any]]) -> HoldedMasterIndex:
    """Index the catalogue and reject duplicate product SKUs before fiscal use."""
    by_sku: dict[str, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}

    for product in products:
        product_id = str(product.get("id") or "").strip()
        if product_id:
            by_id[product_id] = product

        sku = str(product.get("sku") or "").strip()
        if not sku:
            continue
        if sku in by_sku:
            raise FiscalValidationError(f"Hay más de un producto maestro de Holded para el SKU {sku}.")
        by_sku[sku] = product

    return HoldedMasterIndex(by_sku=by_sku, by_id=by_id)


def _component_master_id(component: dict[str, Any]) -> str:
    selection = component.get("product_selection") or component.get("productSelection") or component.get("pid")
    if not selection:
        raise FiscalValidationError("Un escandallo de Holded contiene un componente sin producto asociado.")
    # A Holded variant can be stored as product-id#variant-id.  Tax belongs to
    # the product master, so only the first segment is relevant here.
    return str(selection).split("#", 1)[0]


def _pack_components(product: dict[str, Any], index: HoldedMasterIndex) -> list[dict[str, Any]]:
    components = product.get("pack_items") or product.get("packItems") or []
    if not components:
        raise FiscalValidationError(
            f"El pack {_product_label(product)} no tiene escandallo en Holded; no se puede emitir ningún documento."
        )

    resolved: list[dict[str, Any]] = []
    for component in components:
        component_id = _component_master_id(component)
        component_master = index.by_id.get(component_id)
        if not component_master:
            raise FiscalValidationError(
                f"El escandallo de {_product_label(product)} referencia el producto inexistente {component_id}."
            )
        resolved.append(component_master)
    return resolved


def resolve_tax_profile(product: dict[str, Any], index: HoldedMasterIndex, visited: tuple[str, ...] = ()) -> TaxProfile:
    """Resolve one product's master tax profile, expanding only approved packs."""
    sku = str(product.get("sku") or "").strip()
    label = _product_label(product)

    if sku in RETIRED_PACK_SKUS:
        raise FiscalValidationError(f"{sku} está retirado y bloqueado para emisión de documentos.")

    if sku not in EXPANDABLE_PACK_SKUS:
        tax_id = master_tax_id(product)
        return TaxProfile(tax_ids=(tax_id,), iva_rate=TAX_RATE_BY_ID[tax_id], status="single")

    product_id = str(product.get("id") or sku)
    if product_id in visited:
        raise FiscalValidationError(f"El escandallo de {label} contiene una referencia circular.")

    tax_ids: list[str] = []
    for component in _pack_components(product, index):
        component_profile = resolve_tax_profile(component, index, visited + (product_id,))
        tax_ids.extend(component_profile.tax_ids)

    unique_tax_ids = tuple(dict.fromkeys(tax_ids))
    if not unique_tax_ids:
        raise FiscalValidationError(f"No se pudo resolver ningún impuesto para el escandallo de {label}.")
    if len(unique_tax_ids) == 1:
        tax_id = unique_tax_ids[0]
        return TaxProfile(tax_ids=unique_tax_ids, iva_rate=TAX_RATE_BY_ID[tax_id], status="single")
    return TaxProfile(tax_ids=unique_tax_ids, iva_rate=None, status="mixed")


def tax_profile_for_sku(sku: str, index: HoldedMasterIndex) -> TaxProfile:
    product = index.by_sku.get((sku or "").strip())
    if not product:
        raise FiscalValidationError(f"No existe un producto maestro de Holded para el SKU {sku or 'vacío'}.")
    return resolve_tax_profile(product, index)


def _as_positive_decimal(value: Any, field: str, sku: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except Exception as exc:
        raise FiscalValidationError(f"El {field} de {sku} no es válido.") from exc
    if decimal_value <= 0:
        raise FiscalValidationError(f"El {field} de {sku} debe ser mayor que cero.")
    return decimal_value


def prepare_simple_document_item(order_item: dict[str, Any], index: HoldedMasterIndex) -> dict[str, Any]:
    """Build a validated Holded line for a simple product, never guessing its tax."""
    sku = str(order_item.get("sku") or "").strip()
    if not sku:
        raise FiscalValidationError("Una línea de pedido web no tiene SKU; no se puede emitir un documento.")

    product = index.by_sku.get(sku)
    if not product:
        raise FiscalValidationError(f"El SKU {sku} no existe en el catálogo maestro de Holded.")

    profile = resolve_tax_profile(product, index)
    if sku in EXPANDABLE_PACK_SKUS:
        # The only safe behaviour while the gestor has not approved the price
        # allocation is to reject the operation before any Holded write.
        raise PackAllocationStrategyRequired(
            f"{sku} debe desglosarse en {', '.join(profile.tax_ids)}; "
            "falta la regla de reparto fiscal del precio del pack aprobada por el gestor."
        )

    price_with_iva = _as_positive_decimal(order_item.get("price"), "precio", sku)
    units = _as_positive_decimal(order_item.get("quantity", 1), "cantidad", sku)
    unit_base = (price_with_iva / (Decimal("1") + profile.iva_rate)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "name": order_item.get("name") or product.get("name") or sku,
        "description": order_item.get("description", ""),
        "units": int(units) if units == units.to_integral_value() else float(units),
        "subtotal": float(unit_base),
        "tax": profile.tax_ids[0],
        "sku": sku,
    }


def prepare_document_items(order_items: Iterable[dict[str, Any]], holded_products: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate every cart line before callers create contacts or documents."""
    index = build_holded_master_index(holded_products)
    prepared = [prepare_simple_document_item(item, index) for item in order_items]
    if not prepared:
        raise FiscalValidationError("El pedido no tiene líneas válidas para emitir en Holded.")
    return prepared
