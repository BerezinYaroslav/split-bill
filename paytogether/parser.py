from __future__ import annotations

import re
from decimal import Decimal
from typing import List

from .models import Receipt, ReceiptItem, money


PRICE_RE = re.compile(r"(-?\d[\d ]*[.,]\d{2})(?:\D*)$")
SPACELESS_AMOUNT_RE = re.compile(r"(?<!\d)(-?\d{1,3}(?:\d{3})*[.,]\d{2})(?!\d)")
COMBINED_QTY_PRICE_RE = re.compile(
    r"^(?P<qty>\d{1,2})\s+(?P<price>\d{1,3}(?: \d{3})*[.,]\d{2})$"
)
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
DIGIT_RE = re.compile(r"\d")
AMOUNT_TAIL_RE = re.compile(r"^-?\d{1,3}[.,]\d{2}$")
NOISE_TOKEN_RE = re.compile(r"^[A-Za-z]+$")

ALLOWED_SHORT_WORDS = {
    "и",
    "с",
    "в",
    "во",
    "на",
    "из",
    "по",
    "до",
    "от",
    "к",
    "со",
    "б/а",
    "г",
    "гр",
    "кг",
    "мл",
    "л",
}
DESCRIPTOR_HINTS = ("мл", "л", "г", "гр", "кг", "шт", "порц")


def normalize_lines(text: str) -> List[str]:
    lines = []
    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if not line or is_noise_line(line):
            continue
        lines.append(line)
    return lines


def normalize_line(raw_line: str) -> str:
    substitutions = {
        "_": " ",
        ";": ":",
        "“": '"',
        "”": '"',
        "„": '"',
        "’": "'",
        "`": "",
    }
    line = raw_line
    for source, target in substitutions.items():
        line = line.replace(source, target)
    line = re.sub(r"(?<!\w)[|Il](?!\w)", "1", line)
    line = re.sub(r"[<>~^]+", " ", line)
    line = re.sub(r"\s+", " ", line).strip(" .,-:")
    return line


def is_noise_line(line: str) -> bool:
    if set(line) <= {"*", "-", "=", ".", ":"}:
        return True
    if not CYRILLIC_RE.search(line) and not DIGIT_RE.search(line):
        return True
    if DIGIT_RE.search(line) is not None:
        return False
    if CYRILLIC_RE.search(line) is None and len(line) < 3:
        return True
    return False


def parse_decimal(raw_value: str) -> Decimal:
    cleaned = raw_value.replace(" ", "").replace(",", ".")
    return money(cleaned)


def parse_receipt(text: str) -> Receipt:
    lines = normalize_lines(text)
    items = parse_items(extract_item_section(lines))
    metadata = extract_metadata(lines)
    subtotal = find_amount_for_keywords(lines, ("подыт",))
    total = find_amount_for_keywords(lines, ("итого", "к оплат"))

    if subtotal == 0:
        subtotal = money(sum((item.total_price - item.discount) for item in items))
    if total == 0:
        total = subtotal

    service_charge = money(total - subtotal) if total > subtotal else Decimal("0")
    return Receipt(
        items=items,
        subtotal=subtotal,
        total=total,
        service_charge=service_charge,
        metadata=metadata,
    )


def extract_metadata(lines: List[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("открыт") and ":" in line:
            metadata["opened_at"] = line.split(":", 1)[1].strip()
            break
    return metadata


def extract_item_section(lines: List[str]) -> List[str]:
    start_index = 0
    end_index = len(lines)

    for index, line in enumerate(lines):
        if "наименование" in line.lower():
            start_index = index + 1
            break

    for index in range(start_index, len(lines)):
        lowered = lines[index].lower()
        if any(marker in lowered for marker in ("полная сумма", "подыт", "итого к оплат")):
            end_index = index
            break

    return lines[start_index:end_index]


def parse_items(lines: List[str]) -> List[ReceiptItem]:
    items: List[ReceiptItem] = []
    current_item: ReceiptItem | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        lowered = line.lower()

        if "скидка" in lowered and current_item is not None:
            discount, consumed = parse_amount_nearby(lines, index)
            current_item.discount += abs(discount)
            index += consumed
            continue

        item, consumed = try_parse_item_block(lines, index)
        if item is not None:
            items.append(item)
            current_item = item
            index += consumed
            continue

        if current_item is not None and can_extend_item_name(line):
            current_item.name = f"{current_item.name} {line}".strip()
        index += 1

    return items


def try_parse_item_block(lines: List[str], start_index: int) -> tuple[ReceiptItem | None, int]:
    window: List[str] = []

    for index in range(start_index, min(len(lines), start_index + 4)):
        line = lines[index]
        lowered = line.lower()
        if "скидка" in lowered and index != start_index:
            break
        if any(marker in lowered for marker in ("полная сумма", "подыт", "итого к оплат")):
            break

        if window and looks_like_new_item_start(line):
            break

        window.append(line)
        item = try_parse_item(" ".join(window))
        if item is not None:
            return item, index - start_index + 1

    return None, 1


def try_parse_item(line: str) -> ReceiptItem | None:
    line = normalize_line(line)
    parsed_tail = parse_item_tail(line)
    if parsed_tail is None:
        return try_parse_item_with_spaceless_total(line)

    name, qty, price = parsed_tail
    name = clean_item_name(name)
    if not name:
        return None

    return ReceiptItem(name=name, quantity=qty, total_price=price)


def parse_item_tail(line: str) -> tuple[str, Decimal, Decimal] | None:
    tokens = line.split()
    if len(tokens) < 2:
        return None

    last = tokens[-1]
    if not AMOUNT_TAIL_RE.match(last):
        return None

    if len(tokens) >= 3 and tokens[-2].isdigit() and tokens[-3].isdigit():
        name_tokens = tokens[:-3]
        qty = Decimal(tokens[-3].replace(",", "."))
        price = parse_decimal(f"{tokens[-2]} {last}")
        return " ".join(name_tokens), qty, price

    if len(tokens) >= 2 and tokens[-2].isdigit() and len(tokens[-2]) == 2:
        name_tokens = tokens[:-2]
        qty = Decimal(tokens[-2][0])
        price = parse_decimal(f"{tokens[-2][1]} {last}")
        return " ".join(name_tokens), qty, price

    if len(tokens) >= 2 and tokens[-2].isdigit():
        name_tokens = tokens[:-2]
        qty = Decimal(tokens[-2].replace(",", "."))
        price = parse_decimal(last)
        return " ".join(name_tokens), qty, price

    return None


def try_parse_item_with_spaceless_total(line: str) -> ReceiptItem | None:
    condensed = normalize_line(line)
    combined_match = COMBINED_QTY_PRICE_RE.match(condensed)
    if combined_match:
        return ReceiptItem(
            name="позиция",
            quantity=Decimal(combined_match.group("qty").replace(",", ".")),
            total_price=parse_decimal(combined_match.group("price")),
        )

    amount_match = SPACELESS_AMOUNT_RE.search(condensed)
    if not amount_match:
        return None

    prefix = condensed[: amount_match.start()].rstrip()
    qty_match = re.search(r"(?P<qty>\d+(?:[.,]\d+)?)\s*$", prefix)
    if not qty_match:
        return None

    name = normalize_line(condensed[: qty_match.start("qty")])
    if not name:
        return None

    return ReceiptItem(
        name=clean_item_name(name),
        quantity=Decimal(qty_match.group("qty").replace(",", ".")),
        total_price=parse_decimal(amount_match.group(1)),
    )


def find_amount_for_keywords(lines: List[str], keywords: tuple[str, ...]) -> Decimal:
    for index, line in enumerate(lines):
        lowered = line.lower()
        if not all(keyword in lowered for keyword in keywords):
            continue

        amount = parse_trailing_amount(line)
        if amount > 0:
            return amount

        for offset in range(1, 4):
            probe_index = index + offset
            if probe_index >= len(lines):
                break
            amount = parse_trailing_amount(lines[probe_index])
            if amount > 0:
                return amount
    return Decimal("0")


def parse_amount_nearby(lines: List[str], index: int) -> tuple[Decimal, int]:
    amount = parse_trailing_amount(lines[index])
    if amount != 0:
        return amount, 1

    for offset in range(1, 3):
        probe_index = index + offset
        if probe_index >= len(lines):
            break
        amount = parse_trailing_amount(lines[probe_index])
        if amount != 0:
            return amount, offset + 1
    return Decimal("0"), 1


def parse_trailing_amount(line: str) -> Decimal:
    match = PRICE_RE.search(normalize_line(line))
    if not match:
        return Decimal("0")
    return parse_decimal(match.group(1))


def can_extend_item_name(line: str) -> bool:
    normalized = normalize_line(line)
    if CYRILLIC_RE.search(normalized) is None or parse_trailing_amount(normalized) != 0:
        return False
    return is_descriptor_line(normalized)


def clean_item_name(name: str) -> str:
    tokens = normalize_line(name).split()
    cleaned: List[str] = []

    for index, token in enumerate(tokens):
        lowered = token.lower()
        if NOISE_TOKEN_RE.match(token):
            continue
        if lowered == "к" and has_digit_neighbor(tokens, index):
            continue
        if len(lowered) <= 2 and lowered not in ALLOWED_SHORT_WORDS and DIGIT_RE.search(lowered) is None:
            continue
        if lowered in {"м", "у", "а", "я", "ь", "ее", "ео", "сх", "ах", "ые", "чо"}:
            continue
        cleaned.append(token)

    while cleaned and is_bad_edge_token(cleaned[0]):
        cleaned.pop(0)
    while cleaned and is_bad_edge_token(cleaned[-1]):
        cleaned.pop()

    return " ".join(cleaned)


def is_bad_edge_token(token: str) -> bool:
    lowered = token.lower()
    if lowered in ALLOWED_SHORT_WORDS:
        return False
    if DIGIT_RE.search(token):
        return False
    return len(lowered) <= 2


def has_digit_neighbor(tokens: List[str], index: int) -> bool:
    previous_token = tokens[index - 1] if index > 0 else ""
    next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
    return DIGIT_RE.search(previous_token) is not None or DIGIT_RE.search(next_token) is not None


def looks_like_new_item_start(line: str) -> bool:
    normalized = normalize_line(line)
    tokens = normalized.split()
    if len(tokens) < 2:
        return False
    if parse_trailing_amount(normalized) != 0:
        return False
    if any(hint in normalized.lower() for hint in DESCRIPTOR_HINTS):
        return False
    return CYRILLIC_RE.search(normalized) is not None


def is_descriptor_line(line: str) -> bool:
    lowered = line.lower()
    if any(hint in lowered for hint in DESCRIPTOR_HINTS):
        return True
    tokens = lowered.split()
    if len(tokens) <= 3 and any(DIGIT_RE.search(token) for token in tokens):
        return True
    return False


def attach_service_charge(receipt: Receipt) -> Receipt:
    if receipt.service_charge <= 0 or not receipt.items:
        return receipt

    subtotal = sum((item.total_price - item.discount) for item in receipt.items)
    if subtotal <= 0:
        even_share = money(receipt.service_charge / len(receipt.items))
        for item in receipt.items[:-1]:
            item.service_share = even_share
        last_share = receipt.service_charge - sum(item.service_share for item in receipt.items[:-1])
        receipt.items[-1].service_share = money(last_share)
        return receipt

    allocated = Decimal("0")
    for item in receipt.items[:-1]:
        ratio = (item.total_price - item.discount) / subtotal
        share = money(receipt.service_charge * ratio)
        item.service_share = share
        allocated += share

    receipt.items[-1].service_share = money(receipt.service_charge - allocated)
    return receipt


def serialize_receipt(receipt: Receipt) -> str:
    lines = []
    for index, item in enumerate(receipt.items, start=1):
        if item.net_total <= 0:
            continue
        lines.append(
            f"{index}. {item.name} x{item.quantity} = {item.net_total:.2f} RUB"
        )
    lines.append(f"\nПромежуточный итог: {receipt.subtotal:.2f} RUB")
    lines.append(f"Сервисный сбор: {receipt.service_charge:.2f} RUB")
    lines.append(f"Итого: {receipt.total:.2f} RUB")
    return "\n".join(lines)
