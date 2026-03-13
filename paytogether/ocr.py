from __future__ import annotations

from io import BytesIO
from typing import Iterable

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class OCRError(RuntimeError):
    pass


def extract_text(image_bytes: bytes, lang: str = "rus+eng") -> str:
    try:
        image = Image.open(BytesIO(image_bytes))
    except Exception as exc:  # pragma: no cover
        raise OCRError("Не удалось открыть изображение чека.") from exc

    texts = []
    try:
        for prepared, config in generate_ocr_variants(image):
            text = pytesseract.image_to_string(prepared, lang=lang, config=config)
            if text.strip():
                texts.append(text)
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRError(
            "Tesseract OCR не установлен. Установите бинарник tesseract и языки rus/eng."
        ) from exc
    except Exception as exc:  # pragma: no cover
        raise OCRError("Не удалось распознать текст чека.") from exc

    if not texts:
        raise OCRError("OCR не вернул текст. Пожалуйста, используйте фотографию с более ровным освещением.")
    return pick_best_text(texts)


def prepare_image(image: Image.Image) -> Image.Image:
    return threshold_image(image)


def threshold_image(image: Image.Image) -> Image.Image:
    grayscale = ImageOps.grayscale(image)
    contrasted = ImageOps.autocontrast(grayscale)
    filtered = contrasted.filter(ImageFilter.MedianFilter(size=3))
    return filtered.point(lambda pixel: 0 if pixel < 170 else 255)


def soft_image(image: Image.Image) -> Image.Image:
    grayscale = ImageOps.grayscale(image)
    contrasted = ImageOps.autocontrast(grayscale)
    enlarged = contrasted.resize(
        (contrasted.width * 2, contrasted.height * 2),
        Image.Resampling.LANCZOS,
    )
    return ImageEnhance.Sharpness(enlarged).enhance(1.8)


def generate_ocr_variants(image: Image.Image) -> Iterable[tuple[Image.Image, str]]:
    rgb = image.convert("RGB")
    yield threshold_image(rgb), "--oem 3 --psm 6"
    yield threshold_image(rgb.rotate(0.2, expand=True, fillcolor="white")), "--oem 3 --psm 4"
    yield soft_image(rgb), "--oem 3 --psm 4"
    yield soft_image(rgb), "--oem 3 --psm 11"


def pick_best_text(texts: list[str]) -> str:
    def score(text: str) -> tuple[int, int, int]:
        lowered = text.lower()
        items_like = sum(1 for line in text.splitlines() if any(char.isdigit() for char in line))
        totals_like = sum(
            1 for marker in ("подыт", "итого", "сумма", "скидка") if marker in lowered
        )
        return totals_like, items_like, len(text)

    return max(texts, key=score)
