# -*- coding: utf-8 -*-
"""tgju_core/status.py — Status / health endpoints & connection probes.

Extracted verbatim from tgju_platform.py (lines 735–783 for api_status,
2316–2340 for health endpoints) plus the Bot API connection probe layer
(lines 643–732).  No behavior change.
"""
import time
from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from tgju_engine_config import load_channel_state  # noqa: E402
from tgju_core.runtime import RUNTIME, CONN_PROBE_CACHE  # noqa: E402
from tgju_core.settings import load_settings  # noqa: E402
from tgju_core.state import get_channels  # noqa: E402
from tgju_core.sender import get_bot_token, _tg_api_call  # noqa: E402


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


# ── Connection probe (live Bot API, 60s cache) ────────────────────────────
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


def api_connections_probe():
    """LIVE Bot API probes with a 60s cache.

    status ∈ {not_set, ok_admin, not_admin, bot_missing, error:<msg>}.
    """
    if not CONN_PROBE_CACHE["entry"] or \
            time.time() - CONN_PROBE_CACHE["entry"] >= 60:
        _build_connection_probe()
    return CONN_PROBE_CACHE["data"]


# ── Health (tgju_core) ────────────────────────────────────────────────────
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
