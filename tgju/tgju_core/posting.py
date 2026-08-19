# -*- coding: utf-8 -*-
"""tgju_core/posting.py — Post text building and per-channel delivery.

Extracted verbatim from tgju_platform.py (lines 1067–1184): build_post_text
(prices|news|analysis), the stale-notice helpers and post_channel_type.
No behavior change.
"""
from datetime import datetime

from tgju_engine_config import log_line  # noqa: E402
from tgju_engine_orchestrator import build_for_channel  # noqa: E402
from tgju_engine_ai import (load_ai_config, run_analysis,  # noqa: E402
                            _channel_domain)
from tgju_engine_format import (SEP as _FORMAT_SEP, FA_WEEKDAYS,  # noqa: E402
                                fa_num)
from tgju_core.runtime import RUNTIME  # noqa: E402
from tgju_core.sender import send_telegram, send_telegram_poll  # noqa: E402
from tgju_core.polls import pick_poll  # noqa: E402
from tgju_core.state import cached_rows  # noqa: E402


def _esc(s) -> str:
    """Escape channel-supplied text for Telegram's HTML parser."""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
