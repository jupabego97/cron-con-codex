"""Verified Alegra resources used by the retail intelligence platform.

The catalog deliberately contains read-oriented business resources.  It is kept in
one place so a new endpoint is an explicit product and data-model decision rather
than another one-off script.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AlegraResource:
    key: str
    collection_path: str
    response_keys: tuple[str, ...]
    order_field: str | None = "id"
    supports_metadata: bool = True
    supports_detail: bool = True
    hydrate_details_by_default: bool = False
    list_params: tuple[tuple[str, str], ...] = ()


BUSINESS_RESOURCES: tuple[AlegraResource, ...] = (
    AlegraResource("contact", "/contacts", ("contact",), list_params=(("mode", "advanced"),)),
    AlegraResource("item", "/items", ("item",), list_params=(("mode", "advanced"),)),
    AlegraResource("warehouse", "/warehouses", ("warehouse",)),
    AlegraResource(
        "seller",
        "/sellers",
        ("seller",),
        order_field=None,
        supports_metadata=False,
        supports_detail=False,
    ),
    AlegraResource("invoice", "/invoices", ("invoice",), hydrate_details_by_default=True),
    AlegraResource("bill", "/bills", ("bill",), hydrate_details_by_default=True),
    AlegraResource("payment", "/payments", ("payment",)),
    AlegraResource(
        "credit_note",
        "/credit-notes",
        ("creditNote", "credit_note"),
        hydrate_details_by_default=True,
    ),
    AlegraResource(
        "inventory_adjustment",
        "/inventory-adjustments",
        ("inventoryAdjustment", "inventory_adjustment"),
        hydrate_details_by_default=True,
    ),
    AlegraResource(
        "warehouse_transfer",
        "/warehouse-transfers",
        ("warehouseTransfer", "warehouse_transfer"),
        order_field=None,
        supports_metadata=False,
        hydrate_details_by_default=True,
    ),
)

RESOURCE_BY_KEY = {resource.key: resource for resource in BUSINESS_RESOURCES}


def resolve_resources(selection: str) -> tuple[AlegraResource, ...]:
    """Resolve `all` or a comma-separated list of supported resource keys."""
    cleaned = [value.strip() for value in selection.split(",") if value.strip()]
    if not cleaned or cleaned == ["all"]:
        return BUSINESS_RESOURCES
    unknown = sorted(set(cleaned).difference(RESOURCE_BY_KEY))
    if unknown:
        supported = ", ".join(resource.key for resource in BUSINESS_RESOURCES)
        raise ValueError(f"Unknown resource(s): {', '.join(unknown)}. Supported: {supported}")
    return tuple(RESOURCE_BY_KEY[value] for value in cleaned)
