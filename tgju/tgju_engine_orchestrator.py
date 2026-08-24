# -*- coding: utf-8 -*-
"""Channel orchestrator: prices + news + message build for one channel."""
import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from tgju_engine_config import log_line, BASE_DIR, load_slug_overrides
from tgju_engine_scrape import get_all_prices, fetch_profile_price, SLUG_ALIASES
from tgju_engine_news import analysis_line
from tgju_engine_format import build_message

PROFILE_CACHE = {}          # slug -> row (in-memory)
PROFILE_CACHE_TTL = 300     # seconds
PROFILE_CACHE_LOCK = threading.Lock()
_PROFILE_CACHE_FILE = os.path.join(BASE_DIR, "state", "profile_cache.json")
_PROFILE_LOADED = False


def _load_profile_cache():
    global _PROFILE_LOADED, PROFILE_CACHE
    if _PROFILE_LOADED:
        return
    _PROFILE_LOADED = True
    try:
        with open(_PROFILE_CACHE_FILE, encoding="utf-8") as f:
            PROFILE_CACHE = json.load(f)
        # drop stale entries (older than TTL)
        import time
        now = time.time()
        PROFILE_CACHE = {k: v for k, v in PROFILE_CACHE.items()
                         if now - v.get("_t", 0) < PROFILE_CACHE_TTL}
    except Exception:
        PROFILE_CACHE = {}


def _save_profile_cache():
    try:
        with open(_PROFILE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(PROFILE_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


def _backfill_one(slug: str, name: str = "") -> dict:
    _load_profile_cache()
    cached = PROFILE_CACHE.get(slug)
    if cached and cached.get("price"):
        return {k: v for k, v in cached.items() if k != "_t"}
    # Fallback: check the disk fallback cache BEFORE hitting tgju.org —
    # if it has a price, use it (may be stale but better than empty).
    try:
        from tgju_engine_fallback import load_fallback_prices
        fb_all = load_fallback_prices()
        fb = fb_all.get(slug, {})
        if fb and fb.get("price"):
            row = dict(fb)
            row2 = dict(row)
            row2["_t"] = time.time()
            with PROFILE_CACHE_LOCK:
                PROFILE_CACHE[slug] = row2
                _save_profile_cache()
            return row
    except Exception:
        pass
    row = fetch_profile_price(slug, name or None)
    # transient tgju fetch failures happen (timeout/5xx) — retry once
    if not (row and row.get("price")):
        time.sleep(0.6)  # give the WAF a beat between retries
        row = fetch_profile_price(slug, name or None)
    if row and row.get("price"):
        row2 = dict(row)
        row2["_t"] = time.time()
        with PROFILE_CACHE_LOCK:
            PROFILE_CACHE[slug] = row2
            _save_profile_cache()
        return row
    # Both network + profile cache failed: last resort is disk fallback
    try:
        from tgju_engine_fallback import load_fallback_prices
        fb = load_fallback_prices().get(slug, {})
        if fb and fb.get("price"):
            return dict(fb)
    except Exception:
        pass
    return row or {}


def apply_slug_overrides(slug: str, row: dict, overrides: dict | None = None) -> dict:
    """THE shared single-source-of-truth merge for ALL platforms.

    Telegram build_for_channel, WhatsApp keyword/group replies and Bale
    previews all call this so a manual price/name/link set in the
    «داده‌ها و لینک‌ها» tab applies everywhere identically.
    Precedence: manual_price > name override > live row (homepage/profile).
    """
    if overrides is None:
        from tgju_engine_config import load_slug_overrides
        overrides = load_slug_overrides()
    ov = overrides.get(slug) or {}
    merged = dict(row or {})
    if ov.get("manual_price"):
        merged["price"] = str(ov["manual_price"]).replace(",", "").strip()
    if ov.get("name"):
        merged["name"] = ov["name"]
    if ov.get("change_pct") not in (None, ""):
        merged["change_pct"] = str(ov["change_pct"])
    if ov.get("change_amt") not in (None, ""):
        merged["change_amt"] = str(ov["change_amt"])
    if ov.get("dir"):
        merged["dir"] = ov["dir"]
    return merged


def slug_group_map(channel: dict) -> dict:
    m = {}
    for gname, slugs in (channel.get("slug_groups") or {}).items():
        for s in slugs:
            m[s] = gname
    return m


def get_channel_rows(channel: dict, all_rows: dict) -> dict:
    """Select + backfill rows for a channel's slug pool (parallel backfill).

    Manual overrides (state/slug_overrides.json) take precedence over both
    homepage and profile data: manual_price wins, custom name wins,
    custom profile_url wins. Slugs without an alias/override use the
    homepage row as-is.
    """
    wanted = list(channel.get("slugs") or [])
    for slugs in (channel.get("slug_groups") or {}).values():
        wanted.extend(slugs)
    wanted = list(dict.fromkeys(wanted))
    overrides = load_slug_overrides()
    out = {}
    need_backfill = []
    for s in wanted:
        ov = overrides.get(s) or {}
        row = all_rows.get(s) or {}
        # manual price / name override beats everything
        if ov.get("manual_price"):
            merged = dict(row)
            merged["price"] = str(ov["manual_price"]).replace(",", "").strip()
            merged["name"] = (ov.get("name") or row.get("name") or s)
            if ov.get("change_pct") is not None:
                merged["change_pct"] = str(ov["change_pct"])
            if ov.get("change_amt") is not None:
                merged["change_amt"] = str(ov["change_amt"])
            if ov.get("dir"):
                merged["dir"] = ov["dir"]
            out[s] = merged
            continue
        # even without manual_price, a name override should win — but if the
        # row has NO price, still backfill (don't skip it with an empty row)
        if ov.get("name") and row and row.get("price"):
            merged = dict(row)
            merged["name"] = ov["name"]
            out[s] = merged
            continue
        if not row.get("price"):
            need_backfill.append(s)
        else:
            out[s] = row
    if need_backfill:
        # tgju.org rate-limits parallel profile hits (429/timeouts under 6
        # concurrent) — throttle to 3 workers with a tiny stagger so the
        # first burst doesn't trip the WAF. Verified 2026-08-16: serial
        # fetches always succeed; 6-parallel intermittently fails.
        with ThreadPoolExecutor(max_workers=3) as ex:
            results = list(ex.map(_backfill_one, need_backfill,
                                  [all_rows.get(s, {}).get("name", "") for s in need_backfill]))
        for s, r in zip(need_backfill, results):
            ov = overrides.get(s) or {}
            if r.get("price"):
                # name override wins even for backfilled rows (the profile
                # page may name the slug differently or poorly)
                if ov.get("name"):
                    r = dict(r)
                    r["name"] = ov["name"]
                out[s] = r
            elif ov.get("manual_price"):
                # override applied post-backfill (backfill failed but manual wins)
                out[s] = {"name": ov.get("name") or all_rows.get(s, {}).get("name") or s,
                          "price": str(ov["manual_price"]).replace(",", "").strip(),
                          "change_pct": str(ov.get("change_pct") or ""),
                          "change_amt": str(ov.get("change_amt") or ""),
                          "dir": ov.get("dir") or ""}
            else:
                name = ov.get("name") or all_rows.get(s, {}).get("name") or s
                out[s] = {"name": name, "price": "", "change_pct": "",
                          "change_amt": "", "dir": ""}
    return out


def build_for_channel(channel: dict, all_rows: dict | None = None,
                      stale: bool = False, stale_age_hours: float = 0.0) -> str:
    if all_rows is None:
        all_rows = get_all_prices()
    rows = get_channel_rows(channel, all_rows)
    news = ""
    if channel.get("with_analysis", True):
        news = analysis_line(channel.get("id", "ch"),
                             channel.get("news_categories", []),
                             channel.get("analysis_tags", []))
    gmap = slug_group_map(channel)
    msg = build_message(channel, rows, gmap, news,
                        stale=stale, stale_age_hours=stale_age_hours)
    # If fallback data was used, append the visible stale notice at the end
    # (after footer) so channel members never mistake old numbers for fresh.
    if stale:
        try:
            from tgju_engine_format import stale_notice, SEP
            notice = stale_notice(rows, stale_age_hours)
            if notice:
                msg = msg.rstrip() + "\n" + SEP + "\n" + notice
        except Exception:
            pass
    return msg


def post_channel(channel: dict, send_msg, all_rows: dict | None = None) -> str:
    """Build + send via callable `send_msg(text)`; returns the posted text."""
    msg = build_for_channel(channel, all_rows)
    if not channel.get("telegram_id"):
        raise ValueError("channel %s has no telegram_id" % channel.get("id"))
    send_msg(msg)
    log_line("posted %s (%d chars)" % (channel.get("id"), len(msg)))
    return msg