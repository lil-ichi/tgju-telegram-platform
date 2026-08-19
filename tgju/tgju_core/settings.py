# -*- coding: utf-8 -*-
"""tgju_core/settings.py — Platform settings (state/settings.json).

Extracted verbatim from tgju_platform.py (lines 62–116): the settings
loader/saver and the AI provider catalog.  No behavior change.
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Settings (state/settings.json) ───────────────────────────────────────
SETTINGS_PATH = os.path.join(BASE, "state", "settings.json")
DEFAULT_SETTINGS = {
    "auto_post": True,          # master kill-switch for the scheduler
    "fetch_ttl_seconds": 60,    # background refresher cadence
    "poll_interval_hours": 4,   # scheduler: poll slot every N hours
    "max_profile_workers": 6,   # profile backfill parallelism
    # scheduler
    "scheduler_interval_seconds": 45,  # scheduler loop tick
    "post_retry_seconds": 0,           # delay before retrying a failed post
    # posts / display
    "numeral_system": "fa",            # fa | en  — Persian/Eastern digits
    "default_footer": "",              # appended to every price post
    "price_decimals": 0,               # 0 | 2 — decimal places in prices
    "star_chars": "⭐",                # symbol used for the star line
    "news_max_items": 3,               # max news items per news post
    # polls
    "poll_options_count": 4,           # 2..10 — options per generated poll
    "poll_anonymous": True,            # native Telegram anonymous polls
    # telegram
    "telegram_timeout_seconds": 30,    # HTTP timeout for Telegram API calls
    "telegram_retry_count": 2,         # retries on transient Telegram errors
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s = json.load(f)
        if not isinstance(s, dict):
            s = {}
    except Exception:
        s = {}
    out = dict(DEFAULT_SETTINGS)
    out.update({k: v for k, v in s.items() if k in DEFAULT_SETTINGS})
    return out


def save_settings(s: dict) -> dict:
    s = {k: v for k, v in (s or {}).items() if k in DEFAULT_SETTINGS}
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    return s

# ── AI providers (mirror of the LLM Gateway convention) ──────────────────
PROVIDERS = {
    "mock":      {"label": "بدون هوش مصنوعی (Mock)", "kind": "mock"},
    "gateway":   {"label": "Hermes LLM Gateway (:8788)", "kind": "openai_compat",
                  "base_url": "http://localhost:8788/v1"},
    "openai":    {"label": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1"},
    "openrouter": {"label": "OpenRouter", "kind": "openai_compat",
                   "base_url": "https://openrouter.ai/api/v1"},
}
MODEL_SUGGESTIONS = ["gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet",
                     "claude-3-haiku", "mock"]
