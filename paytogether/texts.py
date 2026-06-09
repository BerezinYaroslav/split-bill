from __future__ import annotations

import json
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_TEXT_CONFIG_PATH = Path(__file__).with_name("texts.json")


@lru_cache(maxsize=1)
def load_text_config() -> dict[str, Any]:
    config_path = Path(os.getenv("SPLITBILL_TEXT_CONFIG", DEFAULT_TEXT_CONFIG_PATH))
    with config_path.open(encoding="utf-8") as config_file:
        return json.load(config_file)


def text(key: str, **kwargs) -> str:
    value = load_text_config().get("messages", {}).get(key, "")
    return value.format(**kwargs) if kwargs else value


def button(key: str) -> str:
    return load_text_config().get("buttons", {}).get(key, key)


def random_phrase(pool_name: str) -> str:
    phrases = load_text_config().get("phrase_pools", {}).get(pool_name, [])
    if not phrases:
        return ""
    return random.choice(phrases)


def tone_experiments_enabled() -> bool:
    return os.getenv("SPLITBILL_TONE_EXPERIMENTS", "1").lower() not in {"0", "false", "no", "off"}
