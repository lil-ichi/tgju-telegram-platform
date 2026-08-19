# -*- coding: utf-8 -*-
"""tgju_core/state.py — Channel cache + live price cache (runtime state).

Extracted verbatim from tgju_platform.py (lines 118–213): channel
accessors backed by the RUNTIME cache and the non-blocking price
refresh pipeline.  No behavior change.

Design (unchanged): /api/status serves from the in-memory cache instantly;
a background refresher task re-fetches tgju.org every `fetch_ttl_seconds`
and fills RUNTIME["last_rows"]/["last_fetch"].  refresh_prices() is never
called from request handlers — only from the background refresher or the
scheduler's force path.
"""
import asyncio
import os
import time
from datetime import datetime, timedelta

from tgju_engine_config import (load_channels, log_line,  # noqa: E402
                                channel_state_path)
from tgju_engine_scrape import get_all_prices  # noqa: E402
from tgju_core.runtime import RUNTIME  # noqa: E402
from tgju_core.settings import load_settings  # noqa: E402


def get_channels():
    if RUNTIME["channels"] is None:
        RUNTIME["channels"] = load_channels()
    return RUNTIME["channels"]


def reload_channels():
    RUNTIME["channels"] = load_channels()
    return RUNTIME["channels"]


def get_channel(cid):
    for c in get_channels():
        if c.get("id") == cid:
            return c
    return None


def refresh_prices(force=False):
    """Fetch tgju.org and update the runtime cache.

    NOT called from request handlers — only from the background refresher
    task (or the force path of the scheduler when the cache is stale).
    Returns the (possibly unchanged) cached rows.
    """
    settings = load_settings()
    ttl = max(int(settings.get("fetch_ttl_seconds", 60)), 10)
    now = datetime.now()
    if (not force and RUNTIME["last_fetch"]
            and now - RUNTIME["last_fetch"] < timedelta(seconds=ttl)
            and RUNTIME["last_rows"]):
        return RUNTIME["last_rows"]
    with RUNTIME["refresh_lock"]:
        # double-check inside the lock (two background tasks racing)
        now = datetime.now()
        if (not force and RUNTIME["last_fetch"]
                and now - RUNTIME["last_fetch"] < timedelta(seconds=ttl)
                and RUNTIME["last_rows"]):
            return RUNTIME["last_rows"]
        t0 = time.time()
        try:
            rows = get_all_prices()
        except Exception as e:
            log_line("refresh failed: %s" % e)
            rows = {}
        if rows:
            RUNTIME["last_rows"] = rows
            RUNTIME["last_fetch"] = datetime.now()
            # ── Fallback: persist the good data to disk so a TGJU outage
            #    doesn't blank the bot (survives restarts) ──
            try:
                from tgju_engine_fallback import save_fallback_prices
                save_fallback_prices(rows)
            except Exception:
                pass
        elif not RUNTIME["last_rows"]:
            # First-ever fetch failed OR cache empty: try the disk fallback
            # so a restart during an outage still has something to post.
            try:
                from tgju_engine_fallback import load_fallback_prices
                fb = load_fallback_prices()
                if fb:
                    log_line("TGJU unreachable — serving %d fallback prices from disk" % len(fb))
                    RUNTIME["last_rows"] = fb
                    RUNTIME["last_fetch"] = datetime.now()
                    RUNTIME["degraded"] = True
            except Exception:
                pass
        RUNTIME["last_fetch_duration"] = round(time.time() - t0, 2)
        RUNTIME["refreshing"] = False
    return RUNTIME["last_rows"]


def cached_rows() -> dict:
    """Instant cache read — NEVER performs network I/O."""
    return RUNTIME["last_rows"] or {}


async def background_refresh(force=True):
    """Schedule a refresh in a worker thread; returns immediately."""
    RUNTIME["refreshing"] = True
    asyncio.get_running_loop().run_in_executor(None, refresh_prices, force)


async def refresher_loop():
    """Periodic background refresh: keeps the cache warm every fetch_ttl."""
    while True:
        try:
            settings = load_settings()
            ttl = max(int(settings.get("fetch_ttl_seconds", 60)), 10)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, refresh_prices, True)
        except Exception as e:
            log_line("refresher error: %s" % e)
        await asyncio.sleep(ttl)
