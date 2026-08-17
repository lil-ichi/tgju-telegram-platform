# -*- coding: utf-8 -*-
"""TGJU Telegram Platform — webapp (port 8791).

Persian RTL dashboard to manage channels, bot behavior, posts,
AI providers, and the async scheduler.

Non-blocking design:
- /api/status serves from the in-memory cache instantly; it NEVER touches
  the network. A background refresher task (started at startup) re-fetches
  tgju.org every `fetch_ttl_seconds` (settings.json, default 60) and fills
  RUNTIME["last_rows"]/["last_fetch"].
- /api/refresh POST only *schedules* a background refresh and returns
  immediately — the dashboard never waits on tgju.org.
"""
import asyncio
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from tgju_engine_config import (  # noqa: E402
    load_channels, save_channels, channel_state_path, load_channel_state,
    save_channel_state, log_line, LOG_PATH,
    load_slug_overrides, save_slug_overrides, rename_slug)
from tgju_engine_scrape import get_all_prices, fetch_html  # noqa: E402
from tgju_engine_orchestrator import build_for_channel  # noqa: E402
from tgju_engine_ai import (  # noqa: E402
    load_ai_config, save_ai_config, test_provider, route_category,
    auto_build_routing, run_analysis, load_ai_jobs, save_ai_jobs,
    record_ai_activity, _channel_domain)
from tgju_engine_format import SEP as _FORMAT_SEP, FA_WEEKDAYS, fa_num  # noqa: E402


def _esc(s) -> str:
    """Escape channel-supplied text for Telegram's HTML parser."""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

app = FastAPI(title="TGJU Telegram Platform", version="1.1.0")

# persistent runtime state
RUNTIME = {"channels": None, "last_rows": {}, "last_fetch": None,
           "last_fetch_duration": None, "refreshing": False,
           "last_preview": {}, "scheduler": None, "scheduler_running": False,
           "refresh_lock": threading.Lock()}

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
    from datetime import datetime, timedelta
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


# ── Telegram send helper ──────────────────────────────────────────────────
def get_bot_token() -> str:
    """Get the active Telegram bot token. Priority: active bot profile in
    state/bot_profile.json → legacy .env TELEGRAM_BOT_TOKEN → ''."""
    try:
        from tgju_engine_bot import get_active_token
        tok = get_active_token()
        if tok:
            return tok
    except Exception:
        pass
    # Legacy fallback: standard Hermes .env location (resolved per-user)
    env_path = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env")
    try:
        env = open(env_path, encoding="utf-8").read()
    except Exception:
        return ""
    # line-anchored: `^TELEGRAM_BOT_TOKEN=...` only — a commented example
    # (`# TELEGRAM_BOT_TOKEN=...`) must NOT match.
    m = re.search(r"^TELEGRAM_BOT_TOKEN\s*=\s*(\S+)", env, re.M)
    return m.group(1).strip() if m else ""


def _tg_api_call(method: str, payload: dict, timeout: int = 30) -> dict:
    """POST JSON to api.telegram.org/bot<token>/<method>.

    Raises urllib.error.HTTPError / OSError / ValueError on transport or
    API errors; the API error detail is in the returned dict when HTTP 200.
    """
    import urllib.error
    import urllib.request
    token = get_bot_token()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found")
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def send_telegram(chat_id: str, text: str) -> dict:
    token = get_bot_token()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not found"}
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    timeout = max(5, int(load_settings().get("telegram_timeout_seconds", 30)))
    retries = max(0, int(load_settings().get("telegram_retry_count", 2)))
    last_err = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode())
            if resp.get("ok"):
                return resp
            last_err = str(resp.get("description") or resp)
        except Exception as e:
            last_err = str(e)
    return {"ok": False, "error": last_err}


def send_telegram_poll(chat_id: str, question: str, options: list) -> dict:
    """Native Telegram poll (sendPoll). is_anonymous=true is MANDATORY for
    channel chats (API rejects non-anonymous polls in channels)."""
    token = get_bot_token()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not found"}
    url = "https://api.telegram.org/bot%s/sendPoll" % token
    anon = bool(load_settings().get("poll_anonymous", True))
    body = {"chat_id": chat_id, "question": question, "options": options,
            "poll_type": "regular", "is_anonymous": anon}
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    timeout = max(5, int(load_settings().get("telegram_timeout_seconds", 30)))
    retries = max(0, int(load_settings().get("telegram_retry_count", 2)))
    last_err = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode())
            if resp.get("ok"):
                return resp
            last_err = str(resp.get("description") or resp)
        except Exception as e:
            last_err = str(e)
    return {"ok": False, "error": last_err}


# ── Poll pool (24 approved engagement questions, formal Persian) ──────────
# Source: tgju-telegram-price-publisher skill reference (2026-08-09).
# Safety: engagement/opinion only — never signals, news speculation or
# anything that puts TGJU at risk. Questions use formal plural «شما».
POLL_POOL = [
    {"question": "به نظر شما کدام بازار در هفته پیشِ رو بیشترین نوسان را تجربه خواهد کرد؟ 📊",
     "options": ["طلا و سکه", "دلار", "بیت‌کوین", "تتر"]},
    {"question": "اگر قصد سرمایه‌گذاری در یک دارایی را داشتید، کدام را انتخاب می‌کردید؟ 🏦",
     "options": ["طلا", "دلار", "بیت‌کوین", "تتر"]},
    {"question": "مناسب‌ترین زمان ورود به بازار طلا از نظر شما چه زمانی است؟ ⏰",
     "options": ["زمان کنونی", "پس از کاهش قیمت", "در بازار باثبات", "مطلع نیستم"]},
    {"question": "هر چند وقت یک‌بار تمایل دارید قیمت‌های لحظه‌ای را مشاهده کنید؟ 🕒",
     "options": ["هر ۳۰ دقیقه", "هر یک ساعت", "روزانه ۲ تا ۳ بار"]},
    {"question": "کدام بخش از محتوای کانال برای شما مفیدتر است؟ 💬",
     "options": ["جدول قیمت‌ها", "تحلیل بازار", "هر دو", "نظرسنجی‌ها"]},
    {"question": "به نظر شما محور تحلیل بعدی کدام بازار باشد؟ 🔮",
     "options": ["طلا", "دلار", "بیت‌کوین"]},
    {"question": "قیمت‌ها را معمولاً با کدام وسیله دنبال می‌کنید؟ 📱",
     "options": ["تلفن همراه", "رایانه", "تبلت", "همه موارد"]},
    {"question": "نخستین موضوعی که در آغاز روز بررسی می‌کنید چیست؟ ☀️",
     "options": ["قیمت دلار", "قیمت طلا", "بیت‌کوین", "اخبار بازار"]},
    {"question": "سطح آشنایی شما با تحلیل تکنیکال چگونه است؟ 📈",
     "options": ["حرفه‌ای", "متوسط", "مبتدی", "آشنایی ندارم"]},
    {"question": "کدام واحد پولی برای شما آشناتر است؟ 💰",
     "options": ["تومان", "ریال", "دلار", "هر سه"]},
    {"question": "هدف اصلی شما از دنبال کردن قیمت‌ها چیست؟ 🎯",
     "options": ["سرمایه‌گذاری بلندمدت", "معامله کوتاه‌مدت", "اطلاع از وضعیت بازار", "خرید و فروش روزانه"]},
    {"question": "دوست دارید در تحلیل‌های بازار چه موضوعی بیشتر پوشش داده شود؟ 📊",
     "options": ["تحلیل طلا و سکه", "تحلیل دلار", "تحلیل ارزهای دیجیتال", "تحلیل بازار جهانی"]},
    {"question": "به‌طور میانگین چه مدت زمانی را صرف بررسی بازارها می‌کنید؟ ⏳",
     "options": ["کمتر از یک ساعت", "یک تا دو ساعت", "بیش از دو ساعت"]},
    {"question": "برای دریافت قیمت‌ها تلگرام را ترجیح می‌دهید یا اینستاگرام؟ 📲",
     "options": ["تلگرام", "اینستاگرام", "هر دو"]},
    {"question": "آیا تاکنون با طلای آب‌شده معامله کرده‌اید؟ 🔥",
     "options": ["بله", "خیر", "در نظر دارم"]},
    {"question": "آیا تتر را گزینه‌ای مناسب برای نگهداری دارایی می‌دانید؟ 🪙",
     "options": ["بله", "خیر", "اطلاعی ندارم"]},
    {"question": "کدام نوع سکه بیشتر مورد توجه شماست؟ 🟡",
     "options": ["سکه امامی", "سکه بهار آزادی", "نیم‌سکه", "ربع‌سکه"]},
    {"question": "اگر بازار با افت قابل توجهی مواجه شود، واکنش شما چیست؟ 🧐",
     "options": ["اقدام به خرید می‌کنم", "اقدام به فروش می‌کنم", "منتظر می‌مانم", "برنامه مشخصی ندارم"]},
    {"question": "تحلیل‌های بازار را با چه لحنی ترجیح می‌دهید؟ 🗣️",
     "options": ["زبان ساده و روان", "لحن تخصصی", "مختصر و سریع"]},
    {"question": "دیدگاه شما درباره محتوای قیمتی کانال چیست؟ ⭐",
     "options": ["رضایت‌بخش است", "مناسب است", "نیازمند بهبود است"]},
    {"question": "کدام بازار جهانی برای شما اهمیت بیشتری دارد؟ 🌍",
     "options": ["طلا", "نفت", "نقره", "ارزهای دیجیتال"]},
    {"question": "جایگاه بیت‌کوین در ده سال آینده را چگونه ارزیابی می‌کنید؟ 🧭",
     "options": ["رشد قابل توجه", "وضعیت مشابه کنونی", "کاهش اهمیت"]},
    {"question": "در روزهای تعطیلی بازار معمولاً چه می‌کنید؟ 📅",
     "options": ["استراحت", "پیگیری اخبار", "برنامه‌ریزی برای هفته آینده"]},
    {"question": "افزودن کدام اطلاعات به جدول قیمت‌ها مفید است؟ ➕",
     "options": ["حجم معاملات", "روند هفتگی", "نوسان روزانه", "وضعیت کنونی مناسب است"]},
]


# Track recently-used poll questions (avoid repeats across channels/slots)
_RECENT_POLLS = []  # list of questions, newest last
_RECENT_POLLS_MAX = 8

POLL_STORE_PATH = os.path.join(BASE, "state", "polls.json")


def load_poll_store() -> dict:
    """Load the poll store {questions: [...], fixed: {channel_id: [...]}}."""
    try:
        with open(POLL_STORE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            d = {}
        d.setdefault("questions", [])
        d.setdefault("fixed", {})
        return d
    except Exception:
        return {"questions": [], "fixed": {}}


def save_poll_store(store: dict):
    try:
        with open(POLL_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def get_poll_pool(channel: dict | None = None) -> list:
    """Return the active poll pool for a channel (fixed + shared).

    Fixed polls (per-channel) come first, then the shared pool. Falls back
    to the built-in POLL_POOL when the store is empty.
    """
    store = load_poll_store()
    pool = []
    if channel:
        cid = channel.get("id", "")
        fixed = (store.get("fixed") or {}).get(cid) or []
        pool.extend(fixed)
    pool.extend(store.get("questions") or [])
    if not pool:
        pool = POLL_POOL
    return pool


class _AIUnavailable(Exception):
    """Raised when the AI provider is not reachable for poll picking."""


def _poll_indexes(pool_len: int, exclude: list = None) -> list:
    """Shuffled candidate indexes (random order), optionally excluding recent."""
    import random
    exclude = set(exclude or [])
    idxs = [i for i in range(pool_len) if i not in exclude]
    if not idxs:
        idxs = list(range(pool_len))
    random.shuffle(idxs)
    return idxs


def pick_poll(channel: dict, ai_pick: bool = True) -> dict:
    """Pick the poll question for this send.

    Strategy (v3):
    1. If AI is enabled+configured for the channel, let the AI pick the most
       relevant question from the pool, given the current market table.
    2. Otherwise fall back to RANDOM selection (no repeats within the last
       8 sends) — no more fixed rotation.
    3. If the AI fails or times out, fall back to random as well.

    Returns {"question": ..., "options": [...], "source": "ai"|"random"}
    """
    from datetime import datetime
    pool = get_poll_pool(channel)

    # Which questions were used recently (by normalized text)
    recent_q = [p["question"] for p in _RECENT_POLLS]
    recent_idx = [i for i, p in enumerate(pool) if p["question"] in recent_q]

    # FIXED polls take absolute priority: when a channel has dedicated
    # polls (state/polls.json -> fixed[cid]), cycle ONLY through those
    # (no-repeat within the recent window). The shared pool is a fallback.
    store = load_poll_store()
    fixed = (store.get("fixed") or {}).get(channel.get("id", "")) or []
    if fixed:
        fixed_recent = [f["question"] for f in fixed
                        if f["question"] in recent_q]
        avail = [f for f in fixed if f["question"] not in recent_q]
        if not avail:
            avail = fixed  # all were recent -> allow reuse (still no-repeat back-to-back)
        import random as _rnd
        pick = _rnd.choice(avail)
        _RECENT_POLLS.append(pick)
        if len(_RECENT_POLLS) > _RECENT_POLLS_MAX:
            _RECENT_POLLS.pop(0)
        return {"question": pick["question"], "options": list(pick["options"]),
                "source": "fixed"}

    # Try AI pick first
    if ai_pick and channel.get("id"):
        try:
            from tgju_engine_ai import load_ai_config
            cfg = load_ai_config()
            ch_cfg = (cfg.get("channels") or {}).get(channel["id"]) or {}
            analysis = ch_cfg.get("analysis") or {}
            if analysis.get("enabled", False):
                prov_name = analysis.get("provider") or ch_cfg.get("provider") or ""
                prov = (cfg.get("providers") or {}).get(prov_name)
                if prov and prov.get("kind") != "mock" and prov.get("base_url"):
                    # Local models (hermes-2 on :20128) are SLOW (10-17s).
                    # Give the AI a generous budget on real posts — this runs
                    # inside a worker thread, not the HTTP handler, so a
                    # 25s chat timeout is acceptable. Falls back to random
                    # when the model is unreachable or times out.
                    import urllib.request as _ur
                    import time as _time
                    _ai_deadline = _time.time() + 25.0  # hard budget for AI pick
                    base = (prov.get("base_url") or "").rstrip("/")
                    probe_url = base + "/models"
                    try:
                        _ur.urlopen(_ur.Request(probe_url), timeout=2.0).read(64)
                    except Exception:
                        raise _AIUnavailable()
                    from tgju_engine_ai import _chat_completion
                    # Build a compact market snapshot for the prompt
                    from tgju_engine_format import slug_unit, fmt_price, direction_arrow
                    rows = cached_rows()
                    wanted = list(channel.get("slugs") or [])
                    for slugs in (channel.get("slug_groups") or {}).values():
                        wanted.extend(slugs)
                    table_lines = []
                    for s in dict.fromkeys(wanted):
                        row = (rows or {}).get(s) or {}
                        if not row.get("price"):
                            continue
                        table_lines.append("%s | %s %s | %s%%" % (
                            row.get("name") or s,
                            fmt_price(s, row["price"], slug_unit(s, channel)),
                            slug_unit(s, channel),
                            row.get("change_pct") or "—"))
                    table = "\n".join(table_lines) or "(داده‌ای در دسترس نیست)"
                    pool_txt = "\n".join(
                        "%d) %s [گزینه‌ها: %s]" % (
                            i + 1, p["question"], " / ".join(p["options"]))
                        for i, p in enumerate(pool))
                    recent_txt = "، ".join(recent_q[:4]) or "هیچ"
                    prompt = (
                        "شما مدیر محتوای کانال تلگرام بازار طلا و ارز هستید.\n"
                        "وضعیت فعلی بازار:\n%s\n\n"
                        "سؤالات نظرسنجی موجود:\n%s\n\n"
                        "سؤالات اخیر (تکرار نکن): %s\n\n"
                        "فقط شماره یک سؤال را که بیشترین ارتباط را با وضعیت بازار دارد "
                        "انتخاب کن (عدد، بدون توضیح)." % (table, pool_txt, recent_txt))
                    prov_cfg = dict(prov)
                    prov_cfg["model"] = (analysis.get("model")
                                         or ch_cfg.get("model") or prov.get("model") or "")
                    job = None
                    try:
                        from tgju_engine_ai import resolve_job
                        job = resolve_job(cfg, "poll_select")
                    except Exception:
                        pass
                    _job_tokens = int((job or {}).get("max_tokens") or 500)
                    _job_timeout = int((job or {}).get("timeout_s") or 25)
                    if _time.time() > _ai_deadline:
                        raise _AIUnavailable()
                    # max_tokens must be generous: reasoning models spend
                    # tokens on reasoning_content before the actual answer.
                    answer = _chat_completion(prov_cfg, prompt,
                                              max_tokens=_job_tokens,
                                              timeout=_job_timeout)
                    # Accept Persian digits too (۰-۹)
                    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
                    m = re.search(r"\d+", answer or "") or re.search(
                        r"[۰-۹]+", answer or "")
                    if m:
                        txt = m.group(0)
                        for fd, ad in zip(fa_digits, "0123456789"):
                            txt = txt.replace(fd, ad)
                        idx = int(txt) - 1
                        if 0 <= idx < len(pool):
                            poll = pool[idx]
                            if poll["question"] not in recent_q:  # respect no-repeat
                                _RECENT_POLLS.append(poll)
                                if len(_RECENT_POLLS) > _RECENT_POLLS_MAX:
                                    _RECENT_POLLS.pop(0)
                                try:
                                    record_ai_activity({
                                        "job": "poll_select", "channel": channel["id"],
                                        "status": "ok", "provider": prov_name,
                                        "model": prov_cfg.get("model"),
                                        "question": poll["question"][:60]})
                                except Exception:
                                    pass
                                return {"question": poll["question"],
                                        "options": list(poll["options"]),
                                        "source": "ai"}
        except Exception:
            pass  # fall through to random

    # Random fallback (no repeats of the last 8)
    candidates = _poll_indexes(len(pool), recent_idx)
    idx = candidates[0] if candidates else 0
    poll = pool[idx]
    _RECENT_POLLS.append(poll)
    if len(_RECENT_POLLS) > _RECENT_POLLS_MAX:
        _RECENT_POLLS.pop(0)
    return {"question": poll["question"], "options": list(poll["options"]),
            "source": "random"}


# ── API: dashboard data ───────────────────────────────────────────────────
@app.get("/api/types")
def api_types():
    return {"types": [
        {"id": "prices", "label": "جدول قیمت", "icon": "📊", "on": True},
        {"id": "news", "label": "خبر روز", "icon": "📰", "on": True},
        {"id": "poll", "label": "نظرسنجی", "icon": "🗳", "on": True},
        {"id": "analysis", "label": "تحلیل بازار", "icon": "📈", "on": True},
    ], "channel_types": {
        c["id"]: c.get("post_types", ["prices"])
        for c in get_channels()}}


@app.put("/api/types/{cid}")
async def api_types_set(cid: str, req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad body"}, status_code=400)
    ch = get_channel(cid)
    if not ch:
        return JSONResponse({"error": "unknown channel"}, status_code=404)
    ch["post_types"] = body.get("post_types", ["prices"])
    save_channels(get_channels())
    reload_channels()
    return {"ok": True}


@app.get("/api/connections")
def api_connections():
    """Connection overview = live Bot API probe data (60s cache).

    Returns the probe shape the UI expects: per-channel probe_status,
    probe_detail, title, member_count, can_post/can_edit, last_probe.
    """
    if not CONN_PROBE_CACHE["entry"] or \
            time.time() - CONN_PROBE_CACHE["entry"] >= 60:
        _build_connection_probe()
    data = CONN_PROBE_CACHE["data"] or {}
    bot = data.get("bot") or {}
    channels = []
    for item in (data.get("channels") or []):
        channels.append({
            "id": item.get("id"), "name": item.get("name"),
            "telegram_id": item.get("telegram_id", ""),
            "connected": bool(item.get("telegram_id")),
            "enabled": item.get("enabled", True),
            "probe_status": item.get("status", "not_set"),
            "probe_detail": item.get("error", ""),
            "title": item.get("title", ""),
            "member_count": item.get("member_count"),
            "can_post": item.get("status") == "ok_admin",
            "can_edit": item.get("status") == "ok_admin",
            "last_probe": data.get("probed_at", ""),
        })
    return {"bot_token_set": data.get("bot_token_set", False),
            "bot_username": (bot.get("username") or "") if bot.get("ok") else "",
            "channels": channels,
            "probed_at": data.get("probed_at", ""),
            "note": "وضعیت واقعی از Bot API (کش ۶۰ ثانیه)"}


def _probe_bot_info():
    """getMe — live Bot API probe (60s cache)."""
    try:
        data = _tg_api_call("getMe", {}, timeout=12)
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    if not data.get("ok"):
        return {"ok": False, "error": (data.get("description") or "getMe failed")[:120]}
    u = data.get("result") or {}
    return {"ok": True, "username": u.get("username", ""),
            "bot_id": u.get("id"), "name": u.get("first_name", "")}


def _probe_channel(bot_id: int, chat_id: str) -> dict:
    """getChat + getChatMember(bot) — live Bot API probes for one channel."""
    try:
        chat = _tg_api_call("getChat", {"chat_id": chat_id}, timeout=12)
    except Exception as e:
        return {"status": "error", "error": str(e)[:120]}
    if not chat.get("ok"):
        desc = (chat.get("description") or "getChat failed")[:120]
        if "chat not found" in desc.lower():
            return {"status": "bot_missing", "error": desc}
        return {"status": "error", "error": desc}
    info = chat.get("result") or {}
    out = {"title": info.get("title") or info.get("username") or chat_id,
           "member_count": info.get("member_count"),
           "linked_chat": (info.get("linked_chat_id") or
                           (info.get("linked_chat") or {}).get("id"))}
    try:
        mem = _tg_api_call("getChatMember",
                           {"chat_id": chat_id, "user_id": bot_id}, timeout=12)
    except Exception as e:
        out["status"] = "error"
        out["error"] = str(e)[:120]
        return out
    if not mem.get("ok"):
        desc = (mem.get("description") or "getChatMember failed")[:120]
        out["status"] = "error"
        out["error"] = desc
        return out
    m = mem.get("result") or {}
    if m.get("status") != "administrator":
        out["status"] = "not_admin"
        return out
    rights = m.get("can_post_messages", False) and m.get("can_edit_messages", False)
    out["status"] = "ok_admin" if rights else "not_admin"
    if not rights:
        out["error"] = "ادمین است ولی اجازه ارسال/ویرایش ندارد"
    return out


@app.get("/api/connections/probe")
def api_connections_probe():
    """LIVE Bot API probes with a 60s cache.

    status ∈ {not_set, ok_admin, not_admin, bot_missing, error:<msg>}.
    """
    if not CONN_PROBE_CACHE["entry"] or \
            time.time() - CONN_PROBE_CACHE["entry"] >= 60:
        _build_connection_probe()
    return CONN_PROBE_CACHE["data"]


CONN_PROBE_CACHE = {"entry": 0.0, "data": None}


def _build_connection_probe():
    bot = _probe_bot_info()
    bot_id = bot.get("bot_id")
    chans = []
    for c in get_channels():
        cid = c.get("telegram_id") or ""
        item = {"id": c["id"], "name": c.get("name"),
                "telegram_id": cid, "enabled": c.get("enabled", True),
                "status": "not_set"}
        if cid:
            if not bot.get("ok"):
                item["status"] = "error"
                item["error"] = bot.get("error") or "getMe failed"
            else:
                item.update(_probe_channel(bot_id, cid))
        chans.append(item)
    CONN_PROBE_CACHE["data"] = {
        "probed_at": str(datetime.now().isoformat(timespec="seconds")),
        "bot_token_set": bool(get_bot_token()),
        "bot": bot,
        "channels": chans,
        "note": "وضعیت واقعی از Bot API (کش ۶۰ ثانیه)"}
    CONN_PROBE_CACHE["entry"] = time.time()


@app.get("/api/status")
def api_status():
    # serve instantly from cache; NEVER block the dashboard on a network fetch
    rows = RUNTIME["last_rows"] or {}
    now = datetime.now()
    chans = []
    for c in get_channels():
        st = load_channel_state(c["id"])
        chans.append({
            "id": c["id"], "name": c["name"], "telegram_id": c.get("telegram_id", ""),
            "enabled": c.get("enabled", True), "icon": c.get("icon", ""),
            "header": c.get("header", ""), "section_title": c.get("section_title", ""),
            "schedule_minutes": c.get("schedule_minutes", 10),
            "poll_enabled": c.get("poll_enabled", False),
            "with_analysis": c.get("with_analysis", True),
            "with_star": c.get("with_star", True),
            "with_footer": c.get("with_footer", True),
            "format": c.get("format", "chips"),
            "category": c.get("category", ""),
            "news_categories": c.get("news_categories", []),
            "slug_count": sum(len(v) for v in (c.get("slug_groups") or {}).values())
                          + len(c.get("slugs") or []),
            "used_today": len(st.get("used", [])),
            "last_post": st.get("last_post_at", ""),
            "last_error": st.get("last_error", ""),
        })
    age_sec = None
    if RUNTIME["last_fetch"]:
        age_sec = int((now - RUNTIME["last_fetch"]).total_seconds())
    # Fallback/degraded status: are we serving stale disk data?
    degraded = bool(RUNTIME.get("degraded"))
    fallback_age = None
    try:
        from tgju_engine_fallback import fallback_prices_age_seconds
        fallback_age = fallback_prices_age_seconds()
    except Exception:
        pass
    # If no fresh fetch for a long time but fallback exists -> degraded
    if not degraded and age_sec and age_sec > 600 and fallback_age is not None:
        degraded = True
    return {"channels": chans, "rows": len(rows),
            "last_fetch": str(RUNTIME["last_fetch"]) if RUNTIME["last_fetch"] else None,
            "fetch_age_seconds": age_sec,
            "fetch_duration_ms": (RUNTIME.get("last_fetch_duration") or 0) * 1000,
            "fetch_duration_seconds": RUNTIME.get("last_fetch_duration"),
            "refreshing": RUNTIME.get("refreshing", False),
            "degraded": degraded,
            "fallback_age_seconds": fallback_age,
            "auto_post": load_settings().get("auto_post", True)}


@app.get("/api/channels")
def api_channels():
    chans = get_channels()
    # Expose the real default template so the UI can pre-fill the editor
    # (a channel with template="" still shows what will actually post).
    from tgju_engine_format import TEMPLATE_DEFAULT
    out = []
    for c in chans:
        cc = dict(c)
        cc["template_default"] = TEMPLATE_DEFAULT
        out.append(cc)
    return {"channels": out}


@app.get("/api/templates")
def api_templates():
    """Category templates derived from existing channels.

    Lets the UI offer 'add a channel for this category' which copies the
    verified slug pools, news categories and analysis tags — so a new channel
    immediately fetches the relevant prices, builds its table and posts.
    """
    tpls = []
    for c in get_channels():
        if not c.get("slug_groups") and not c.get("news_categories"):
            continue
        tpls.append({
            "id": c["id"],
            "name": c.get("name"),
            "icon": c.get("icon", "📢"),
            "header": c.get("header", c.get("name", "")),
            "section_title": c.get("section_title", "قیمت‌ها"),
            "slug_groups": c.get("slug_groups", {}),
            "slug_count": sum(len(v) for v in (c.get("slug_groups") or {}).values()),
            "news_categories": c.get("news_categories", []),
            "analysis_tags": c.get("analysis_tags", []),
            "schedule_minutes": c.get("schedule_minutes", 10),
            "footer": c.get("footer", ""),
            "template": c.get("template", ""),
        })
    return {"templates": tpls}


# ── Categories ────────────────────────────────────────────────────────────
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


@app.get("/api/categories")
def api_categories():
    extra = load_categories()
    merged = {}
    for name, info in extra.items():
        merged[name] = {"icon": info.get("icon", "🗂") if isinstance(info, dict) else "🗂"}
    for c in get_channels():
        merged.setdefault(channel_category(c), {"icon": "🗂"})
    result = []
    for name, info in merged.items():
        chans = [c for c in get_channels() if channel_category(c) == name]
        result.append({"name": name, "icon": info["icon"],
                       "count": len(chans), "channels": [c["id"] for c in chans]})
    result.sort(key=lambda x: -x["count"])
    return {"categories": result}


@app.post("/api/categories")
async def api_categories_add(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "نام دسته خالی است"}, status_code=400)
    cats = load_categories()
    cats[name] = {"icon": body.get("icon") or "🗂"}
    save_categories(cats)
    return {"ok": True}


@app.delete("/api/categories/{name}")
def api_categories_del(name: str):
    cats = load_categories()
    if name in cats:
        del cats[name]
        save_categories(cats)
    return {"ok": True}


# ── Slug data & manual overrides (دادهها و لینکها) ────────────────────────
@app.get("/api/slugs")
def api_slugs():
    """Inventory of every slug used by any channel + current data status.

    rows = live homepage cache; overrides = manual config; aliases =
    slug->working-slug map for slugs that 404 on their own profile path.
    """
    chans = get_channels()
    used = {}
    for c in chans:
        for s in list(c.get("slugs") or []):
            used.setdefault(s, []).append(c["id"])
        for slugs in (c.get("slug_groups") or {}).values():
            for s in slugs:
                used.setdefault(s, []).append(c["id"])
    rows = cached_rows()
    from tgju_engine_scrape import SLUG_ALIASES
    overrides = load_slug_overrides()
    out = []
    for s in sorted(used):
        row = rows.get(s) or {}
        ov = overrides.get(s) or {}
        out.append({
            "slug": s, "name": ov.get("name") or row.get("name") or s,
            "channels": sorted(set(used[s])),
            "homepage_price": row.get("price") or "",
            "change_pct": row.get("change_pct") or "",
            "dir": row.get("dir") or "",
            "manual": bool(ov.get("manual_price")),
            "manual_price": ov.get("manual_price") or "",
            "override": ov,
            "unit": ov.get("unit", ""),   # manual unit override (auto if empty)
            "profile_url": (ov.get("profile_url") or ""),
            "alias": SLUG_ALIASES.get(s, ""),
        })
    return {"slugs": out, "overrides": overrides}


@app.put("/api/slugs/{slug}")
async def api_slug_override(slug: str, req: Request):
    """Create/replace a manual override for one slug.

    Body fields (all optional): name, profile_url, manual_price,
    change_pct, change_amt, dir (high|low). Empty body removes the override.
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    overrides = load_slug_overrides()
    cleaned = {}
    for k in ("name", "profile_url", "manual_price", "change_pct",
              "change_amt", "dir", "unit"):
        v = body.get(k)
        if v is not None and str(v).strip() != "":
            cleaned[k] = str(v).strip()
    if cleaned:
        overrides[slug] = cleaned
    else:
        overrides.pop(slug, None)
    save_slug_overrides(overrides)
    log_line("slug override saved: %s %s" % (slug, cleaned))
    return {"ok": True, "slug": slug, "override": overrides.get(slug, {})}


@app.delete("/api/slugs/{slug}")
def api_slug_override_del(slug: str):
    overrides = load_slug_overrides()
    if slug in overrides:
        del overrides[slug]
        save_slug_overrides(overrides)
    return {"ok": True}


@app.post("/api/slugs/rename")
async def api_slug_rename(req: Request):
    """Rename a slug key everywhere (channels.yaml, overrides, cache)."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    old = (body.get("old") or "").strip()
    new = (body.get("new") or "").strip()
    res = rename_slug(old, new)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    reload_channels()  # pick up channels.yaml changes in the running server
    log_line("slug renamed: %s -> %s (channels: %s)" % (
        old, new, ",".join(res.get("updated") or [])))
    return res


@app.post("/api/slugs/test")
async def api_slug_test(req: Request):
    """Test-fetch a profile URL for a slug (returns the parsed row)."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    slug = (body.get("slug") or "").strip()
    url = (body.get("profile_url") or "").strip()
    if not slug:
        return JSONResponse({"ok": False, "error": "slug required"}, status_code=400)
    from tgju_engine_scrape import fetch_profile_price
    import urllib.parse
    if url and "://" not in url:
        url = "https://" + url.lstrip("/")
    row = {}
    if url:
        try:
            # custom URL: reuse the profile fetch logic via a temp call
            from tgju_engine_scrape import fetch_html
            html = fetch_html(url)
            import re as _re
            m = _re.search(r'data-col="info\.last_trade\.PDrCotVal">\s*([\d,\.]+)\s*<', html)
            if m:
                row = {"price": m.group(1).replace(",", ""), "name": slug}
            else:
                row = {"error": "no PDrCotVal found on that page"}
        except Exception as e:
            row = {"error": str(e)[:150]}
    else:
        row = fetch_profile_price(slug)
    return {"ok": bool(row.get("price")), "slug": slug, "row": row}


@app.get("/api/preview/{cid}")
async def api_preview(cid: str, type: str = "prices", template: str = ""):
    ch = get_channel(cid)
    if not ch:
        return JSONResponse({"error": "unknown channel"}, status_code=404)
    # Draft template override: preview as-if this template were saved
    # (used by the template editor in the preview tab — no persist).
    if template:
        ch = dict(ch)
        ch["template"] = template
    post_type = type or "prices"
    if post_type not in ("prices", "news", "poll", "analysis", "all"):
        return JSONResponse({"error": "unknown post_type: %s" % post_type},
                            status_code=400)
    stale, age_h = _stale_info()
    if post_type == "poll":
        p = pick_poll(ch, ai_pick=False)  # preview: instant, no AI
        return {"type": "poll", "question": p["question"], "options": p["options"],
                "chars": len(p["question"]) + sum(len(o) for o in p["options"])}
    if post_type == "all":
        p = pick_poll(ch, ai_pick=False)  # preview: instant
        rows = cached_rows()
        try:
            txt = build_post_text(ch, "prices", rows, stale=stale, stale_age_hours=age_h)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"type": "all", "preview": txt, "chars": len(txt),
                "poll": {"question": p["question"], "options": p["options"]}}
    rows = cached_rows()
    try:
        if post_type == "prices":
            msg = await asyncio.to_thread(build_for_channel, ch, rows,
                                          stale=stale, stale_age_hours=age_h)
        else:
            msg = await asyncio.to_thread(build_post_text, ch, post_type, rows,
                                          stale=stale, stale_age_hours=age_h)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    RUNTIME["last_preview"][cid] = msg
    return {"type": post_type, "preview": msg, "chars": len(msg)}


def build_post_text(channel: dict, post_type: str, rows: dict,
                    stale: bool = False, stale_age_hours: float = 0.0) -> str:
    """Build the message text for a post_type (prices|news|analysis).

    'prices'  -> full price digest (header + chips + TGJU analysis line + footer)
    'news'    -> TGJU analysis line with a header (TGJU's own words only)
    'analysis'-> optional AI-generated text (only when enabled+configured)
    `stale` / `stale_age_hours`: appends a visible notice when the data
    came from the fallback cache (tgju.org unreachable).
    """
    if post_type == "news":
        from tgju_engine_news import analysis_line as tgju_analysis_line
        try:
            line = tgju_analysis_line(channel.get("id", "ch"),
                                      channel.get("news_categories", []),
                                      channel.get("analysis_tags", []))
        except Exception as e:
            log_line("news build error %s: %s" % (channel.get("id"), e))
            line = ""
        if not line:
            return ""
        icon = channel.get("icon") or ""
        header_txt = channel.get("header") or channel.get("name", "TGJU بازار")
        wd = FA_WEEKDAYS[datetime.now().weekday()]
        hm = fa_num(datetime.now().strftime("%H:%M"))
        lines = ["%s %s | %s %s" % (_esc(icon), _esc(header_txt), wd, hm), _FORMAT_SEP, line]
        if channel.get("with_footer", True):
            lines.append(_esc(channel.get("footer") or "منبع: tgju.org"))
        return _append_stale_notice("\n".join(lines), stale, stale_age_hours)
    if post_type == "analysis":
        cfg = load_ai_config()
        res = run_analysis(cfg, channel, rows)
        if not res.get("ok"):
            raise ValueError(res.get("error") or "AI analysis failed")
        # Use template from functions config, with channel-aware placeholders
        template = ""
        try:
            import tgju_engine_functions as fn_mod
            fns = fn_mod.load_functions()
            template = (fns.get("analysis") or {}).get("template", "")
        except Exception:
            pass
        wd = FA_WEEKDAYS[datetime.now().weekday()]
        hm = fa_num(datetime.now().strftime("%H:%M"))
        header = template.format(
            name=channel.get("name", channel.get("id")),
            domain=_channel_domain(channel),
            weekday=wd, time=hm
        ) if template else ("📈 تحلیل بازار %s | %s %s" % (
            channel.get("name", ""), wd, hm))
        lines = [header, _FORMAT_SEP,
                 res["text"]]
        if channel.get("with_footer", True):
            lines.append(_esc(channel.get("footer") or "منبع: tgju.org"))
        return _append_stale_notice("\n".join(lines), stale, stale_age_hours)
    # default: prices
    return build_for_channel(channel, rows, stale=stale, stale_age_hours=stale_age_hours)


def _append_stale_notice(text: str, stale: bool, stale_age_hours: float) -> str:
    """Append a visible Persian notice when data is old (TGJU down)."""
    if not stale or not text:
        return text
    try:
        from tgju_engine_format import stale_notice, SEP as _S
        notice = stale_notice({}, stale_age_hours)
        if notice:
            return text.rstrip() + "\n" + _S + "\n" + notice
    except Exception:
        pass
    return text


def _stale_info():
    """Return (stale_bool, age_hours) from runtime + fallback disk stats."""
    stale = bool(RUNTIME.get("degraded"))
    age_h = 0.0
    if stale:
        try:
            from tgju_engine_fallback import fallback_prices_age_seconds
            fb_s = fallback_prices_age_seconds()
            if fb_s is not None:
                age_h = fb_s / 3600.0
            else:
                age_h = 1.0
        except Exception:
            age_h = 1.0
    return stale, age_h


def post_channel_type(channel: dict, post_type: str, rows: dict) -> dict:
    """Send one post to the channel. post_type ∈ {prices, news, poll,
    analysis, all}. Returns {ok, ...} — never raises."""
    target = channel.get("telegram_id")
    if not target:
        return {"ok": False, "error": "no telegram_id set for channel"}
    stale, age_h = _stale_info()
    if post_type == "poll":
        p = pick_poll(channel)
        return send_telegram_poll(target, p["question"], p["options"])
    if post_type == "all":
        rows = rows or cached_rows()
        text = build_post_text(channel, "prices", rows, stale=stale, stale_age_hours=age_h)
        resp = {"ok": True}
        if text:
            resp = send_telegram(target, text)
            if not resp.get("ok"):
                return resp
        p = pick_poll(channel, ai_pick=True)  # real post: AI decides
        rp = send_telegram_poll(target, p["question"], p["options"])
        if not rp.get("ok"):
            return rp
        return {"ok": True, "message_id": resp.get("result", {}).get("message_id"),
                "poll_message_id": rp.get("result", {}).get("message_id")}
    text = build_post_text(channel, post_type, rows, stale=stale, stale_age_hours=age_h)
    if not text:
        return {"ok": False, "error": "post_type %s produced empty text" % post_type}
    return send_telegram(target, text)


@app.post("/api/channels")
async def api_create_channel(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    chans = get_channels()
    new_id = "ch%d" % (len(chans) + 1)

    # Template-based creation: copy verified slug pools + news tags from an
    # existing channel so the new channel posts the relevant table instantly.
    if body.get("template_id"):
        tpl = next((c for c in get_channels() if c["id"] == body["template_id"]), None)
        if tpl is None:
            return JSONResponse({"error": "unknown template"}, status_code=400)
        ch = {"id": new_id,
              "name": body.get("name", tpl.get("name", "کانال جدید")),
              "telegram_id": body.get("telegram_id", ""),
              "enabled": body.get("enabled", True),
              "icon": body.get("icon", tpl.get("icon", "📢")),
              "header": body.get("header", tpl.get("header", "")),
              "section_title": body.get("section_title", tpl.get("section_title", "قیمت‌ها")),
              "slug_groups": body.get("slug_groups", tpl.get("slug_groups", {})),
              "slugs": body.get("slugs", tpl.get("slugs", [])),
              "news_categories": body.get("news_categories", tpl.get("news_categories", [])),
              "analysis_tags": body.get("analysis_tags", tpl.get("analysis_tags", [])),
              "schedule_minutes": body.get("schedule_minutes", tpl.get("schedule_minutes", 10)),
              "poll_enabled": body.get("poll_enabled", False),
              "with_star": body.get("with_star", True),
              "with_analysis": body.get("with_analysis", True),
              "with_footer": body.get("with_footer", True),
              "footer": body.get("footer", tpl.get("footer", "")),
              "format": body.get("format", "chips"),
              "post_types": body.get("post_types", tpl.get("post_types", ["prices"])),
              "template": body.get("template", tpl.get("template", ""))}
    else:
        ch = {"id": new_id,
              "name": body.get("name", "کانال جدید"),
              "telegram_id": body.get("telegram_id", ""),
              "enabled": body.get("enabled", True),
              "icon": body.get("icon", "📢"),
              "header": body.get("header", ""),
              "section_title": body.get("section_title", "قیمت‌ها"),
              "slug_groups": body.get("slug_groups", {}),
              "slugs": body.get("slugs", []),
              "news_categories": body.get("news_categories", []),
              "analysis_tags": body.get("analysis_tags", []),
              "schedule_minutes": body.get("schedule_minutes", 10),
              "poll_enabled": body.get("poll_enabled", False),
              "with_star": body.get("with_star", True),
              "with_analysis": body.get("with_analysis", True),
              "with_footer": body.get("with_footer", True),
              "footer": body.get("footer", ""),
              "format": body.get("format", "chips"),
              "post_types": body.get("post_types", ["prices"]),
              "template": body.get("template", "")}
    chans.append(ch)
    save_channels(chans)
    reload_channels()
    return {"ok": True, "id": new_id}


@app.put("/api/channels/{cid}")
async def api_update_channel(cid: str, req: Request):
    ch = get_channel(cid)
    if not ch:
        return JSONResponse({"error": "unknown channel"}, status_code=404)
    try:
        body = await req.json()
    except Exception:
        body = {}
    for k, v in body.items():
        if k != "id":
            ch[k] = v
    save_channels(get_channels())
    reload_channels()
    return {"ok": True}


@app.delete("/api/channels/{cid}")
def api_delete_channel(cid: str):
    chans = get_channels()
    chans = [c for c in chans if c.get("id") != cid]
    RUNTIME["channels"] = chans
    save_channels(chans)
    reload_channels()
    return {"ok": True}


@app.post("/api/post/{cid}")
async def api_post(cid: str, req: Request):
    ch = get_channel(cid)
    if not ch:
        return JSONResponse({"error": "unknown channel"}, status_code=404)
    try:
        body = await req.json()
    except Exception:
        body = {}
    post_type = body.get("post_type") or "prices"
    if post_type not in ("prices", "news", "poll", "analysis", "all"):
        return JSONResponse({"error": "unknown post_type: %s" % post_type},
                            status_code=400)
    target = body.get("telegram_id") or ch.get("telegram_id")
    if not target:
        return JSONResponse({"error": "no telegram_id set for channel"}, status_code=400)
    rows = cached_rows()
    if post_type == "analysis" and not rows:
        return JSONResponse(
            {"error": "cache empty — منتظر به‌روزرسانی پس‌زمینه بمانید و دوباره تلاش کنید"},
            status_code=503)
    try:
        if post_type == "poll":
            p = pick_poll(ch, ai_pick=True)  # real post
            resp = await asyncio.to_thread(send_telegram_poll, target,
                                           p["question"], p["options"])
            payload = {"type": "poll", "question": p["question"],
                       "source": p.get("source", "random")}
        elif post_type == "all":
            txt = await asyncio.to_thread(build_post_text, ch, "prices", rows)
            resp = {"ok": True}
            if txt:
                resp = await asyncio.to_thread(send_telegram, target, txt)
                if not resp.get("ok"):
                    return JSONResponse(
                        {"error": resp.get("description", resp.get("error", "send failed"))},
                        status_code=502)
            p = pick_poll(ch, ai_pick=True)  # real post (all)
            rp = await asyncio.to_thread(send_telegram_poll, target,
                                         p["question"], p["options"])
            if not rp.get("ok"):
                return JSONResponse(
                    {"error": rp.get("description", rp.get("error", "poll failed"))},
                    status_code=502)
            payload = {"type": "all",
                       "message_id": resp.get("result", {}).get("message_id"),
                       "poll_message_id": rp.get("result", {}).get("message_id")}
            log_line("webapp posted %s (all: prices+poll) -> %s" % (cid, target))
            return {"ok": True, **payload}
        else:
            txt = await asyncio.to_thread(build_post_text, ch, post_type, rows)
            resp = await asyncio.to_thread(send_telegram, target, txt)
            payload = {"type": post_type}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not resp.get("ok"):
        return JSONResponse({"error": resp.get("description", resp.get("error", "send failed"))},
                            status_code=502)
    log_line("webapp posted %s (%s) -> %s" % (cid, post_type, target))
    return {"ok": True, **payload,
            "message_id": resp.get("result", {}).get("message_id")}


@app.post("/api/refresh")
async def api_refresh():
    # trigger a background refresh and return immediately — the dashboard
    # never blocks on tgju.org
    await background_refresh(force=True)
    return {"refreshing": True, "rows": len(cached_rows()),
            "last_fetch": str(RUNTIME["last_fetch"]) if RUNTIME["last_fetch"] else None}


# ── Settings ──────────────────────────────────────────────────────────────
@app.get("/api/settings")
def api_settings_get():
    return load_settings()


@app.put("/api/settings")
async def api_settings_put(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "bad body"}, status_code=400)
    cur = load_settings()
    INT_KEYS = ("fetch_ttl_seconds", "poll_interval_hours", "max_profile_workers",
                "scheduler_interval_seconds", "post_retry_seconds",
                "news_max_items", "poll_options_count",
                "telegram_timeout_seconds")
    INT_ZERO_OK = ("price_decimals", "telegram_retry_count")  # 0 is a valid value
    BOOL_KEYS = ("auto_post", "poll_anonymous")
    STR_KEYS = ("numeral_system", "default_footer", "star_chars")
    for k, v in body.items():
        if k not in DEFAULT_SETTINGS:
            continue
        if k in INT_KEYS:
            try:
                cur[k] = max(1, int(v))
            except (TypeError, ValueError):
                return JSONResponse({"error": "%s must be a number" % k},
                                    status_code=400)
        elif k in INT_ZERO_OK:
            try:
                cur[k] = max(0, int(v))
            except (TypeError, ValueError):
                return JSONResponse({"error": "%s must be a number" % k},
                                    status_code=400)
        elif k in BOOL_KEYS:
            cur[k] = bool(v)
        elif k in STR_KEYS:
            cur[k] = str(v).strip()
    save_settings(cur)
    return {"ok": True, "settings": cur}


@app.get("/api/providers")
def api_providers():
    return {"providers": PROVIDERS, "models": MODEL_SUGGESTIONS}


@app.get("/api/logs")
def api_logs():
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            return HTMLResponse("<pre>" + f.read()[-8000:] + "</pre>")
    except Exception:
        return HTMLResponse("<pre>(خالی)</pre>")


@app.get("/api/polls")
def api_polls_get():
    """Poll store: shared pool + per-channel fixed polls."""
    store = load_poll_store()
    return {
        "questions": store.get("questions") or [],
        "fixed": store.get("fixed") or {},
        "builtin_count": len(POLL_POOL),
    }


@app.post("/api/polls")
async def api_polls_add(req: Request):
    """Add a new poll question to the shared pool (or fixed to a channel)."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    question = (body.get("question") or "").strip()
    options = [str(o).strip() for o in (body.get("options") or []) if str(o).strip()]
    if not question:
        return JSONResponse({"error": "question required"}, status_code=400)
    if len(options) < 2:
        return JSONResponse({"error": "at least 2 options required"}, status_code=400)
    cid = (body.get("channel_id") or "").strip()
    store = load_poll_store()
    entry = {"question": question, "options": options}
    if cid:
        store.setdefault("fixed", {}).setdefault(cid, []).append(entry)
    else:
        store.setdefault("questions", []).append(entry)
    save_poll_store(store)
    log_line("poll added: %s %s" % ("fixed:" + cid if cid else "shared", question[:50]))
    return {"ok": True, "polls": store}


@app.put("/api/polls/{index}")
async def api_polls_update(index: int, req: Request):
    """Update a shared-pool poll at `index` (0-based)."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    question = (body.get("question") or "").strip()
    options = [str(o).strip() for o in (body.get("options") or []) if str(o).strip()]
    if not question:
        return JSONResponse({"error": "question required"}, status_code=400)
    if len(options) < 2:
        return JSONResponse({"error": "at least 2 options required"}, status_code=400)
    store = load_poll_store()
    qs = store.setdefault("questions", [])
    if index < 0 or index >= len(qs):
        return JSONResponse({"error": "index out of range"}, status_code=404)
    qs[index] = {"question": question, "options": options}
    save_poll_store(store)
    return {"ok": True, "polls": store}


@app.delete("/api/polls/{index}")
def api_polls_del(index: int):
    """Delete a shared-pool poll at `index` (0-based)."""
    store = load_poll_store()
    qs = store.setdefault("questions", [])
    if 0 <= index < len(qs):
        del qs[index]
        save_poll_store(store)
    return {"ok": True, "polls": store}


@app.post("/api/polls/delete-fixed")
async def api_polls_del_fixed(req: Request):
    """Delete a fixed poll from a channel's fixed list."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    cid = (body.get("channel_id") or "").strip()
    index = int(body.get("index") or -1)
    store = load_poll_store()
    flist = (store.get("fixed") or {}).get(cid) or []
    if cid and 0 <= index < len(flist):
        del flist[index]
        if not flist:
            store.setdefault("fixed", {}).pop(cid, None)
        save_poll_store(store)
    return {"ok": True, "polls": store}


@app.post("/api/polls/generate")
async def api_polls_generate(req: Request):
    """AI-generate poll questions from the current market context.

    Body: {count: 3, channel_id: optional}. Uses the channel's configured
    AI provider (or the first enabled non-mock provider). Returns a list of
    generated {question, options} — user reviews before saving (never
    auto-added to the pool).
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    count = max(1, min(int(body.get("count") or 3), 6))
    cid = (body.get("channel_id") or "").strip()
    cfg = load_ai_config()
    # pick a provider: channel's analysis provider, else first enabled non-mock
    provider_name = ""
    prov = None
    if cid:
        ch_cfg = (cfg.get("channels") or {}).get(cid) or {}
        provider_name = (ch_cfg.get("analysis") or {}).get("provider") or ""
        prov = (cfg.get("providers") or {}).get(provider_name)
    if not prov or prov.get("kind") == "mock":
        for pname, p in (cfg.get("providers") or {}).items():
            if p.get("kind") != "mock" and p.get("base_url"):
                provider_name, prov = pname, p
                break
    if not prov or prov.get("kind") == "mock" or not prov.get("base_url"):
        return JSONResponse({"ok": False,
                             "error": "هیچ provider فعالی پیکربندی نشده — ابتدا از تب هوش مصنوعی یک provider اضافه کنید"},
                            status_code=400)
    model = (ch_cfg.get("analysis") or {}).get("model") if cid else ""
    model = model or prov.get("model") or ""
    prov_cfg = dict(prov)
    prov_cfg["model"] = model
    from tgju_engine_ai import _chat_completion
    # Build market context from live rows
    rows = cached_rows()
    table_lines = []
    for c in get_channels():
        if c.get("id") != cid and cid:
            continue
        for s in (c.get("slugs") or []):
            row = (rows or {}).get(s) or {}
            if row.get("price"):
                table_lines.append("%s | %s | %s%%" % (
                    row.get("name") or s, row["price"], row.get("change_pct") or "—"))
    table = "\n".join(table_lines[:25]) or "(داده‌ای در دسترس نیست)"
    from tgju_engine_ai import POLL_GEN_PROMPT
    prompt = POLL_GEN_PROMPT % (count, table)
    from tgju_engine_ai import _parse_json_response
    polls = []
    last_error = ""
    for attempt in range(3):  # retry: reasoning models are probabilistic
        try:
            raw = await asyncio.to_thread(_chat_completion, prov_cfg, prompt,
                                          max_tokens=2000, timeout=90)
            try:
                data = _parse_json_response(raw)
                items = data if isinstance(data, list) else data.get("questions", [])
            except Exception:
                items = []
            for it in items[:count]:
                q = (it.get("question") or "").strip()
                opts = [str(o).strip() for o in (it.get("options") or []) if str(o).strip()]
                if q and len(opts) >= 2:
                    polls.append({"question": q, "options": opts})
            if polls:
                break
            last_error = "خروجی JSON نامعتبر (تلاش %d)" % (attempt + 1)
        except Exception as e:
            last_error = str(e)[:200]
            if "HTTP" in last_error:
                if "429" in last_error and attempt < 2:
                    await asyncio.sleep(5.0)  # rate-limited: back off
                    continue
                break  # other HTTP errors won't fix themselves
        await asyncio.sleep(1.0)
    if not polls:
        record_ai_activity({"job": "poll_generate", "status": "error",
                            "error": last_error or "خروجی JSON نامعتبر",
                            "provider": provider_name})
        return JSONResponse({"ok": False,
                             "error": last_error or "مدل خروجی JSON معتبر نداد — دوباره تلاش کنید"},
                            status_code=400)
    record_ai_activity({"job": "poll_generate", "status": "ok",
                        "provider": provider_name, "model": model,
                        "polls": len(polls)})
    return {"ok": True, "polls": polls, "provider": provider_name, "model": model}


# ── AI + routing API ──────────────────────────────────────────────────────
@app.get("/api/ai")
def api_ai_get():
    cfg = load_ai_config()
    return cfg


@app.post("/api/ai/providers")
async def api_ai_add_provider(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad body"}, status_code=400)
    cfg = load_ai_config()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    cfg["providers"][name] = {
        "label": body.get("label") or name,
        "kind": body.get("kind") or "openai_compat",
        "base_url": body.get("base_url") or "",
        "api_key": body.get("api_key") or "",
        "model": body.get("model") or "",
        "enabled": body.get("enabled", True)}
    save_ai_config(cfg)
    return {"ok": True, "providers": cfg["providers"]}


@app.post("/api/ai/models")
async def api_ai_models(req: Request):
    """List model IDs from a provider's /models endpoint (for pickers)."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    from tgju_engine_ai import list_provider_models
    cfg = load_ai_config()
    name = (body.get("provider") or "").strip()
    prov = (cfg.get("providers") or {}).get(name)
    if not prov:
        return JSONResponse({"ok": False, "detail": "provider پیکربندی نشده"},
                            status_code=400)
    return list_provider_models(prov)


@app.delete("/api/ai/providers/{name}")
def api_ai_del_provider(name: str):
    cfg = load_ai_config()
    if name in cfg["providers"]:
        del cfg["providers"][name]
        save_ai_config(cfg)
    return {"ok": True}


@app.post("/api/ai/test/{name}")
async def api_ai_test_provider(name: str, req: Request):
    cfg = load_ai_config()
    prov = cfg["providers"].get(name)
    if not prov:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    try:
        body = await req.json()
    except Exception:
        body = {}
    prov = dict(prov)
    prov.update({k: v for k, v in body.items() if v})
    return test_provider(prov)


@app.post("/api/ai/run/{cid}")
async def api_ai_run(cid: str):
    """Run optional AI analysis for a channel (requires analysis.enabled
    + configured provider). Returns {ok, text, provider, model, latency_ms}
    or {ok:false, error}. Never posts — preview/analysis posting happens via
    /api/post/{cid} with post_type=analysis."""
    ch = get_channel(cid)
    if not ch:
        return JSONResponse({"error": "unknown channel"}, status_code=404)
    cfg = load_ai_config()
    res = await asyncio.to_thread(run_analysis, cfg, ch, cached_rows())
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error", "AI analysis failed")},
                            status_code=400)
    return res


@app.post("/api/ai/channel/{cid}")
async def api_ai_channel_set(cid: str, req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    cfg = load_ai_config()
    cfg.setdefault("channels", {})[cid] = body
    save_ai_config(cfg)
    return {"ok": True}


@app.post("/api/ai/routing/build")
def api_ai_routing_build():
    """Auto-derive category→channels routing from news_categories."""
    cfg = load_ai_config()
    routing = auto_build_routing(get_channels(), cfg)
    save_ai_config(cfg)
    return {"ok": True, "routing": routing}


@app.get("/api/ai/routing")
def api_ai_routing_get():
    cfg = load_ai_config()
    return {"routing": cfg.get("routing", {}),
            "channels": [{"id": c.get("id"), "name": c.get("name"),
                          "icon": c.get("icon", ""),
                          "news_categories": c.get("news_categories", [])}
                         for c in get_channels()]}


# ── Bot Profile API ──────────────────────────────────────────────────────
@app.get("/api/bot")
def api_get_bot():
    """Get bot profiles + current active bot info."""
    from tgju_engine_bot import load_bot_profiles, get_active_token
    cfg = load_bot_profiles()
    active = cfg["active_id"]
    # Try getMe on active bot
    bot_info = {}
    tok = get_active_token()
    if tok:
        try:
            import urllib.request, json as _j
            r = urllib.request.urlopen("https://api.telegram.org/bot%s/getMe" % tok, timeout=10)
            resp = _j.loads(r.read().decode())
            if resp.get("ok"):
                bot_info = resp.get("result", {})
        except Exception:
            bot_info = {}
    return {"active_id": active,
            "profiles": cfg.get("profiles", []),
            "bot_info": bot_info}


@app.post("/api/bot")
async def api_add_bot(req: Request):
    """Add a new bot profile."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    token = (body.get("token") or "").strip()
    name = (body.get("name") or "").strip() or "Bot"
    if not token:
        return JSONResponse({"error": "token is required"}, status_code=400)
    # Validate token
    from tgju_engine_bot import test_bot_token, load_bot_profiles, save_bot_profiles
    res = test_bot_token(token)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error", "invalid token")}, status_code=400)
    cfg = load_bot_profiles()
    bot_id = "bot_%s" % res.get("bot_id", len(cfg["profiles"]))
    # Check for duplicate token
    for p in cfg["profiles"]:
        if p.get("token") == token:
            return JSONResponse({"ok": False, "error": "این توکن قبلاً اضافه شده"}, status_code=400)
    cfg["profiles"].append({
        "id": bot_id,
        "name": name or res.get("bot_name", "Bot"),
        "token": token,
        "bot_username": res.get("bot_username", ""),
        "bot_name": res.get("bot_name", ""),
    })
    save_bot_profiles(cfg)
    return {"ok": True, "id": bot_id, "bot_name": res.get("bot_name"), "bot_username": res.get("bot_username")}


@app.post("/api/bot/activate/{bot_id}")
def api_activate_bot(bot_id: str):
    """Set a bot profile as active."""
    from tgju_engine_bot import load_bot_profiles, save_bot_profiles
    cfg = load_bot_profiles()
    for p in cfg["profiles"]:
        if p["id"] == bot_id:
            cfg["active_id"] = bot_id
            save_bot_profiles(cfg)
            return {"ok": True}
    return JSONResponse({"error": "unknown bot id"}, status_code=404)


@app.delete("/api/bot/{bot_id}")
def api_delete_bot(bot_id: str):
    """Delete a bot profile."""
    from tgju_engine_bot import load_bot_profiles, save_bot_profiles
    cfg = load_bot_profiles()
    before = len(cfg["profiles"])
    cfg["profiles"] = [p for p in cfg["profiles"] if p["id"] != bot_id]
    if len(cfg["profiles"]) == before:
        return JSONResponse({"error": "unknown bot id"}, status_code=404)
    if cfg["active_id"] == bot_id:
        cfg["active_id"] = cfg["profiles"][0]["id"] if cfg["profiles"] else ""
    save_bot_profiles(cfg)
    return {"ok": True}


@app.post("/api/bot/test")
async def api_test_bot(req: Request):
    """Test a bot token without saving."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    token = (body.get("token") or "").strip()
    if not token:
        return JSONResponse({"error": "token required"}, status_code=400)
    from tgju_engine_bot import test_bot_token
    res = test_bot_token(token)
    return res


@app.get("/api/bot/status")
def api_bot_status():
    """Quick status: is a bot token available?"""
    tok = get_bot_token()
    return {"token_set": bool(tok), "token_preview": (tok[:8] + "...") if tok else ""}


# ── Functions API ────────────────────────────────────────────────────────
@app.get("/api/functions")
def api_get_functions():
    """Get all function configs (analysis interval, template, effort, channels)."""
    from tgju_engine_functions import load_functions, function_channel_enabled, function_channel_interval
    fns = load_functions()
    # enrich with per-channel status
    for fid, fn in fns.items():
        chans = fn.get("channels") or {}
        if not chans:
            # inherit from channel post_types: enabled if 'analysis' in post_types
            for ch in get_channels():
                if function_channel_enabled(fn, ch["id"]):
                    chans[ch["id"]] = {"enabled": True, "interval_hours": function_channel_interval(fn, ch["id"], 6)}
            fn["channels"] = chans
    # add runtime info: last_analysis_at from state
    state_keys = {
        "analysis": "last_analysis_at",
        "news": "last_news_at",
    }
    for fid, fn in fns.items():
        skey = state_keys.get(fid, "last_poll_at")
        for cid, ccfg in fn.get("channels").items():
            try:
                st = load_channel_state(cid)
                ccfg["last_run_at"] = st.get(skey) or ""
                ccfg["last_error"] = st.get("last_%s_error" % fid) or ""
            except Exception:
                ccfg["last_run_at"] = ""
    return {"functions": fns,
            "channels": [{"id": c.get("id"), "name": c.get("name"),
                          "icon": c.get("icon", ""), "analysis_tags": c.get("analysis_tags", [])}
                         for c in get_channels()]}


@app.put("/api/functions")
async def api_save_functions(req: Request):
    """Save function configs."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    from tgju_engine_functions import save_functions, load_functions
    fns = load_functions()
    fns.update(body.get("functions") or {})
    save_functions(fns)
    return {"ok": True}


@app.post("/api/functions/analysis/run/{cid}")
async def api_run_analysis_now(cid: str):
    """Run analysis for a specific channel NOW (posts to Telegram)."""
    ch = get_channel(cid)
    if not ch:
        return JSONResponse({"error": "unknown channel"}, status_code=404)
    cfg = load_ai_config()
    rows = cached_rows()
    res = await asyncio.to_thread(run_analysis, cfg, ch, rows)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error", "AI analysis failed")}, status_code=400)
    # post to Telegram
    template = ""
    try:
        from tgju_engine_functions import load_functions
        fns = load_functions()
        template = (fns.get("analysis") or {}).get("template", "")
    except Exception:
        pass
    wd = FA_WEEKDAYS[datetime.now().weekday()]
    hm = fa_num(datetime.now().strftime("%H:%M"))
    header = template.format(name=ch.get("name", ch.get("id")),
                             weekday=wd, time=hm) if template else (
        "📈 تحلیل بازار %s | %s %s" % (ch.get("name", ""), wd, hm))
    lines = [header, _FORMAT_SEP, res["text"]]
    if ch.get("with_footer", True):
        lines.append(_esc(ch.get("footer") or "منبع: tgju.org"))
    text = "\n".join(lines)
    resp = send_telegram(ch["telegram_id"], text)
    if resp.get("ok"):
        st = load_channel_state(cid)
        st["last_analysis_at"] = datetime.now().isoformat(timespec="seconds")
        save_channel_state(cid, st)
        return {"ok": True, "analysis": res, "post": {"message_id": resp.get("result", {}).get("message_id")}}
    return JSONResponse({"ok": False, "error": resp.get("error", "post failed")}, status_code=500)


# ── AI Orchestrator API ──────────────────────────────────────────────────
@app.get("/api/ai/orchestrator")
def api_ai_orchestrator():
    """AI orchestrator view: jobs + live activity + providers + channels."""
    cfg = load_ai_config()
    jobs_data = load_ai_jobs()
    provs = cfg.get("providers") or {}
    return {
        "jobs": jobs_data["jobs"],
        "activity": list(reversed(jobs_data["activity"])),  # newest first
        "providers": [{"name": n, "label": p.get("label") or n,
                       "model": p.get("model") or "", "kind": p.get("kind"),
                       "enabled": p.get("enabled", True),
                       "base_url": p.get("base_url") or ""}
                      for n, p in provs.items()],
        "channels": [{"id": c.get("id"), "name": c.get("name"),
                      "icon": c.get("icon", ""),
                      "ai_enabled": bool(((cfg.get("channels") or {})
                                          .get(c.get("id")) or {})
                                         .get("analysis", {}).get("enabled"))}
                     for c in get_channels()],
    }


@app.post("/api/ai/jobs/{job_id}")
async def api_ai_job_save(job_id: str, req: Request):
    """Update one job's config (enabled/provider/model/max_tokens/timeout/...)."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    data = load_ai_jobs()
    if job_id not in data["jobs"]:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    job = data["jobs"][job_id]
    for k in ("enabled", "provider", "model", "max_tokens", "timeout_s",
              "default_count", "channels"):
        if k in body:
            job[k] = body[k]
    save_ai_jobs({"jobs": data["jobs"], "activity": data["activity"]})
    return {"ok": True, "job": job}


@app.post("/api/ai/jobs/{job_id}/run")
async def api_ai_job_run(job_id: str):
    """Run one job now (analysis for all/selected channels)."""
    data = load_ai_jobs()
    if job_id not in data["jobs"]:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    if job_id == "analysis":
        cfg = load_ai_config()
        rows = cached_rows()
        chans = get_channels()
        results = []
        for c in chans:
            if not c.get("enabled"):
                continue
            ch_cfg = (cfg.get("channels") or {}).get(c["id"]) or {}
            if not (ch_cfg.get("analysis") or {}).get("enabled"):
                continue
            res = await asyncio.to_thread(run_analysis, cfg, c, rows)
            results.append({"channel": c["id"], "ok": res.get("ok"),
                            "latency_ms": res.get("latency_ms"),
                            "error": res.get("error"),
                            "text": res.get("text")})
        return {"ok": True, "results": results}
    if job_id == "poll_generate":
        return JSONResponse({"error": "use POST /api/polls/generate"},
                            status_code=400)
    return {"ok": True, "note": "job %s has no standalone run" % job_id}


# ── Async scheduler (in-app, self-healing) ────────────────────────────────
def _channel_post_types(c: dict) -> list:
    pts = c.get("post_types") or []
    if not pts:
        pts = ["prices"]
    return [p for p in pts if p in ("prices", "news", "poll", "analysis", "all")]


def _next_post_type(c: dict, now: datetime) -> str:
    """Rotation: cycle the channel's post_types (default ['prices']); a
    channel with poll_enabled gets a poll on every poll_interval boundary
    (default: hours 0,4,8,12,16,20 — matches the legacy `0 */4 * * *` cron).

    FIX (v2): polls fire on the interval boundary whenever poll_enabled=True,
    even if 'poll' is not listed in post_types — so enabling polls via the UI
    actually turns them on in the scheduler.

    v3 (functions): the 'analysis' function fires on its OWN interval
    (functions.json → interval_hours, default 6-8h) tracked via
    last_analysis_at in channel state. When the analysis function is enabled
    for the channel AND its interval elapsed, analysis REPLACES this tick's
    post — so enabling analysis NEVER steals price slots permanently.
    """
    # 1) Analysis function (interval-driven, independent of rotation)
    try:
        from tgju_engine_functions import load_functions, function_channel_enabled, function_channel_interval
        fns = load_functions()
        fn = fns.get("analysis") or {}
        if function_channel_enabled(fn, c.get("id", "")):
            iv = function_channel_interval(fn, c.get("id", ""), 6)
            st = load_channel_state(c.get("id", ""))
            la = st.get("last_analysis_at")
            if not la:
                return "analysis"  # first run when enabled
            try:
                la_dt = datetime.fromisoformat(la)
                if (now - la_dt) >= timedelta(hours=iv):
                    return "analysis"
            except Exception:
                return "analysis"
    except Exception:
        pass
    # 2) Polls (interval-driven, configurable via functions.json → poll)
    try:
        from tgju_engine_functions import (load_functions, function_channel_enabled,
                                           function_channel_interval)
        fns = load_functions()
        pfn = fns.get("poll") or {}
        poll_on = function_channel_enabled(pfn, c.get("id", ""))
    except Exception:
        poll_on = False
    # Backwards-compat: legacy per-channel poll_enabled toggle on the channel
    if not poll_on and c.get("poll_enabled"):
        poll_on = True
    if poll_on:
        try:
            poll_iv = function_channel_interval(pfn, c.get("id", ""),
                                                int(load_settings().get("poll_interval_hours", 4)))
        except Exception:
            poll_iv = max(1, int(load_settings().get("poll_interval_hours", 4)))
        if now.hour % poll_iv == 0:
            # Only fire ONE poll per boundary, not the whole hour:
            # a channel posting every 10min would otherwise send 6 polls
            # per 4h window. Track last_poll_at in channel state.
            try:
                st = load_channel_state(c.get("id", ""))
                lp = st.get("last_poll_at")
                if lp:
                    lp_dt = datetime.fromisoformat(lp)
                    if (now - lp_dt) < timedelta(hours=poll_iv):
                        # poll already sent within this window → skip to
                        # the regular rotation for this tick
                        pass
                    else:
                        return "poll"
                else:
                    return "poll"
            except Exception:
                return "poll"
    # 3) News (interval-driven, configurable via functions.json → news)
    try:
        fns = load_functions()
        nfn = fns.get("news") or {}
        news_on = function_channel_enabled(nfn, c.get("id", ""))
        if news_on:
            news_iv = function_channel_interval(nfn, c.get("id", ""), 6)
            st = load_channel_state(c.get("id", ""))
            ln = st.get("last_news_at")
            if not ln:
                return "news"  # first run when enabled
            try:
                ln_dt = datetime.fromisoformat(ln)
                if (now - ln_dt) >= timedelta(hours=news_iv):
                    return "news"
            except Exception:
                return "news"
    except Exception:
        pass
    pts = _channel_post_types(c)
    if "all" in pts:
        return "all"
    # rotate by the hour so a multi-type channel alternates deterministically
    idx = now.hour % len(pts)
    return pts[idx]


async def _scheduler_tick():
    settings = load_settings()
    if not settings.get("auto_post", True):
        return  # master kill-switch
    chans = reload_channels()
    now = datetime.now()
    for c in chans:
        try:
            if not c.get("enabled"):
                continue
            cid = c["id"]
            if not c.get("telegram_id"):
                continue  # no telegram_id -> skip (never even attempt)
            st = load_channel_state(cid)
            min_iv = int(c.get("schedule_minutes") or 10)
            last = st.get("last_post_at")
            due = False
            if not last:
                due = True  # first post when scheduler starts
            else:
                try:
                    last_dt = datetime.fromisoformat(last)
                    due = (now - last_dt) >= timedelta(minutes=min_iv)
                except Exception:
                    due = True
            if not due:
                continue
            # cached rows; force-refresh ONLY if older than 2x fetch_ttl
            rows = cached_rows()
            ttl = max(int(settings.get("fetch_ttl_seconds", 60)), 10)
            if not rows or not RUNTIME["last_fetch"] or \
                    (now - RUNTIME["last_fetch"]) > timedelta(seconds=2 * ttl):
                rows = await asyncio.to_thread(refresh_prices, True)
            post_type = _next_post_type(c, now)
            # Detect stale/fallback data so posts carry a visible notice
            stale = bool(RUNTIME.get("degraded"))
            stale_age_h = 0.0
            if stale:
                try:
                    from tgju_engine_fallback import fallback_prices_age_seconds
                    fb_s = fallback_prices_age_seconds()
                    if fb_s is not None:
                        stale_age_h = fb_s / 3600.0
                except Exception:
                    stale_age_h = 1.0  # assume 1h if can't read
            if post_type == "prices":
                # Route price posts through the core pipeline (runs/events/idempotency)
                try:
                    import tgju_core_integration as ci
                    from tgju_core.types import TriggerType
                    ci.init_core()
                    res = await asyncio.to_thread(
                        ci.orchestrated_post, c, rows, TriggerType.SCHEDULER,
                        lambda m: send_telegram(c["telegram_id"], m),
                        stale=stale, stale_age_hours=stale_age_h)
                    ok = bool(res.get("ok"))
                    detail = res.get("error") or ""
                    if ok:
                        st["run_id"] = res.get("run_id")
                        st["message_id"] = res.get("message_id")
                except Exception as e:
                    # Fallback to legacy direct send if core integration fails
                    resp = await asyncio.to_thread(send_telegram, c["telegram_id"],
                                                   build_for_channel(c, rows, stale=stale, stale_age_hours=stale_age_h))
                    ok = bool(resp.get("ok"))
                    detail = resp.get("description") or resp.get("error", "send failed")
            else:
                resp = await asyncio.to_thread(post_channel_type, c, post_type, rows)
                ok = bool(resp.get("ok"))
                detail = resp.get("error") or resp.get("description") or ""
            if ok:
                st["last_post_at"] = now.isoformat(timespec="seconds")
                st["last_ok"] = now.isoformat(timespec="seconds")
                st["last_error"] = None
                st["last_type"] = post_type
                if post_type == "poll":
                    st["last_poll_at"] = now.isoformat(timespec="seconds")
                if post_type == "analysis":
                    st["last_analysis_at"] = now.isoformat(timespec="seconds")
                if post_type == "news":
                    st["last_news_at"] = now.isoformat(timespec="seconds")
                log_line("scheduler posted %s (%s)" % (cid, post_type))
            else:
                st["last_error"] = detail
                log_line("scheduler ERROR %s (%s): %s" % (cid, post_type, detail))
            save_channel_state(cid, st)
        except Exception as e:
            try:
                st = load_channel_state(c["id"])
                st["last_error"] = str(e)[:300]
                save_channel_state(c["id"], st)
            except Exception:
                pass
            log_line("scheduler ERROR %s: %s" % (c.get("id"), e))


async def scheduler_loop():
    while True:
        try:
            await _scheduler_tick()
        except Exception as e:
            log_line("scheduler loop error: %s" % e)
        # WhatsApp is an interactive bot — replies happen on demand via the
        # webhook; no auto-broadcast loop at scheduler level.
        # Bale mirrors the Telegram channel-orchestration model.
        try:
            await _bale_scheduler_tick()
        except Exception as e:
            log_line("bale scheduler loop error: %s" % e)
        tick = max(5, int(load_settings().get("scheduler_interval_seconds", 45)))
        await asyncio.sleep(tick)


@app.on_event("startup")
async def startup():
    RUNTIME["channels"] = load_channels()
    # warm the cache immediately (background), then keep it warm
    asyncio.create_task(refresher_loop())
    task = asyncio.create_task(scheduler_loop())
    RUNTIME["scheduler"] = task


# ── Command Center (tgju_core) ─────────────────────────────────────────────
@app.get("/api/command_center")
def api_command_center():
    """Aggregated Command Center data: health, runs, approvals, next actions."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        rows = RUNTIME["last_rows"] or {}
        return ci.command_center(get_channels(), rows)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/explain/{run_id}")
def api_explain(run_id: str):
    """Explainability: why did this run happen / post get published?"""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        return ci.explain_run(run_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/runs")
def api_runs():
    """Recent execution runs (observability)."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.runs import get_run_manager
        runs = get_run_manager().list_runs(limit=50)
        return {"runs": [r.to_dict() for r in runs]}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/events")
def api_events():
    """Recent structured events (observability)."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.events import get_event_bus
        events = get_event_bus().query_events(limit=100)
        return {"events": [e.to_dict() for e in events]}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/simulate/{cid}")
def api_simulate(cid: str):
    """Dry-run simulation for a channel (no Telegram delivery)."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.types import TriggerType
        channel = next((c for c in get_channels() if c["id"] == cid), None)
        if not channel:
            return JSONResponse({"ok": False, "error": f"Channel {cid} not found"}, status_code=404)
        rows = RUNTIME["last_rows"] or {}
        res = ci.orchestrated_post(channel, rows, TriggerType.SIMULATION, send_fn=None, simulation=True)
        return res
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/simulate_all")
def api_simulate_all():
    """Simulate all enabled channels (dry run)."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.simulation import SimulationRunner
        from tgju_core.types import Snapshot, generate_snapshot_id
        from datetime import datetime
        rows = RUNTIME["last_rows"] or {}
        snap = Snapshot(id=generate_snapshot_id(), created_at=datetime.now(),
                        source="tgju.org", raw_data=rows, normalized_data={"prices": rows})
        runner = SimulationRunner()
        return runner.simulate_all(snap)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/approvals")
def api_approvals():
    """Pending content approvals."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.approval import get_approval_manager
        return {"pending": get_approval_manager().get_pending(),
                "history": get_approval_manager().get_history(limit=20)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/approvals/{approval_id}/{action}")
def api_approval_action(approval_id: str, action: str):
    """Approve or reject pending content."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.approval import get_approval_manager
        mgr = get_approval_manager()
        if action == "approve":
            ok = mgr.approve(approval_id, decided_by="webapp")
        elif action == "reject":
            ok = mgr.reject(approval_id, decided_by="webapp", reason="Rejected via webapp")
        else:
            return JSONResponse({"ok": False, "error": f"Unknown action {action}"}, status_code=400)
        return {"ok": ok}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/health")
def api_health():
    """System health scoring."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.health import HealthScorer
        scorer = HealthScorer()
        return scorer.get_overall_health()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/secret_health")
def api_secret_health():
    """Secrets configuration status (masked)."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.secrets import get_secrets_manager
        mgr = get_secrets_manager()
        return {"secrets": mgr.list_secrets(),
                "missing": mgr.get_required_missing(["TELEGRAM_BOT_TOKEN"])}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Platforms (multi-platform control center) ─────────────────────────────
@app.get("/api/platforms")
def api_platforms():
    """Registry of manageable platforms (for the control center header)."""
    return {"platforms": [
        {"id": "telegram", "name": "تلگرام", "icon": "✈️",
         "description": "کانال‌ها، ربات‌ها و ارسال خودکار تلگرام"},
        {"id": "whatsapp", "name": "واتساپ", "icon": "💬",
         "description": "شماره‌ها و ارسال خودکار واتساپ (Meta Cloud API)"},
        {"id": "bale", "name": "بله", "icon": "✅",
         "description": "کانال‌ها و ارسال خودکار بله (Bale)"},
    ]}


# ── WhatsApp interactive bot (Meta Cloud API) ─────────────────────────────
@app.get("/api/whatsapp")
def api_whatsapp_config():
    """WhatsApp bot full config: settings + categories + user count (token masked)."""
    try:
        import tgju_engine_whatsapp as wa
        data = wa.load_whatsapp()
        s = dict(data["settings"])
        if s.get("access_token"):
            s["access_token"] = "***"
        return {
            "settings": s,
            "categories": data["categories"],
            "users": len(data.get("users") or {}),
            "mock": wa.is_mock(),
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/whatsapp/settings")
async def api_whatsapp_settings(req: Request):
    try:
        import tgju_engine_whatsapp as wa
        body = await req.json()
        data = wa.load_whatsapp()
        for k in ("access_token", "phone_number_id", "verify_token"):
            if k in body:
                v = str(body[k] or "").strip()
                data["settings"][k] = v
        if "mock" in body:
            data["settings"]["mock"] = bool(body["mock"])
        if "welcome" in body:
            data["settings"]["welcome"] = str(body["welcome"] or "")
        if "about" in body:
            data["settings"]["about"] = str(body["about"] or "")
        wa.save_whatsapp(data)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/whatsapp/test")
def api_whatsapp_test():
    try:
        import tgju_engine_whatsapp as wa
        return wa.test_credentials()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/whatsapp/categories")
def api_whatsapp_categories():
    """Bot menu categories (each with id/label/menu_code/slug_groups)."""
    try:
        import tgju_engine_whatsapp as wa
        return {"categories": wa.get_categories()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/whatsapp/categories")
async def api_whatsapp_save_categories(req: Request):
    try:
        import tgju_engine_whatsapp as wa
        body = await req.json()
        cats = body.get("categories") or []
        wa.save_categories(cats)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/whatsapp/broadcast")
async def api_whatsapp_broadcast(req: Request):
    """Send any text to one phone (or all known users) — bot replies are
    built the same way (mock-aware). Never blocks on network (to_thread)."""
    try:
        import tgju_engine_whatsapp as wa
        body = {}
        try:
            body = await req.json()
        except Exception:
            pass
        text = str(body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "متن پیام خالی است"}, status_code=400)
        to = str(body.get("to") or "").strip()
        targets = []
        if to:
            targets.append(wa.normalize_phone(to))
        else:
            data = wa.load_whatsapp()
            targets = list((data.get("users") or {}).keys())
            targets = [t for t in targets if t]
        if not targets:
            return JSONResponse({"error": "کاربری برای ارسال نیست (اول شبیه‌سازی کنید)"}, status_code=400)
        results = []
        for ph in targets:
            r = await asyncio.to_thread(wa.send_whatsapp, ph, text)
            results.append({"to": ph, "ok": bool(r.get("ok")),
                            "message_id": r.get("message_id"), "error": r.get("error")})
        return {"ok": True, "results": results, "mock": wa.is_mock()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/whatsapp/simulate")
async def api_whatsapp_simulate(req: Request):
    """Simulate a WhatsApp user conversation (no network; mock-friendly)."""
    try:
        import tgju_engine_whatsapp as wa
        body = {}
        try:
            body = await req.json()
        except Exception:
            pass
        phone = wa.normalize_phone(str(body.get("phone") or "989120000000"))
        steps = body.get("steps") or ["1", "1-1"]
        return await asyncio.to_thread(wa.simulate_conversation, phone, steps)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/whatsapp/status")
def api_whatsapp_status():
    """WhatsApp bot status: mock, categories, known users (instant)."""
    try:
        import tgju_engine_whatsapp as wa
        data = wa.load_whatsapp()
        return {
            "mock": wa.is_mock(),
            "categories": len(data["categories"]),
            "users": len(data.get("users") or {}),
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── WhatsApp webhook (Meta Cloud API) ─────────────────────────────────────
@app.get("/api/whatsapp/webhook")
def api_whatsapp_webhook_verify(req: Request):
    """GET verification required by Meta when registering the webhook."""
    try:
        mode = req.query_params.get("hub.mode", "")
        token = req.query_params.get("hub.verify_token", "")
        challenge = req.query_params.get("hub.challenge", "")
        import tgju_engine_whatsapp as wa
        if wa.verify_webhook(mode, token):
            return Response(challenge, media_type="text/plain")
        return JSONResponse({"error": "verification failed"}, status_code=403)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/whatsapp/webhook")
async def api_whatsapp_webhook(req: Request):
    """POST inbound messages from Meta → handle → reply via Cloud API.

    Never blocks on network (replies go via to_thread). Echoes 200 fast.
    """
    try:
        payload = await req.json()
        import tgju_engine_whatsapp as wa
        replies = await asyncio.to_thread(wa.process_webhook, payload)
        if replies:
            for r in replies:
                await asyncio.to_thread(wa.send_whatsapp, r["to"], r["text"])
            log_line("whatsapp webhook handled %d inbound message(s)" % len(replies))
        return {"ok": True}
    except Exception as e:
        log_line("whatsapp webhook error: %s" % str(e)[:200])
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Bale (بله) — Telegram-compatible channel platform ─────────────────────
@app.get("/api/bale")
def api_bale_config():
    """Bale full config: settings + channels (token masked)."""
    try:
        import tgju_engine_bale as bale
        data = bale.load_bale()
        s = dict(data["settings"])
        if s.get("access_token"):
            s["access_token"] = "***"
        return {"settings": s, "channels": data["channels"],
                "mock": bale.is_mock()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/bale/settings")
async def api_bale_settings(req: Request):
    try:
        import tgju_engine_bale as bale
        body = await req.json()
        data = bale.load_bale()
        s = data["settings"]
        for k in ("access_token", "auto_post"):
            if k in body:
                s[k] = body[k]
        bale.save_bale(data)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/bale/test")
async def api_bale_test():
    try:
        import tgju_engine_bale as bale
        r = await asyncio.to_thread(bale.test_credentials)
        if r.get("ok"):
            bot = r.get("bot") or {}
            return {"ok": True, "mock": bool(r.get("mock")),
                    "username": bot.get("username", "(mock)")}
        return JSONResponse({"ok": False, "error": r.get("error", "test failed")},
                            status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/bale/status")
def api_bale_status():
    """Bale channels + per-channel state (instant, no network)."""
    try:
        import tgju_engine_bale as bale
        data = bale.load_bale()
        out = []
        for c in data["channels"]:
            st = bale.load_channel_state(c.get("id", ""))
            out.append({
                "id": c.get("id"), "name": c.get("name"),
                "bale_id": c.get("bale_id", ""), "enabled": bool(c.get("enabled")),
                "schedule_minutes": c.get("schedule_minutes", 30),
                "last_post": st.get("last_post_at"),
                "last_error": st.get("last_error"),
            })
        return {"channels": out, "mock": bale.is_mock(),
                "configured": bale.is_configured()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/bale/preview/{cid}")
def api_bale_preview(cid: str, post_type: str = "prices"):
    """Instant preview (mock-friendly) — reuses Telegram builders."""
    try:
        import tgju_engine_bale as bale
        data = bale.load_bale()
        ch = next((c for c in data["channels"] if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown bale channel %s" % cid},
                                status_code=404)
        text = bale.preview_channel(ch, post_type)
        return {"ok": True, "preview": text, "type": post_type}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/bale/post/{cid}")
async def api_bale_post(cid: str, req: Request):
    """Live post to a Bale channel (network via to_thread, never blocks)."""
    try:
        import tgju_engine_bale as bale
        body = await req.json()
        post_type = (body or {}).get("post_type", "prices")
        data = bale.load_bale()
        ch = next((c for c in data["channels"] if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown bale channel %s" % cid},
                                status_code=404)
        if not ch.get("bale_id"):
            return JSONResponse({"ok": False, "error": "channel has no bale_id"},
                                status_code=400)
        text = bale.preview_channel(ch, post_type)
        if not text:
            return JSONResponse({"ok": False, "error": "empty message built"},
                                status_code=400)
        if bale.is_mock():
            resp = {"ok": True, "message_id": "bale-mock-%d" % (abs(hash(text)) % 10 ** 8),
                    "mock": True}
        else:
            resp = await asyncio.to_thread(bale.send_bale, ch["bale_id"], text)
        st = bale.load_channel_state(cid)
        if resp.get("ok"):
            st["last_post_at"] = datetime.now().isoformat(timespec="seconds")
            st["last_ok"] = datetime.now().isoformat(timespec="seconds")
            st["last_error"] = None
            st["last_type"] = post_type
            st["message_id"] = resp.get("message_id")
        else:
            st["last_error"] = resp.get("error") or "send failed"
        bale.save_channel_state(cid, st)
        resp["ok"] = bool(resp.get("ok"))
        resp["channel"] = cid
        resp["mock"] = bool(resp.get("mock"))
        return resp if resp.get("ok") else JSONResponse(resp, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/bale/channels")
async def api_bale_new_channel(req: Request):
    try:
        import tgju_engine_bale as bale
        body = await req.json()
        data = bale.load_bale()
        cid = "bale%d" % (len(data["channels"]) + 1)
        ch = {
            "id": cid,
            "name": (body.get("name") or "").strip() or "کانال بله",
            "bale_id": (body.get("bale_id") or "").strip(),
            "enabled": bool(body.get("enabled", True)),
            "schedule_minutes": int(body.get("schedule_minutes") or 30),
            "icon": (body.get("icon") or "🟢").strip(),
            "slug_groups": body.get("slug_groups") or {},
            "slugs": body.get("slugs") or [],
            "post_types": body.get("post_types") or ["prices"],
        }
        data["channels"].append(ch)
        bale.save_bale(data)
        return {"ok": True, "channel": ch}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.put("/api/bale/channels/{cid}")
async def api_bale_update_channel(cid: str, req: Request):
    try:
        import tgju_engine_bale as bale
        body = await req.json()
        data = bale.load_bale()
        ch = next((c for c in data["channels"] if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown bale channel %s" % cid},
                                status_code=404)
        for k in ("name", "bale_id", "enabled", "schedule_minutes", "icon",
                  "slug_groups", "slugs", "post_types"):
            if k in body:
                ch[k] = body[k]
        bale.save_bale(data)
        return {"ok": True, "channel": ch}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.delete("/api/bale/channels/{cid}")
def api_bale_delete_channel(cid: str):
    try:
        import tgju_engine_bale as bale
        data = bale.load_bale()
        data["channels"] = [c for c in data["channels"] if c.get("id") != cid]
        bale.save_bale(data)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def _bale_scheduler_tick():
    """Bale scheduler — mirrors the Telegram tick (prices/news/poll/analysis)."""
    try:
        import tgju_engine_bale as bale
    except Exception:
        return
    data = bale.load_bale()
    s = data["settings"]
    if not s.get("auto_post"):
        return
    if bale.is_mock():
        return  # never auto-post in mock
    settings = load_settings()
    now = datetime.now()
    chans = data["channels"]
    for c in chans:
        try:
            if not c.get("enabled") or not c.get("bale_id"):
                continue
            cid = c["id"]
            st = bale.load_channel_state(cid)
            min_iv = int(c.get("schedule_minutes") or s.get("schedule_minutes", 30))
            last = st.get("last_post_at")
            due = False
            if not last:
                due = True
            else:
                try:
                    last_dt = datetime.fromisoformat(last)
                    due = (now - last_dt) >= timedelta(minutes=min_iv)
                except Exception:
                    due = True
            if not due:
                continue
            rows = cached_rows() or {}
            ttl = max(int(settings.get("fetch_ttl_seconds", 60)), 10)
            if not rows or not RUNTIME["last_fetch"] or \
                    (now - RUNTIME["last_fetch"]) > timedelta(seconds=2 * ttl):
                rows = await asyncio.to_thread(refresh_prices, True)
            # same post-type rotation as Telegram
            post_type = _next_post_type(c, now)
            stale = bool(RUNTIME.get("degraded"))
            text = bale.preview_channel(c, post_type)
            if not text:
                continue
            resp = await asyncio.to_thread(bale.send_bale, c["bale_id"], text)
            if resp.get("ok"):
                st["last_post_at"] = now.isoformat(timespec="seconds")
                st["last_ok"] = now.isoformat(timespec="seconds")
                st["last_error"] = None
                st["last_type"] = post_type
                st["message_id"] = resp.get("message_id")
                if post_type == "poll":
                    st["last_poll_at"] = now.isoformat(timespec="seconds")
                if post_type == "analysis":
                    st["last_analysis_at"] = now.isoformat(timespec="seconds")
                if post_type == "news":
                    st["last_news_at"] = now.isoformat(timespec="seconds")
                log_line("bale scheduler posted %s (%s)" % (cid, post_type))
            else:
                st["last_error"] = resp.get("error") or "send failed"
                log_line("bale scheduler ERROR %s (%s): %s" % (cid, post_type,
                                                               st["last_error"]))
            bale.save_channel_state(cid, st)
        except Exception as e:
            try:
                st = bale.load_channel_state(c["id"])
                st["last_error"] = str(e)[:300]
                bale.save_channel_state(c["id"], st)
            except Exception:
                pass
            log_line("bale scheduler ERROR %s: %s" % (c.get("id"), e))


# ── UI ────────────────────────────────────────────────────────────────────
UI_PAGE = None


@app.get("/", response_class=HTMLResponse)
def index():
    global UI_PAGE
    if UI_PAGE is None:
        ui_path = os.path.join(BASE, "tgju_platform_ui.html")
        try:
            UI_PAGE = open(ui_path, encoding="utf-8").read()
        except Exception:
            UI_PAGE = "<html><body><h1>UI file missing</h1></body></html>"
    return HTMLResponse(UI_PAGE, headers={"Content-Type": "text/html; charset=utf-8"})


if __name__ == "__main__":
    import uvicorn
    print("TGJU Telegram Platform → http://localhost:8791")
    uvicorn.run(app, host="0.0.0.0", port=8791)