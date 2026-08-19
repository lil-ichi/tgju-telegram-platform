# -*- coding: utf-8 -*-
"""tgju_core/categories.py — Channel categories (state/categories.json).

Extracted verbatim from tgju_platform.py (lines 830–895): category
defaults, the load/save helpers, channel_category() and the two API
handlers.  No behavior change.
"""
import json
import os

from tgju_core.state import get_channels  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CATEGORY_DEFAULTS = {
    "ch1": "ارز", "ch2": "طلا", "ch3": "فلزات", "ch4": "طلا",
    "ch5": "ارز دیجیتال", "ch6": "بازار جهانی", "ch7": "نفت و انرژی",
    "ch8": "اخبار و تحلیل", "ch9": "قیمت‌ها",
}
CATEGORIES_PATH = os.path.join(BASE, "state", "categories.json")


def load_categories() -> dict:
    """Extra categories the user created in the UI (name -> {icon})."""
    try:
        with open(CATEGORIES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_categories(cats: dict):
    os.makedirs(os.path.dirname(CATEGORIES_PATH), exist_ok=True)
    with open(CATEGORIES_PATH, "w", encoding="utf-8") as f:
        json.dump(cats, f, ensure_ascii=False, indent=1)


def channel_category(c: dict) -> str:
    return (c.get("category") or "").strip() or CATEGORY_DEFAULTS.get(c["id"], "عمومی")
