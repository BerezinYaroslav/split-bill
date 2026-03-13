from decimal import Decimal

from paytogether.parser import attach_service_charge, clean_item_name, parse_receipt, try_parse_item


def test_try_parse_item_extracts_name_qty_and_price():
    item = try_parse_item("Роллы Филадельфия 2 900,00")

    assert item is not None
    assert item.name == "Роллы Филадельфия"
    assert item.quantity == Decimal("2")
    assert item.total_price == Decimal("900.00")


def test_try_parse_item_handles_thousands_prefix_split_by_ocr():
    item = try_parse_item("Морской гребешок с пастой 1 1 290,00")

    assert item is not None
    assert item.name == "Морской гребешок с пастой"
    assert item.quantity == Decimal("1")
    assert item.total_price == Decimal("1290.00")


def test_try_parse_item_handles_qty_glued_to_thousands_prefix():
    item = try_parse_item("Шашлык из куриного бедра 31 110,00")

    assert item is not None
    assert item.name == "Шашлык из куриного бедра"
    assert item.quantity == Decimal("3")
    assert item.total_price == Decimal("1110.00")


def test_clean_item_name_removes_short_noise_tokens():
    assert (
        clean_item_name('м Глинтвейн "Облепиховый" 35 к у ь 0 мл ео м')
        == 'Глинтвейн "Облепиховый" 35 0 мл'
    )


def test_parse_receipt_applies_discount_to_previous_item():
    text = """
    Наименование Кол-во Сумма
    Роллы Филадельфия 2 900,00
    Скидка 10% (-10%) -90,00
    Чай облепиховый 1 300,00
    Подытог: 1 110,00
    ИТОГО К ОПЛАТЕ: 1 221,00
    """

    receipt = attach_service_charge(parse_receipt(text))

    assert len(receipt.items) == 2
    assert receipt.items[0].discount == Decimal("90.00")
    assert receipt.service_charge == Decimal("111.00")
    assert receipt.items[0].service_share == Decimal("81.00")
    assert receipt.items[0].net_total == Decimal("891.00")
    assert receipt.items[1].net_total == Decimal("330.00")


def test_parse_receipt_handles_multiline_ocr_like_layout():
    text = """
    ste, aN
    ГОСТЕВОЙ СЧЕТ
    Наименование
    Kon-s
    Рулеты из баклажана 180 г
    1
    520,00
    Скидка 10% (-10%)
    -52,00
    Глинтвейн на красном вине
    2
    900,00
    350 мл
    Скидка 10% (-10%)
    -90,00
    Полная сумма:
    1 420,00
    Подытог:
    1 278,00
    ИТОГО К ОПЛАТЕ:
    1 278,00
    """

    receipt = parse_receipt(text)

    assert len(receipt.items) == 2
    assert receipt.items[0].name == "Рулеты из баклажана 180 г"
    assert receipt.items[0].quantity == Decimal("1")
    assert receipt.items[0].discount == Decimal("52.00")
    assert receipt.items[1].name == "Глинтвейн на красном вине 350 мл"
    assert receipt.items[1].quantity == Decimal("2")
    assert receipt.items[1].discount == Decimal("90.00")
    assert receipt.subtotal == Decimal("1278.00")
    assert receipt.total == Decimal("1278.00")
