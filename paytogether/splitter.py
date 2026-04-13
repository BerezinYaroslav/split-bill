from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, Iterable, List

from .models import Allocation, Receipt, ReceiptItem, Settlement, money


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


def calculate_balances(
    allocations: List[Allocation],
    payments: Dict[str, Decimal],
) -> Dict[str, Decimal]:
    balances: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for allocation in allocations:
        balances[allocation.participant] -= allocation.amount

    for participant, amount in payments.items():
        balances[participant] += money(amount)

    return {
        participant: money(amount)
        for participant, amount in sorted(balances.items())
    }


def calculate_settlements(
    allocations: List[Allocation],
    payments: Dict[str, Decimal],
) -> List[Settlement]:
    balances = calculate_balances(allocations, payments)
    creditors = [
        [participant, amount]
        for participant, amount in balances.items()
        if amount > 0
    ]
    debtors = [
        [participant, -amount]
        for participant, amount in balances.items()
        if amount < 0
    ]

    settlements: List[Settlement] = []
    creditor_index = 0
    debtor_index = 0

    while debtor_index < len(debtors) and creditor_index < len(creditors):
        debtor, debt_amount = debtors[debtor_index]
        creditor, credit_amount = creditors[creditor_index]
        transfer_amount = money(min(debt_amount, credit_amount))

        if transfer_amount > 0:
            settlements.append(
                Settlement(
                    debtor=debtor,
                    creditor=creditor,
                    amount=transfer_amount,
                )
            )

        debtors[debtor_index][1] = money(debt_amount - transfer_amount)
        creditors[creditor_index][1] = money(credit_amount - transfer_amount)

        if debtors[debtor_index][1] == 0:
            debtor_index += 1
        if creditors[creditor_index][1] == 0:
            creditor_index += 1

    return settlements


def render_settlements(settlements: List[Settlement]) -> str:
    if not settlements:
        return "Никто никому не должен."

    return "\n".join(
        f"{settlement.debtor} -> {settlement.creditor}: {settlement.amount:.2f} RUB"
        for settlement in settlements
    )
