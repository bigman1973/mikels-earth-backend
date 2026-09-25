"""Master-tax resolution and fiscal line allocation for Holded documents.

Holded's product master is the only source for a product's tax type. No caller
may infer VAT from product names, categories, or a fallback value. The helpers
are deliberately pure so validation and allocation complete before any contact
or accounting document is written to Holded.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Iterable, Mapping


TAX_RATE_BY_ID = {
    "s_iva_4": Decimal("0.04"),
    "s_iva_10": Decimal("0.10"),
    "s_iva_21": Decimal("0.21"),
}

# The only two packs that must be represented as fiscal component lines.
FISCAL_EXPANSION_PACK_SKUS = frozenset({"MIKPACKF", "MIKPACKYPO"})

# The Kraft box remains in the Holded recipe for stock and cost. It is purely
# packaging, so it does not receive a fiscal invoice line or allocation value.
NON_FISCAL_PACK_COMPONENT_SKUS = frozenset({"MIKESTKRA"})

# Approved reference PVPs (VAT included) for components without a current
# standalone web SKU. MIKPARJ250 preserves its last standalone shop PVP; the
# two 14 ml miniatures use the value expressly approved by the gestor. Their
# tax always remains the one held in the corresponding Holded master.
REFERENCE_PVP_WITH_VAT = {
    "MIKPARJ250": Decimal("6.50"),
    "MIKVE14": Decimal("1.00"),
    "MIKBIO14": Decimal("1.00"),
}

# This SKU is withdrawn and must not be emitted even if it still exists in the
# historical Holded catalogue.
RETIRED_PACK_SKUS = frozenset({"MIKPAOVE500"})


class FiscalValidationError(ValueError):
    """Raised when a document line cannot be validated against master data."""


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


@dataclass(frozen=True)
class PackComponent:
    """A recipe component preserved with its units and product master record."""

    product: dict[str, Any]
    units: Decimal


def _product_label(product: dict[str, Any]) -> str:
    return product.get("sku") or product.get("name") or product.get("id") or "sin referencia"


def _decimal(value: Any, label: str) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception as exc:
        raise FiscalValidationError(f"{label} no es válido.") from exc


def _extract_tax_ids(raw_taxes: Any) -> list[str]:
    """Normalise tax formats from Holded API versions v1 and v2."""
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
    """Return one recognised master tax ID or fail closed."""
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
    # A Holded variant can be stored as product-id#variant-id. The master
    # product owns the tax, thus the part before # is the relevant identifier.
    return str(selection).split("#", 1)[0]


def _component_units(component: dict[str, Any]) -> Decimal:
    value = component.get("units", component.get("u"))
    units = _decimal(value, "La cantidad de un componente del escandallo")
    if units <= 0:
        raise FiscalValidationError("La cantidad de un componente del escandallo debe ser mayor que cero.")
    return units


def _pack_components(product: dict[str, Any], index: HoldedMasterIndex) -> list[PackComponent]:
    components = product.get("pack_items") or product.get("packItems") or []
    if not components:
        raise FiscalValidationError(
            f"El pack {_product_label(product)} no tiene escandallo en Holded; no se puede emitir ningún documento."
        )

    resolved: list[PackComponent] = []
    for component in components:
        component_id = _component_master_id(component)
        component_master = index.by_id.get(component_id)
        if not component_master:
            raise FiscalValidationError(
                f"El escandallo de {_product_label(product)} referencia el producto inexistente {component_id}."
            )
        resolved.append(PackComponent(product=component_master, units=_component_units(component)))
    return resolved


def _fiscal_pack_components(product: dict[str, Any], index: HoldedMasterIndex) -> list[PackComponent]:
    """Exclude approved non-fiscal packaging while preserving the ERP recipe."""
    fiscal_components = [
        component for component in _pack_components(product, index)
        if str(component.product.get("sku") or "").strip() not in NON_FISCAL_PACK_COMPONENT_SKUS
    ]
    if not fiscal_components:
        raise FiscalValidationError(
            f"El pack {_product_label(product)} no tiene componentes fiscales tras excluir el embalaje."
        )
    return fiscal_components


def resolve_tax_profile(product: dict[str, Any], index: HoldedMasterIndex, visited: tuple[str, ...] = ()) -> TaxProfile:
    """Resolve one product tax profile, expanding only the approved mixed packs."""
    sku = str(product.get("sku") or "").strip()
    label = _product_label(product)

    if sku in RETIRED_PACK_SKUS:
        raise FiscalValidationError(f"{sku} está retirado y bloqueado para emisión de documentos.")

    # Gift cases are presentation, not separate fiscal sales: these and all
    # other products use their own master tax as a single document line.
    if sku not in FISCAL_EXPANSION_PACK_SKUS:
        tax_id = master_tax_id(product)
        return TaxProfile(tax_ids=(tax_id,), iva_rate=TAX_RATE_BY_ID[tax_id], status="single")

    product_id = str(product.get("id") or sku)
    if product_id in visited:
        raise FiscalValidationError(f"El escandallo de {label} contiene una referencia circular.")

    tax_ids: list[str] = []
    for component in _fiscal_pack_components(product, index):
        component_profile = resolve_tax_profile(component.product, index, visited + (product_id,))
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
    decimal_value = _decimal(value, f"El {field} de {sku}")
    if decimal_value <= 0:
        raise FiscalValidationError(f"El {field} de {sku} debe ser mayor que cero.")
    return decimal_value


def _reference_pvp_with_vat(sku: str, web_reference_prices: Mapping[str, Any]) -> Decimal:
    """Return an approved web PVP reference for allocation, never an ERP tariff."""
    if sku in REFERENCE_PVP_WITH_VAT:
        return REFERENCE_PVP_WITH_VAT[sku]
    if sku not in web_reference_prices:
        raise FiscalValidationError(
            f"Falta el PVP web de referencia para el componente fiscal {sku}; emisión cancelada."
        )
    reference = _as_positive_decimal(web_reference_prices[sku], "PVP web de referencia", sku)
    return reference


def _largest_remainder_cents(total_gross: Decimal, weights: list[Decimal]) -> list[int]:
    """Allocate a gross order total to integer cents using the largest remainder rule."""
    if not weights or any(weight <= 0 for weight in weights):
        raise FiscalValidationError("El reparto fiscal necesita valores de referencia positivos.")

    total_cents = int((total_gross * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if total_cents <= 0:
        raise FiscalValidationError("El importe bruto del pack debe ser mayor que cero.")

    total_weight = sum(weights, Decimal("0"))
    exact_cents = [Decimal(total_cents) * weight / total_weight for weight in weights]
    allocated = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact_cents]
    remaining = total_cents - sum(allocated)

    # Stable index order resolves an exact-tie deterministically after the
    # largest remainder comparison.
    remainders = sorted(
        range(len(weights)), key=lambda index: (exact_cents[index] - allocated[index], -index), reverse=True
    )
    for index in remainders[:remaining]:
        allocated[index] += 1
    return allocated


def _display_units(units: Decimal) -> int | float:
    return int(units) if units == units.to_integral_value() else float(units)


def _expand_pack_document_items(
    order_item: dict[str, Any],
    product: dict[str, Any],
    index: HoldedMasterIndex,
    web_reference_prices: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Expand one approved pack using its gross, post-discount order amount.

    The order item price is the amount returned by Stripe for the pack after
    its own volume discount or coupon allocation. The allocation is therefore
    performed on the actually charged gross amount, before cent rounding.
    """
    pack_sku = str(product.get("sku") or "").strip()
    pack_units = _as_positive_decimal(order_item.get("quantity", 1), "cantidad", pack_sku)
    if pack_units != pack_units.to_integral_value():
        raise FiscalValidationError(f"La cantidad del pack {pack_sku} debe ser un número entero.")

    # Stripe's gross_total is the authoritative charged amount of the cart
    # line after its proportional coupon allocation. Older orders do not have
    # it, so they retain the historical unit-price × quantity fallback.
    if order_item.get("gross_total") is not None:
        total_gross = _as_positive_decimal(order_item.get("gross_total"), "importe bruto", pack_sku)
    else:
        unit_price_with_vat = _as_positive_decimal(order_item.get("price"), "precio", pack_sku)
        total_gross = unit_price_with_vat * pack_units
    fiscal_components = _fiscal_pack_components(product, index)

    component_profiles = [resolve_tax_profile(component.product, index) for component in fiscal_components]
    if any(profile.is_mixed for profile in component_profiles):
        raise FiscalValidationError(f"El escandallo de {pack_sku} contiene un pack mixto anidado no permitido.")

    weights = [
        _reference_pvp_with_vat(str(component.product.get("sku") or ""), web_reference_prices)
        * component.units
        for component in fiscal_components
    ]
    allocated_cents = _largest_remainder_cents(total_gross, weights)

    document_items: list[dict[str, Any]] = []
    for component, profile, cents in zip(fiscal_components, component_profiles, allocated_cents):
        component_sku = str(component.product.get("sku") or "").strip()
        units = component.units * pack_units
        gross = Decimal(cents) / Decimal("100")
        # Holded receives a unit base price. Six decimals keep each tax-included
        # line stable to cents after multiplication by the component units.
        unit_base = (gross / (Decimal("1") + profile.iva_rate) / units).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        document_items.append({
            "name": component.product.get("name") or component_sku,
            "description": f"Incluido en {product.get('name') or pack_sku}",
            "units": _display_units(units),
            "subtotal": float(unit_base),
            "tax": profile.tax_ids[0],
            "sku": component_sku,
        })

    return document_items


def prepare_simple_document_item(order_item: dict[str, Any], index: HoldedMasterIndex) -> dict[str, Any]:
    """Build one validated simple-product line, never guessing its tax."""
    sku = str(order_item.get("sku") or "").strip()
    if not sku:
        raise FiscalValidationError("Una línea de pedido web no tiene SKU; no se puede emitir un documento.")

    product = index.by_sku.get(sku)
    if not product:
        raise FiscalValidationError(f"El SKU {sku} no existe en el catálogo maestro de Holded.")
    if sku in FISCAL_EXPANSION_PACK_SKUS:
        raise FiscalValidationError(f"{sku} debe emitirse mediante el desglose fiscal aprobado.")

    profile = resolve_tax_profile(product, index)
    price_with_iva = _as_positive_decimal(order_item.get("price"), "precio", sku)
    units = _as_positive_decimal(order_item.get("quantity", 1), "cantidad", sku)
    unit_base = (price_with_iva / (Decimal("1") + profile.iva_rate)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )

    return {
        "name": order_item.get("name") or product.get("name") or sku,
        "description": order_item.get("description", ""),
        "units": _display_units(units),
        "subtotal": float(unit_base),
        "tax": profile.tax_ids[0],
        "sku": sku,
    }


def prepare_document_items(
    order_items: Iterable[dict[str, Any]],
    holded_products: Iterable[dict[str, Any]],
    web_reference_prices: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate and prepare every document line before any Holded write.

    `web_reference_prices` contains PVPs with VAT for individual web products.
    It is required only when a mixed pack has to be expanded.
    """
    index = build_holded_master_index(holded_products)
    web_reference_prices = web_reference_prices or {}
    prepared: list[dict[str, Any]] = []

    for order_item in order_items:
        sku = str(order_item.get("sku") or "").strip()
        product = index.by_sku.get(sku)
        if not product:
            raise FiscalValidationError(f"El SKU {sku or 'vacío'} no existe en el catálogo maestro de Holded.")
        if sku in FISCAL_EXPANSION_PACK_SKUS:
            prepared.extend(_expand_pack_document_items(order_item, product, index, web_reference_prices))
        else:
            prepared.append(prepare_simple_document_item(order_item, index))

    if not prepared:
        raise FiscalValidationError("El pedido no tiene líneas válidas para emitir en Holded.")
    return prepared
