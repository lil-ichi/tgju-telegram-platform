# -*- coding: utf-8 -*-
"""tgju_core/scheduler.py — Async post scheduler (Telegram + Bale ticks).

Extracted verbatim from tgju_platform.py (lines 1971–2187): the post-type
rotation, the Telegram _scheduler_tick and scheduler_loop, plus the Bale
mirror tick (lines 2716–2787).  No behavior change.
"""
import asyncio
from datetime import datetime, timedelta

from tgju_engine_config import (load_channel_state, save_channel_state,  # noqa: E402
                                log_line)
from tgju_engine_orchestrator import build_for_channel  # noqa: E402
from tgju_core.runtime import RUNTIME  # noqa: E402
from tgju_core.settings import load_settings  # noqa: E402
from tgju_core.state import cached_rows, refresh_prices, reload_channels  # noqa: E402
from tgju_core.sender import send_telegram  # noqa: E402
from tgju_core.posting import post_channel_type  # noqa: E402


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
            # Fire ONE poll per interval window. Two-layer dedupe:
            #   a) last_poll_at within the window → skip (state merge fix in
            #      tgju_engine_news keeps this intact now)
            #   b) slot fingerprint: the window's start-hour is recorded as
            #      last_poll_slot ("day/hour"); even if timestamps get wiped,
            #      a poll can't repeat inside the same window because the
            #      slot only matches once per window.
            try:
                st = load_channel_state(c.get("id", ""))
                slot = "%s/%d" % (now.date().isoformat(), now.hour - now.hour % poll_iv)
                lp = st.get("last_poll_at")
                if st.get("last_poll_slot") == slot:
                    return_poll = False
                elif lp:
                    lp_dt = datetime.fromisoformat(lp)
                    if (now - lp_dt) < timedelta(hours=poll_iv):
                        return_poll = False
                    else:
                        return_poll = True
                else:
                    return_poll = True
                if return_poll:
                    return "poll"
            except Exception:
                pass  # dedupe failure must not block polling entirely
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
    pts = [p for p in _channel_post_types(c) if p != "poll"]
    if not pts:
        return "prices"
    if "all" in pts:
        return "all"
    # rotate by the hour so a multi-type channel alternates deterministically.
    # NOTE: polls are NOT part of the rotation — they are interval-driven
    # (functions.json → poll.interval_hours) and fire ONLY on boundary hours
    # via the dedupe above. A channel with post_types=[prices, poll] and a
    # 2-minute schedule used to send a poll every rotation tick (the
    # «3 polls in a row» bug): rotation must never produce polls.
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
            # Empty-cache guard: NEVER send a prices/analysis post built from
            # nothing (AI would produce «داده‌ای در دسترس نیست» filler, or the
            # build collapses to an empty message Telegram rejects). Wait for
            # data instead — the next tick retries.
            if not rows and post_type in ("prices", "analysis", "all"):
                log_line("scheduler skipped %s (%s): no price data available"
                         % (cid, post_type))
                continue
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
                if not ok and "empty" in str(detail).lower():
                    # empty build → don't count as a post; retry next tick
                    st["last_error"] = "پست خالی ساخته شد — ارسال نشد (تلاش بعدی)"
                    save_channel_state(cid, st)
                    log_line("scheduler skipped %s (%s): empty message"
                             % (cid, post_type))
                    continue
            if ok:
                st["last_post_at"] = now.isoformat(timespec="seconds")
                st["last_ok"] = now.isoformat(timespec="seconds")
                st["last_error"] = None
                st["last_type"] = post_type
                if post_type == "poll":
                    st["last_poll_at"] = now.isoformat(timespec="seconds")
                    st["last_poll_slot"] = "%s/%d" % (
                        now.date().isoformat(), now.hour - now.hour % max(1, int(
                            load_settings().get("poll_interval_hours", 4))))
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
