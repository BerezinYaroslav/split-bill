from decimal import Decimal

from paytogether.models import Receipt, ReceiptItem
from paytogether.parser import attach_service_charge
from paytogether.splitter import (
    add_shared_amount,
    calculate_allocations,
    calculate_balances,
    calculate_settlements,
)


def test_calculate_allocations_splits_shared_item_and_service_charge():
    receipt = Receipt(
        items=[
            ReceiptItem(name="Роллы", quantity=Decimal("2"), total_price=Decimal("1000"), participants=["Ярослав", "Лера"]),
            ReceiptItem(name="Чай", quantity=Decimal("1"), total_price=Decimal("300"), participants=["Лера"]),
        ],
        subtotal=Decimal("1300"),
        total=Decimal("1430"),
        service_charge=Decimal("130"),
    )

    attach_service_charge(receipt)
    allocations = calculate_allocations(receipt)

    result = {allocation.participant: allocation.amount for allocation in allocations}
    assert result == {"Лера": Decimal("880.00"), "Ярослав": Decimal("550.00")}


def test_add_shared_amount_splits_tip_between_selected_participants():
    allocations = calculate_allocations(
        Receipt(
            items=[
                ReceiptItem(name="Чай", quantity=Decimal("1"), total_price=Decimal("300"), participants=["Лера"]),
            ],
            subtotal=Decimal("300"),
            total=Decimal("300"),
        )
    )

    allocations = add_shared_amount(allocations, ["Лера", "Ярослав"], Decimal("100"))
    result = {allocation.participant: allocation.amount for allocation in allocations}

    assert result == {"Лера": Decimal("350.00"), "Ярослав": Decimal("50.00")}


def test_calculate_settlements_combines_multiple_receipts_with_different_payers():
    allocations = calculate_allocations(
        Receipt(
            items=[
                ReceiptItem(name="Место 1 / Бургер Ярослава", quantity=Decimal("1"), total_price=Decimal("100"), participants=["Ярослав"]),
                ReceiptItem(name="Место 1 / Паста Леры", quantity=Decimal("1"), total_price=Decimal("200"), participants=["Лера"]),
                ReceiptItem(name="Место 2 / Напиток Ярослава", quantity=Decimal("1"), total_price=Decimal("300"), participants=["Ярослав"]),
                ReceiptItem(name="Место 2 / Десерт Леры", quantity=Decimal("1"), total_price=Decimal("300"), participants=["Лера"]),
            ],
            subtotal=Decimal("900"),
            total=Decimal("900"),
        )
    )

    payments = {
        "Ярослав": Decimal("300"),
        "Лера": Decimal("600"),
    }

    balances = calculate_balances(allocations, payments)
    settlements = calculate_settlements(allocations, payments)

    assert balances == {
        "Лера": Decimal("100.00"),
        "Ярослав": Decimal("-100.00"),
    }
    assert len(settlements) == 1
    assert settlements[0].debtor == "Ярослав"
    assert settlements[0].creditor == "Лера"
    assert settlements[0].amount == Decimal("100.00")
