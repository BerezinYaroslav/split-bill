from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, Iterable, List

from .models import Allocation, Receipt, ReceiptItem, money


def auto_assign_evenly(receipt: Receipt, participants: List[str]) -> None:
    if not participants:
        return
    for index, item in enumerate(receipt.items):
        if item.participants:
            continue
        participant = participants[index % len(participants)]
        item.participants = [participant]


def assign_item(item: ReceiptItem, participants: Iterable[str]) -> None:
    normalized = [participant.strip() for participant in participants if participant.strip()]
    item.participants = list(dict.fromkeys(normalized))


def calculate_allocations(receipt: Receipt) -> List[Allocation]:
    totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for item in receipt.items:
        if not item.participants:
            continue
        split_between = len(item.participants)
        share = money(item.net_total / split_between)
        allocated = Decimal("0")

        for participant in item.participants[:-1]:
            totals[participant] += share
            allocated += share

        last_participant = item.participants[-1]
        totals[last_participant] += money(item.net_total - allocated)

    return [
        Allocation(participant=participant, amount=money(amount))
        for participant, amount in sorted(totals.items())
    ]


def render_allocations(allocations: List[Allocation]) -> str:
    if not allocations:
        return "Пока нет распределённых позиций."

    return "\n".join(
        f"{allocation.participant} -> {allocation.amount:.2f} RUB"
        for allocation in allocations
    )


def add_shared_amount(
    allocations: List[Allocation],
    participants: Iterable[str],
    amount: Decimal,
) -> List[Allocation]:
    normalized_participants = [participant.strip() for participant in participants if participant.strip()]
    unique_participants = list(dict.fromkeys(normalized_participants))
    totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for allocation in allocations:
        totals[allocation.participant] += allocation.amount

    if not unique_participants or amount <= 0:
        return [
            Allocation(participant=participant, amount=money(total))
            for participant, total in sorted(totals.items())
        ]

    share = money(amount / len(unique_participants))
    allocated = Decimal("0")
    for participant in unique_participants[:-1]:
        totals[participant] += share
        allocated += share

    last_participant = unique_participants[-1]
    totals[last_participant] += money(amount - allocated)

    return [
        Allocation(participant=participant, amount=money(total))
        for participant, total in sorted(totals.items())
    ]
