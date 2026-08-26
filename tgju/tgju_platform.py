# -*- coding: utf-8 -*-
"""TGJU Telegram Platform — webapp (port 8791) — thin entrypoint.

Persian RTL dashboard to manage channels, bot behavior, posts,
AI providers, and the async scheduler.

This file used to be a 2813-line monolith.  It has been refactored into
logical layers under tgju/tgju_core/ (zero behavior change):

    runtime.py     process-global RUNTIME dict + probe/UI caches
    settings.py    state/settings.json loader/saver + AI provider catalog
    state.py       channel cache + non-blocking price refresh pipeline
    sender.py      Telegram Bot API sending layer (send_telegram, poll)
    polls.py       engagement poll pool, store, pick_poll strategy
    posting.py     build_post_text + post_channel_type delivery
    status.py      /api/status, connection probes, health endpoints
    categories.py  channel categories (state/categories.json)
    scheduler.py   async scheduler loops (Telegram + Bale ticks)
    api_routes.py  every @app.route handler on an APIRouter

Construction order in this file mirrors the original monolith's:
1. legacy top-level imports (kept — other modules still use them lazily),
2. FastAPI app,
3. router + status endpoints attached,
4. startup event wiring the refresher + scheduler loops,
5. main() console entry point.

Backward compatibility: URLs, response shapes, error codes, the `app`
object, and the module-level names other files import from here
(RUNTIME, load_settings, cached_rows, get_bot_token, send_telegram, ...)
are unchanged.

Non-blocking design:
- /api/status serves from the in-memory cache instantly; it NEVER touches
  the network. A background refresher task (started at startup) re-fetches
  tgju.org every `fetch_ttl_seconds` (settings.json, default 60) and fills
  RUNTIME["last_rows"]/["last_fetch"].
- /api/refresh POST only *schedules* a background refresh and returns
  immediately — the dashboard never waits on tgju.org.
"""
import asyncio
import os
import sys
import threading

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles  # noqa: F401  (historical import)

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

# Legacy top-level imports kept so engine modules that do lazy
# `from tgju_platform import ...` keep working after the refactor.
from tgju_engine_config import (  # noqa: E402  # noqa: F401
    load_channels, save_channels, channel_state_path, load_channel_state,
    save_channel_state, log_line, LOG_PATH,
    load_slug_overrides, save_slug_overrides, rename_slug)
from tgju_engine_scrape import get_all_prices, fetch_html  # noqa: E402  # noqa: F401
from tgju_engine_orchestrator import build_for_channel  # noqa: E402  # noqa: F401
from tgju_engine_ai import (  # noqa: E402  # noqa: F401
    load_ai_config, save_ai_config, test_provider, route_category,
    auto_build_routing, run_analysis, load_ai_jobs, save_ai_jobs,
    record_ai_activity, _channel_domain)
from tgju_engine_format import SEP as _FORMAT_SEP, FA_WEEKDAYS, fa_num  # noqa: E402  # noqa: F401

# ── tgju_core layers ──────────────────────────────────────────────────────
from tgju_core.runtime import RUNTIME, CONN_PROBE_CACHE, UI_PAGE  # noqa: E402  # noqa: F401
from tgju_core.settings import (  # noqa: E402  # noqa: F401
    load_settings, save_settings, DEFAULT_SETTINGS, SETTINGS_PATH,
    PROVIDERS, MODEL_SUGGESTIONS)
from tgju_core.state import (  # noqa: E402  # noqa: F401
    get_channels, get_channel, reload_channels, cached_rows,
    background_refresh, refresh_prices, refresher_loop)
from tgju_core.sender import (  # noqa: E402  # noqa: F401
    get_bot_token, _tg_api_call, send_telegram, send_telegram_poll)
from tgju_core.polls import (  # noqa: E402  # noqa: F401
    POLL_POOL, load_poll_store, save_poll_store, get_poll_pool, pick_poll)
from tgju_core.posting import (  # noqa: E402  # noqa: F401
    _esc, build_post_text, _append_stale_notice, _stale_info, post_channel_type)
from tgju_core.status import (  # noqa: E402  # noqa: F401
    api_status, api_connections, api_connections_probe, api_health,
    api_secret_health)
from tgju_core.categories import (  # noqa: E402  # noqa: F401
    load_categories, save_categories, channel_category, CATEGORY_DEFAULTS,
    CATEGORIES_PATH)
from tgju_core.scheduler import (  # noqa: E402  # noqa: F401
    _channel_post_types, _next_post_type, _scheduler_tick, scheduler_loop)
from tgju_core.api_routes import router as api_router  # noqa: E402
from tgju_core.alias_routes import router as alias_router, resolver_loop
from tgju_core import auth  # noqa: E402
from tgju_core.auth import require_auth  # noqa: E402

app = FastAPI(title="TGJU Telegram Platform", version="1.1.0",
              docs_url=None, redoc_url=None, openapi_url=None)

# Attach every route handler (URLs/responses unchanged).
app.include_router(api_router)
app.include_router(alias_router)
# Status-family endpoints live in tgju_core/status.py; register them under
# the exact same URLs the monolith used.  They are registered on the app
# directly (NOT the router), so they get the auth guard explicitly here —
# otherwise they would bypass require_auth entirely.
_status_routes = [
    ("/api/status", api_status),
    ("/api/connections", api_connections),
    ("/api/connections/probe", api_connections_probe),
    ("/api/health", api_health),
    ("/api/secret_health", api_secret_health),
]
for _path, _fn in _status_routes:
    app.add_api_route(_path, _fn, methods=["GET"], include_in_schema=True,
                      dependencies=[Depends(require_auth)])
del _path, _fn, _status_routes


@app.on_event("startup")
async def startup():
    seeded = auth.ensure_default_admin()   # seed the login account (local or baked bootstrap)
    if not seeded:
        print("⚠️  TGJU Auth: no login account available.")
    else:
        print("TGJU Auth: login account ready.")
    RUNTIME["channels"] = load_channels()
    # warm the cache immediately (background), then keep it warm
    asyncio.create_task(refresher_loop())
    task = asyncio.create_task(scheduler_loop())
    RUNTIME["scheduler"] = task
    asyncio.create_task(resolver_loop())   # alias resolver — bounded, safe
    _start_bot_pollers()                   # بله/روبیکا/ایتا long-poll receivers


def _start_bot_pollers():
    """Start unified message pollers for Bale/Rubika/Eitaa (best-effort).

    Each platform only polls when its bot token is configured; handlers
    currently log + record activity so incoming messages are visible in
    the فعالیت‌ها feed. Transport lives in tgju_engine_polling.py.
    """
    try:
        import tgju_engine_polling as polling
    except Exception as e:
        print("polling engine unavailable: %s" % e)
        return

    def make_handler(platform):
        """Best-effort handler: appends to platform.log so the unified activity
        feed shows incoming messages with zero coupling to the event bus."""
        def handle(update):
            try:
                import datetime as _dt
                state_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
                msg = update.get("message") or update.get("update") or {}
                if isinstance(msg, dict):
                    chat = msg.get("chat", {})
                    if isinstance(chat, dict):
                        chat_id = chat.get("id", "")
                    else:
                        chat_id = msg.get("chat_id", "")
                    text = (msg.get("text") or "")[:80]
                else:
                    chat_id = ""
                    text = ""
                log_line = "%s [%s] inbound %s: %s" % (
                    _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    platform, chat_id, text)
                with open(os.path.join(state_dir, "platform.log"), "a",
                          encoding="utf-8") as f:
                    f.write(log_line + "\n")
            except Exception:
                pass  # logging is best-effort; never break the poller
        return handle

    try:
        started = polling.start_all(make_handler)
        n = sum(1 for p in started.values() if p._thread and p._thread.is_alive())
        print("TGJU polling: %d platform receiver(s) running (bale/rubika/eitaa)" % n)
    except Exception as e:
        print("polling start failed: %s" % e)


def main():
    """Console entry point (also used by the launcher scripts)."""
    import uvicorn
    print("TGJU Telegram Platform → http://localhost:8791")
    uvicorn.run(app, host="0.0.0.0", port=8791)


if __name__ == "__main__":
    main()