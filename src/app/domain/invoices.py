import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class InvalidInvoicePayload(ValueError):
    """Raised when a source payload cannot identify a sales invoice."""


@dataclass(frozen=True)
class NormalizedInvoiceLine:
    line_number: int
    item_alegra_id: str | None
    item_name: str
    item_reference: str | None
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class NormalizedInvoice:
    alegra_id: str
    issue_date: date | None
    issued_at: datetime | None
    status: str
    client_alegra_id: str | None
    client_name: str | None
    seller_alegra_id: str | None
    seller_name: str | None
    currency_code: str | None
    total: Decimal | None
    total_paid: Decimal | None
    balance: Decimal | None
    source_hash: str
    lines: tuple[NormalizedInvoiceLine, ...]


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _name(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("name"), str):
        return value["name"] or None
    name = value.get("name")
    if isinstance(name, dict):
        value = name
    parts = [
        value.get("fullname"),
        value.get("firstName"),
        value.get("secondName"),
        value.get("lastName"),
        value.get("secondLastName"),
    ]
    joined = " ".join(str(part).strip() for part in parts if part and str(part).strip())
    return joined or None


def _external_id(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("id")
    return str(value) if value is not None and str(value) else None


def _currency_code(payload: dict[str, Any]) -> str | None:
    currency = payload.get("currency")
    if isinstance(currency, dict):
        return _external_id(currency.get("code")) or _external_id(currency.get("name"))
    return _external_id(currency)


def normalize_invoice(payload: dict[str, Any]) -> NormalizedInvoice:
    """Map an Alegra invoice payload without losing financial state or line order."""
    alegra_id = _external_id(payload.get("id"))
    if not alegra_id:
        raise InvalidInvoicePayload("An invoice payload requires a non-empty id")

    lines: list[NormalizedInvoiceLine] = []
    raw_items = payload.get("items")
    if isinstance(raw_items, list):
        for line_number, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                continue
            quantity = _as_decimal(item.get("quantity")) or Decimal("0")
            unit_price = _as_decimal(item.get("price")) or Decimal("0")
            line_total = _as_decimal(item.get("total"))
            if line_total is None:
                line_total = quantity * unit_price
            lines.append(
                NormalizedInvoiceLine(
                    line_number=line_number,
                    item_alegra_id=_external_id(item.get("id")),
                    item_name=str(item.get("name") or "Sin nombre"),
                    item_reference=_external_id(item.get("reference")),
                    quantity=quantity,
                    unit_price=unit_price,
                    line_total=line_total,
                )
            )

    client = payload.get("client")
    seller = payload.get("seller")
    return NormalizedInvoice(
        alegra_id=alegra_id,
        issue_date=_as_date(payload.get("date")),
        issued_at=_as_datetime(payload.get("datetime")),
        status=str(payload.get("status") or "unknown"),
        client_alegra_id=_external_id(client),
        client_name=_name(client),
        seller_alegra_id=_external_id(seller),
        seller_name=_name(seller),
        currency_code=_currency_code(payload),
        total=_as_decimal(payload.get("total")),
        total_paid=_as_decimal(payload.get("totalPaid")),
        balance=_as_decimal(payload.get("balance")),
        source_hash=canonical_payload_hash(payload),
        lines=tuple(lines),
    )
