from __future__ import annotations

import base64
import json
import os
from decimal import Decimal

from openai import APIStatusError, OpenAI

from .models import Receipt, ReceiptItem, money


class AIReceiptError(RuntimeError):
    pass


PROMPT = """
You extract restaurant receipts from photos.
Return JSON only with this schema:
{
  "merchant": "string",
  "subtotal": number,
  "total": number,
  "service_charge": number,
  "tips": number,
  "items": [
    {
      "name": "string",
      "quantity": number,
      "total_price": number,
      "discount": number
    }
  ]
}

Rules:
- Use the receipt photo only.
- Preserve item names in Russian exactly as best as possible.
- Merge multiline item names into one line.
- Remove OCR garbage and footer/QR/ad text.
- Discounts attached to an item must go into that item's discount field.
- subtotal is after discounts but before service_charge/tips.
- total is final amount to pay.
- service_charge is 0 if absent.
- tips is 0 if absent.
- Ignore QR code, ads, footer text, waiter/store metadata unless needed for merchant.
- If an item quantity is unclear, infer the most likely integer.
- Output valid JSON only, with no markdown and no explanations.
""".strip()


def extract_receipt_with_ai(image_bytes: bytes, model: str | None = None) -> tuple[Receipt, str]:
    return extract_receipt_with_ai_prompt(image_bytes, PROMPT, model=model)


def extract_receipt_with_ai_prompt(
    image_bytes: bytes,
    prompt: str,
    model: str | None = None,
) -> tuple[Receipt, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AIReceiptError("Переменная OPENAI_API_KEY не задана.")

    client = OpenAI(api_key=api_key)
    chosen_model = model or os.getenv("OPENAI_RECEIPT_MODEL", "gpt-4.1-mini")
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    try:
        response = client.responses.create(
            model=chosen_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "detail": "high",
                            "image_url": f"data:image/jpeg;base64,{image_b64}",
                        },
                    ],
                }
            ],
        )
    except APIStatusError as exc:
        if exc.status_code == 402:
            raise AIReceiptError(
                "OpenAI API вернул 402. Пожалуйста, проверьте billing и доступный баланс по ключу."
            ) from exc
        raise AIReceiptError(f"Ошибка OpenAI API ({exc.status_code}): {exc}") from exc
    except Exception as exc:
        raise AIReceiptError(f"Ошибка запроса к OpenAI API: {exc}") from exc

    raw_text = (response.output_text or "").strip()
    if not raw_text:
        raise AIReceiptError("OpenAI не вернул JSON для чека.")

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AIReceiptError("OpenAI вернул невалидный JSON для чека.") from exc

    receipt = Receipt(
        items=[
            ReceiptItem(
                name=str(item["name"]).strip(),
                quantity=Decimal(str(item.get("quantity", 1))),
                total_price=money(item.get("total_price", 0)),
                discount=money(item.get("discount", 0)),
            )
            for item in payload.get("items", [])
            if str(item.get("name", "")).strip()
        ],
        subtotal=money(payload.get("subtotal", 0)),
        total=money(payload.get("total", 0)),
        service_charge=money(payload.get("service_charge", 0)),
        tips=money(payload.get("tips", 0)),
        metadata={"merchant": str(payload.get("merchant", "")).strip()},
    )
    return receipt, raw_text
