# -*- coding: utf-8 -*-
"""Persistent fallback cache: saves last successful prices + news to disk.

When tgju.org goes down, the platform reads from these files instead of
posting empty content. This survives server restarts.

Architecture:
  state/fallback_prices.json  — {slug: row, ..., "_saved_at": timestamp}
  state/fallback_news.json    — [{id, title, url, body, ...}, ..., {_saved_at: timestamp}]
  state/fallback_analysis.json — {channel_id: last_analysis_text, ...}

Each file is updated ONLY when fresh data arrives successfully.
On failure, the fallback reader loads the file and returns it as stale data.
"""
import os
import json
import time
from tgju_engine_config import BASE_DIR

STATE_DIR = os.path.join(BASE_DIR, "state")
PRICES_FILE = os.path.join(STATE_DIR, "fallback_prices.json")
NEWS_FILE = os.path.join(STATE_DIR, "fallback_analysis.json")
NEWS_ARTICLES_FILE = os.path.join(STATE_DIR, "fallback_news_articles.json")

# Maximum age for fallback data (hours) before we refuse to use it
MAX_FALLBACK_AGE_HOURS = 12


def _ts():
    return time.time()


def _age_hours(filepath):
    """Return hours since the file was last saved, or inf if not found."""
    try:
        with open(filepath, encoding="utf-8") as f:
            d = json.load(f)
        saved = d.get("_saved_at", 0)
        return (_ts() - saved) / 3600
    except Exception:
        return float("inf")


# ── Prices ─────────────────────────────────────────────────────────────────

def save_fallback_prices(rows: dict):
    """Persist the current price cache to disk. Only saves non-empty data."""
    if not rows:
        return
    data = dict(rows)
    data["_saved_at"] = _ts()
    try:
        with open(PRICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=None)
    except Exception:
        pass


def load_fallback_prices() -> dict:
    """Load the last-known-good prices from disk. Returns {} if too old."""
    age = _age_hours(PRICES_FILE)
    if age > MAX_FALLBACK_AGE_HOURS:
        return {}
    try:
        with open(PRICES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d.pop("_saved_at", None)
        return d
    except Exception:
        return {}


def fallback_prices_age_seconds():
    """How many seconds old the fallback cache is. None if no file."""
    try:
        with open(PRICES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return int(_ts() - d.get("_saved_at", 0))
    except Exception:
        return None


# ── News articles (per-channel) ───────────────────────────────────────────

def save_fallback_news(channel_id: str, articles: list):
    """Cache the fetched news articles for a channel."""
    if not articles:
        return
    try:
        existing = {}
        try:
            with open(NEWS_ARTICLES_FILE, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
        existing[channel_id] = {"articles": articles[:20], "_saved_at": _ts()}
        with open(NEWS_ARTICLES_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=None)
    except Exception:
        pass


def load_fallback_news(channel_id: str) -> list:
    """Load cached news for a channel if fresh enough."""
    try:
        with open(NEWS_ARTICLES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        entry = d.get(channel_id, {})
        age = (_ts() - entry.get("_saved_at", 0)) / 3600
        if age > MAX_FALLBACK_AGE_HOURS:
            return []
        return entry.get("articles", [])
    except Exception:
        return []


# ── Analysis text (per-channel) ───────────────────────────────────────────

def save_fallback_analysis(channel_id: str, text: str):
    """Save the last analysis text for a channel."""
    if not text:
        return
    try:
        existing = {}
        try:
            with open(NEWS_FILE, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
        existing[channel_id] = {"text": text, "_saved_at": _ts()}
        with open(NEWS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=None)
    except Exception:
        pass


def load_fallback_analysis(channel_id: str) -> str:
    """Load cached analysis for a channel if fresh enough."""
    try:
        with open(NEWS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        entry = d.get(channel_id, {})
        age = (_ts() - entry.get("_saved_at", 0)) / 3600
        if age > MAX_FALLBACK_AGE_HOURS:
            return ""
        return entry.get("text", "")
    except Exception:
        return ""
