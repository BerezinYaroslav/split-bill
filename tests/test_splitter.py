from decimal import Decimal

from paytogether.models import Receipt, ReceiptItem
from paytogether.parser import attach_service_charge
from paytogether.splitter import add_shared_amount, calculate_allocations


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
