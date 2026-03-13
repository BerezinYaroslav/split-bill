from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List


KOPECK = Decimal("0.01")


def money(value: Decimal | float | str) -> Decimal:
    return Decimal(str(value)).quantize(KOPECK, rounding=ROUND_HALF_UP)


@dataclass
class ReceiptItem:
    name: str
    quantity: Decimal
    total_price: Decimal
    discount: Decimal = Decimal("0")
    service_share: Decimal = Decimal("0")
    participants: List[str] = field(default_factory=list)

    @property
    def net_total(self) -> Decimal:
        return money(self.total_price - self.discount + self.service_share)

    @property
    def unit_price(self) -> Decimal:
        if self.quantity == 0:
            return self.net_total
        return money(self.net_total / self.quantity)


@dataclass
class Receipt:
    items: List[ReceiptItem]
    subtotal: Decimal
    total: Decimal
    service_charge: Decimal = Decimal("0")
    tips: Decimal = Decimal("0")
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def extras_total(self) -> Decimal:
        return money(self.service_charge + self.tips)


@dataclass
class Allocation:
    participant: str
    amount: Decimal

