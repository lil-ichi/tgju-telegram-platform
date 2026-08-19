# -*- coding: utf-8 -*-
"""tgju_core/polls.py — Engagement poll pool, persistent store and picker.

Extracted verbatim from tgju_platform.py (lines 312–578): the built-in
POLL_POOL (24 approved questions), the state/polls.json store helpers and
the pick_poll strategy (fixed polls → AI pick → random no-repeat).
No behavior change.
"""
import json
import os
import re

from tgju_core.runtime import RUNTIME  # noqa: E402
from tgju_core.state import cached_rows  # noqa: E402
from tgju_engine_ai import record_ai_activity  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
