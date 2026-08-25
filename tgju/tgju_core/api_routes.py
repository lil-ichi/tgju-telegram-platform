# -*- coding: utf-8 -*-
"""tgju_core/api_routes.py — All FastAPI route handlers.

Extracted verbatim from the original tgju_platform.py monolith: every URL,
HTTP method, return shape and error code is identical.  Routes are defined
on a single APIRouter (decorators changed from @app.X to @router.X) that
tgju_platform.py attaches with `app.include_router(api_router)`.

The helper logic that used to live above the routes in the monolith
(connection probes, category CRUD, post-builders, status) now lives in
their own tgju_core sub-modules (status.py, categories.py, posting.py);
they are imported here so route bodies stay untouched.
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from tgju_engine_config import (  # noqa: E402
    load_channels, save_channels, channel_state_path, load_channel_state,
    save_channel_state, log_line, LOG_PATH,
    load_slug_overrides, save_slug_overrides, rename_slug)
from tgju_engine_scrape import get_all_prices, fetch_html, SLUG_ALIASES  # noqa: E402
from tgju_engine_orchestrator import build_for_channel  # noqa: E402
from tgju_engine_ai import (  # noqa: E402
    load_ai_config, save_ai_config, test_provider, route_category,
    auto_build_routing, run_analysis, load_ai_jobs, save_ai_jobs,
    record_ai_activity, _channel_domain)
from tgju_engine_format import SEP as _FORMAT_SEP, FA_WEEKDAYS, fa_num, TEMPLATE_DEFAULT  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from tgju_core.runtime import RUNTIME, CONN_PROBE_CACHE, UI_PAGE  # noqa: E402
from tgju_core.settings import (  # noqa: E402
    load_settings, save_settings, DEFAULT_SETTINGS, SETTINGS_PATH,
    PROVIDERS, MODEL_SUGGESTIONS)
from tgju_core.state import (  # noqa: E402
    get_channels, get_channel, reload_channels, cached_rows,
    background_refresh, refresh_prices)
from tgju_core.sender import (  # noqa: E402
    get_bot_token, _tg_api_call, send_telegram, send_telegram_poll)
from tgju_core.polls import (  # noqa: E402
    POLL_POOL, load_poll_store, save_poll_store, get_poll_pool, pick_poll)
from tgju_core.posting import (  # noqa: E402
    _esc, build_post_text, _append_stale_notice, _stale_info, post_channel_type)
from tgju_core.status import (  # noqa: E402
    api_status, api_connections, api_connections_probe, api_health, api_secret_health,
    _build_connection_probe)
from tgju_core.categories import (  # noqa: E402
    load_categories, save_categories, channel_category, CATEGORY_DEFAULTS,
    CATEGORIES_PATH)
from tgju_core import auth as auth  # noqa: E402

# Auth guard on EVERY /api/* route (the HTML shell at "/" stays public; the
# UI decides what to render from GET /api/auth/status).  require_auth itself
# exempts the /api/auth/* endpoints (PUBLIC_AUTH_PATHS in auth.py) because
# FastAPI merges router-level deps into every route and cannot opt out.
router = APIRouter(dependencies=[Depends(auth.require_auth)])

# ── Auth (login / logout / status) ────────────────────────────────────────
# Public by design (see PUBLIC_AUTH_PATHS in auth.py): the UI must be able
# to reach /api/auth/status before any login exists.  There is NO setup
# endpoint and NO default account — the single local credential is seeded
# into auth.json on first boot from state/auth.local.json or env vars, so
# only login/logout are needed.

@router.get("/api/auth/status", dependencies=[])
def api_auth_status(request: Request):
    """Public: tells the UI who is logged in."""
    username = auth.current_username(request)
    return {"authenticated": username is not None,
            "setup_complete": auth.setup_complete(),
            "username": username}

@router.post("/api/auth/login", dependencies=[])
async def api_auth_login(request: Request):
    """Validate credentials → 24h UUID session → tgju_session cookie.

    HttpOnly + SameSite=Lax always; Secure only when the request came over
    HTTPS (plain http://localhost and http://LAN-IP:8791 must keep working).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        return JSONResponse({"error": "username and password are required"},
                            status_code=400)
    if auth.is_locked(username):
        remaining = auth.lockout_remaining(username)
        return JSONResponse(
            {"error": "too many failed attempts",
             "detail": "try again in %d seconds" % remaining,
             "retry_after": remaining},
            status_code=429)
    if not auth.verify_credentials(username, password):
        auth.register_failure(username)
        if auth.is_locked(username):
            remaining = auth.lockout_remaining(username)
            return JSONResponse(
                {"error": "too many failed attempts",
                 "detail": "try again in %d seconds" % remaining,
                 "retry_after": remaining},
                status_code=429)
        return JSONResponse({"error": "invalid credentials"}, status_code=401)
    auth.clear_failures(username)
    token = auth.create_session(username)
    secure = auth._request_is_https(request)
    resp = JSONResponse({"ok": True, "username": username})
    resp.set_cookie(
        "tgju_session", token,
        max_age=auth.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return resp

@router.post("/api/auth/logout", dependencies=[])
async def api_auth_logout(request: Request):
    """Drop the session from active_sessions and clear the cookie."""
    token = request.cookies.get("tgju_session")
    if token:
        auth.destroy_session(token)
    secure = auth._request_is_https(request)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("tgju_session", path="/", secure=secure, httponly=True,
                       samesite="lax")
    return resp

@router.get("/api/types")
def api_types():
    return {"types": [
        {"id": "prices", "label": "جدول قیمت", "icon": "📊", "on": True},
        {"id": "news", "label": "خبر روز", "icon": "📰", "on": True},
        {"id": "poll", "label": "نظرسنجی", "icon": "🗳", "on": True},
        {"id": "analysis", "label": "تحلیل بازار", "icon": "📈", "on": True},
    ], "channel_types": {
        c["id"]: c.get("post_types", ["prices"])
        for c in get_channels()}}

@router.put("/api/types/{cid}")
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

@router.get("/api/channels")
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

@router.get("/api/templates")
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

@router.get("/api/categories")
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

@router.post("/api/categories")
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

@router.delete("/api/categories/{name}")
def api_categories_del(name: str):
    cats = load_categories()
    if name in cats:
        del cats[name]
        save_categories(cats)
    return {"ok": True}

# ── Slug data & manual overrides (دادهها و لینکها) ────────────────────────


def _clean_display_name(raw: str) -> str:
    """Extract the Persian display name from a TGJU <th> cell.

    Some homepage rows (bank_aud, bank_cad, …) embed onclick JS inside the
    <th>, so the scraped 'name' arrives as a JS blob with the real name at
    the end. Strategy: drop {...} blocks, treat tags as separators, keep the
    longest chunk that is purely Persian/space.
    """
    raw = raw or ""
    if "<" not in raw and "{" not in raw and '"' not in raw:
        return raw.strip()
    txt = re.sub(r"\{[^}]*\}", " ", raw)
    txt = re.sub(r"<[^>]+>", "\n", txt)
    chunks = re.split(r"[^\u0600-\u06FF\u200c ]+", txt)
    fa = [c.strip() for c in chunks if len(c.strip()) >= 2]
    if fa:
        return re.sub(r"\s+", " ", max(fa, key=len)).strip()
    return raw.strip()

@router.get("/api/slugs")
def api_slugs(scope: str = "all", q: str = ""):
    """Full slug inventory (scope=all, default) or channel-used only (scope=used).

    scope=all merges three sources into ONE table — the single source of
    truth for every messaging platform (Telegram/WhatsApp/Bale):
      1. live homepage rows  (RUNTIME cache — what tgju.org publishes now)
      2. channel slug pools  (channels.yaml + WhatsApp categories + Bale channels)
      3. manual overrides    (state/slug_overrides.json — win over both)

    Every row reports WHERE it is used across platforms and whether its
    value comes from the homepage, a profile backfill cache, disk fallback,
    or a manual override.
    """
    from tgju_engine_scrape import SLUG_ALIASES
    overrides = load_slug_overrides()
    rows = cached_rows()
    # removed_slugs: user-deleted slugs that must stay hidden even if the
    # homepage scrape re-publishes them (e.g. the 504409 numeric codes)
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "state", "removed_slugs.json"),
                  encoding="utf-8") as f:
            removed = set(json.load(f).get("removed", []))
    except Exception:
        removed = set()
    # verified alias map (slug → real tgju slug + last verified price):
    # a mapped slug is NEVER "missing" even when its 5-min profile cache
    # entry expired — the map IS the durable source record.
    try:
        from tgju_core.alias_resolver import load_alias_map
        alias_map = load_alias_map()
    except Exception:
        alias_map = {}
    profile_cache = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "state", "profile_cache.json"),
                  encoding="utf-8") as f:
            profile_cache = json.load(f) or {}
    except Exception:
        pass

    # ── platform usage map ────────────────────────────────────────────
    usage = {}          # slug -> per-platform channel lists
    def _use(s, plat, cid):
        u = usage.setdefault(s, {"telegram": [], "whatsapp": [], "bale": [],
                                 "rubika": [], "eitaa": []})
        if cid not in u[plat]:
            u[plat].append(cid)

    chans = get_channels()
    for c in chans:
        for s in list(c.get("slugs") or []):
            _use(s, "telegram", c["id"])
        for slugs in (c.get("slug_groups") or {}).values():
            for s in slugs:
                _use(s, "telegram", c["id"])
    try:
        from tgju_engine_whatsapp import list_categories
        for cat in list_categories() or []:
            for slugs in (cat.get("slug_groups") or {}).values():
                for s in slugs:
                    _use(s, "whatsapp", cat.get("label") or cat.get("id", ""))
    except Exception:
        pass
    try:
        import tgju_engine_rubika as rub
        for rc in rub.list_channels() or []:
            for s in list(rc.get("slugs") or []):
                _use(s, "rubika", rc.get("name") or rc.get("id"))
            for slugs in (rc.get("slug_groups") or {}).values():
                for s in slugs:
                    _use(s, "rubika", rc.get("name") or rc.get("id"))
    except Exception:
        pass
    try:
        import tgju_engine_eitaa as eit
        for ec in eit.list_channels() or []:
            for s in list(ec.get("slugs") or []):
                _use(s, "eitaa", ec.get("name") or ec.get("id"))
            for slugs in (ec.get("slug_groups") or {}).values():
                for s in slugs:
                    _use(s, "eitaa", ec.get("name") or ec.get("id"))
    except Exception:
        pass
    try:
        from tgju_engine_bale import load_config as bale_load
        for bc in ((bale_load() or {}).get("channels") or []):
            for s in list(bc.get("slugs") or []):
                _use(s, "bale", bc.get("id") or bc.get("name", ""))
            for slugs in (bc.get("slug_groups") or {}).values():
                for s in slugs:
                    _use(s, "bale", bc.get("id") or bc.get("name", ""))
    except Exception:
        pass

    # ── merged universe of slugs ──────────────────────────────────────
    universe = set(rows.keys()) | set(usage.keys()) | set(overrides.keys())
    # user-deleted slugs stay hidden (homepage may still publish them)
    universe -= removed
    if scope == "used":
        universe = set(usage.keys())
        universe -= removed

    needle = (q or "").strip().lower()
    out = []
    for s in sorted(universe):
        row = rows.get(s) or {}
        ov = overrides.get(s) or {}
        pc = profile_cache.get(s) or {}
        name = ov.get("name") or row.get("name") or pc.get("name") or s
        name = _clean_display_name(name)
        price = row.get("price") or pc.get("price") or ""
        source = "homepage" if row.get("price") else (
            "profile" if pc.get("price") else "missing")
        am = alias_map.get(s) or {}
        if not price and am.get("price"):
            # mapped + last-verified price from the resolver (durable)
            price = str(am["price"])
            source = "alias"
        if ov.get("manual_price"):
            price = str(ov["manual_price"]).replace(",", "").strip()
            source = "manual"
        u = usage.get(s) or {}
        plats = [p for p in ("telegram", "whatsapp", "bale", "rubika", "eitaa") if u.get(p)]
        item = {
            "slug": s,
            "name": name,
            "homepage_price": row.get("price") or "",
            "price": price,
            "change_pct": row.get("change_pct") or "",
            "dir": row.get("dir") or "",
            "source": source,
            "manual": bool(ov.get("manual_price")),
            "manual_price": ov.get("manual_price") or "",
            "override": ov,
            "unit": ov.get("unit", ""),
            "unit_auto": _auto_unit(s),
            "profile_url": ov.get("profile_url") or "",
            "ai_log": ov.get("ai_log") or "",
            "alias": SLUG_ALIASES.get(s, ""),
            "platforms": plats,
            "usage": u,
        }
        if needle and needle not in s.lower() and needle not in str(name).lower():
            continue
        out.append(item)
    used_count = sum(1 for s in universe if s in usage)
    return {"slugs": out, "overrides": overrides,
            "total": len(out), "used": used_count,
            "with_price": sum(1 for i in out if i["price"])}

@router.post("/api/slugs/add")
async def api_slug_add(req: Request):
    """Add a NEW slug/data source to the inventory.

    Body: {slug, name?, profile_url?}. The slug is stored as an override so
    it appears in the داده‌ها و لینک‌ها table immediately; if it has no
    homepage price, the resolver/backfill will try to fetch its profile.
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    slug = str(body.get("slug") or "").strip().lower()
    if not slug or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", slug):
        return JSONResponse({"error": "اسلاگ نامعتبر است"}, status_code=400)
    # (re-)adding clears any prior user-removal
    try:
        rs_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "state", "removed_slugs.json")
        with open(rs_path, encoding="utf-8") as f:
            rs = json.load(f) or {}
        if slug in list(rs.get("removed") or []):
            rs["removed"] = [x for x in rs["removed"] if x != slug]
            tmp = rs_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rs, f, ensure_ascii=False, indent=1)
            os.replace(tmp, rs_path)
    except Exception:
        pass
    overrides = load_slug_overrides()
    ov = dict(overrides.get(slug) or {})
    name = str(body.get("name") or "").strip()
    purl = str(body.get("profile_url") or "").strip()
    if name:
        ov["name"] = name
    if purl:
        ov["profile_url"] = purl
    overrides[slug] = ov
    save_slug_overrides(overrides)
    # try to warm a price right away (bounded, one fetch)
    warmed = False
    if not ov.get("manual_price"):
        try:
            from tgju_core.alias_resolver import resolve_slug, warm_profile_cache
            res = await asyncio.to_thread(resolve_slug, slug, name)
            if res.get("price"):
                await asyncio.to_thread(
                    warm_profile_cache, slug, res["price"], name)
                warmed = True
        except Exception:
            pass
    return {"ok": True, "slug": slug, "warmed": warmed}


@router.delete("/api/slugs/remove/{slug}")
async def api_slug_remove(slug: str, purge: bool = True):
    """Remove a slug from the inventory and (default) from every channel."""
    from tgju_engine_config import save_channels
    removed_from = []
    overrides = load_slug_overrides()
    overrides.pop(slug, None)
    save_slug_overrides(overrides)
    # remember removal so homepage re-publish doesn't resurrect it
    try:
        rs_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "state", "removed_slugs.json")
        with open(rs_path, encoding="utf-8") as f:
            rs = json.load(f) or {}
        lst = list(rs.get("removed") or [])
        if slug not in lst:
            lst.append(slug)
        rs["removed"] = lst
        tmp = rs_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rs, f, ensure_ascii=False, indent=1)
        os.replace(tmp, rs_path)
    except Exception:
        pass

    def _strip(channels, saver):
        changed = False
        for c in channels:
            slugs = list(c.get("slugs") or [])
            if slug in slugs:
                slugs.remove(slug)
                c["slugs"] = slugs
                changed = True
            groups = c.get("slug_groups") or {}
            for g, lst in list(groups.items()):
                if slug in (lst or []):
                    groups[g] = [x for x in lst if x != slug]
                    changed = True
        if changed:
            saver(channels)
            return True
        return False

    try:
        chans = get_channels()
        if _strip(chans, save_channels):
            RUNTIME["channels"] = chans
            removed_from.append("telegram")
    except Exception:
        pass
    try:
        from tgju_engine_whatsapp import list_categories, save_categories
        cats = list_categories()
        changed = False
        for cat in cats:
            for g, lst in ((cat.get("slug_groups") or {}).items()):
                if slug in (lst or []):
                    cat["slug_groups"][g] = [x for x in lst if x != slug]
                    changed = True
        if changed:
            save_categories(cats)
            removed_from.append("whatsapp")
    except Exception:
        pass
    try:
        import tgju_engine_bale as bale
        cfg = bale.load_bale()
        changed = False
        for bc in (cfg.get("channels") or []):
            if slug in list(bc.get("slugs") or []):
                bc["slugs"] = [x for x in bc["slugs"] if x != slug]
                changed = True
            for g, lst in ((bc.get("slug_groups") or {}).items()):
                if slug in (lst or []):
                    bc["slug_groups"][g] = [x for x in lst if x != slug]
                    changed = True
        if changed:
            bale.save_bale(cfg)
            removed_from.append("bale")
    except Exception:
        pass
    try:
        import tgju_engine_rubika as rub
        data = rub.load_rubika() if hasattr(rub, "load_rubika") else {}
        changed = False
        for rc in (data.get("channels") or []):
            if slug in list(rc.get("slugs") or []):
                rc["slugs"] = [x for x in rc["slugs"] if x != slug]
                changed = True
            for g, lst in ((rc.get("slug_groups") or {}).items()):
                if slug in (lst or []):
                    rc["slug_groups"][g] = [x for x in lst if x != slug]
                    changed = True
        if changed:
            rub.save_rubika(data)
            removed_from.append("rubika")
    except Exception:
        pass
    try:
        import tgju_engine_eitaa as eit
        data = eit.load_eitaa() if hasattr(eit, "load_eitaa") else {}
        changed = False
        for ec in (data.get("channels") or []):
            if slug in list(ec.get("slugs") or []):
                ec["slugs"] = [x for x in ec["slugs"] if x != slug]
                changed = True
            for g, lst in ((ec.get("slug_groups") or {}).items()):
                if slug in (lst or []):
                    ec["slug_groups"][g] = [x for x in lst if x != slug]
                    changed = True
        if changed:
            eit.save_eitaa(data)
            removed_from.append("eitaa")
    except Exception:
        pass
    # drop any cached price so it stops showing in posts immediately
    try:
        pc_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "state", "profile_cache.json")
        with open(pc_path, encoding="utf-8") as f:
            pc = json.load(f) or {}
        if slug in pc:
            del pc[slug]
            tmp = pc_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(pc, f, ensure_ascii=False)
            os.replace(tmp, pc_path)
    except Exception:
        pass
    return {"ok": True, "removed": slug, "purged_from": removed_from}


def _auto_unit(slug: str) -> str:
    """Convention unit for a slug (what fmt/slug_unit would pick with no override)."""
    try:
        from tgju_engine_format import slug_unit
        return slug_unit(slug, {}) or ""
    except Exception:
        return ""

@router.put("/api/slugs/{slug}")
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

@router.delete("/api/slugs/{slug}")
def api_slug_override_del(slug: str):
    overrides = load_slug_overrides()
    if slug in overrides:
        del overrides[slug]
        save_slug_overrides(overrides)
    return {"ok": True}

@router.post("/api/slugs/rename")
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

@router.post("/api/slugs/test")
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

@router.get("/api/preview/{cid}")
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

@router.post("/api/channels")
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

@router.put("/api/channels/{cid}")
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

@router.delete("/api/channels/{cid}")
def api_delete_channel(cid: str):
    chans = get_channels()
    chans = [c for c in chans if c.get("id") != cid]
    RUNTIME["channels"] = chans
    save_channels(chans)
    reload_channels()
    return {"ok": True}

@router.post("/api/post/{cid}")
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

@router.post("/api/refresh")
async def api_refresh():
    # trigger a background refresh and return immediately — the dashboard
    # never blocks on tgju.org
    await background_refresh(force=True)
    return {"refreshing": True, "rows": len(cached_rows()),
            "last_fetch": str(RUNTIME["last_fetch"]) if RUNTIME["last_fetch"] else None}

# ── Settings ──────────────────────────────────────────────────────────────

@router.get("/api/settings")
def api_settings_get():
    return load_settings()

@router.put("/api/settings")
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

@router.get("/api/providers")
def api_providers():
    return {"providers": PROVIDERS, "models": MODEL_SUGGESTIONS}

@router.get("/api/logs")
def api_logs():
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            return HTMLResponse("<pre>" + f.read()[-8000:] + "</pre>")
    except Exception:
        return HTMLResponse("<pre>(خالی)</pre>")

@router.get("/api/polls")
def api_polls_get():
    """Poll store: shared pool + per-channel fixed polls."""
    store = load_poll_store()
    return {
        "questions": store.get("questions") or [],
        "fixed": store.get("fixed") or {},
        "builtin_count": len(POLL_POOL),
    }

@router.post("/api/polls")
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

@router.put("/api/polls/{index}")
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

@router.delete("/api/polls/{index}")
def api_polls_del(index: int):
    """Delete a shared-pool poll at `index` (0-based)."""
    store = load_poll_store()
    qs = store.setdefault("questions", [])
    if 0 <= index < len(qs):
        del qs[index]
        save_poll_store(store)
    return {"ok": True, "polls": store}

@router.post("/api/polls/delete-fixed")
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

@router.post("/api/polls/generate")
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

@router.get("/api/ai")
def api_ai_get():
    cfg = load_ai_config()
    return cfg

@router.post("/api/ai/providers")
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

@router.post("/api/ai/models")
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

@router.delete("/api/ai/providers/{name}")
def api_ai_del_provider(name: str):
    cfg = load_ai_config()
    if name in cfg["providers"]:
        del cfg["providers"][name]
        save_ai_config(cfg)
    return {"ok": True}

@router.post("/api/ai/test/{name}")
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

@router.post("/api/ai/run/{cid}")
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

@router.post("/api/ai/channel/{cid}")
async def api_ai_channel_set(cid: str, req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    cfg = load_ai_config()
    cfg.setdefault("channels", {})[cid] = body
    save_ai_config(cfg)
    return {"ok": True}

@router.post("/api/ai/routing/build")
def api_ai_routing_build():
    """Auto-derive category→channels routing from news_categories."""
    cfg = load_ai_config()
    routing = auto_build_routing(get_channels(), cfg)
    save_ai_config(cfg)
    return {"ok": True, "routing": routing}

@router.get("/api/ai/routing")
def api_ai_routing_get():
    cfg = load_ai_config()
    return {"routing": cfg.get("routing", {}),
            "channels": [{"id": c.get("id"), "name": c.get("name"),
                          "icon": c.get("icon", ""),
                          "news_categories": c.get("news_categories", [])}
                         for c in get_channels()]}

# ── Bot Profile API ──────────────────────────────────────────────────────

@router.get("/api/bot")
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

@router.post("/api/bot")
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

@router.post("/api/bot/activate/{bot_id}")
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

@router.delete("/api/bot/{bot_id}")
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

@router.post("/api/bot/test")
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

@router.get("/api/bot/status")
def api_bot_status():
    """Quick status: is a bot token available?"""
    tok = get_bot_token()
    return {"token_set": bool(tok), "token_preview": (tok[:8] + "...") if tok else ""}

# ── Functions API ────────────────────────────────────────────────────────

@router.get("/api/functions")
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

@router.put("/api/functions")
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

@router.post("/api/functions/analysis/run/{cid}")
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

@router.get("/api/ai/orchestrator")
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

@router.post("/api/ai/jobs/{job_id}")
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
              "default_count", "channels", "effort"):
        if k in body:
            job[k] = body[k]
    save_ai_jobs({"jobs": data["jobs"], "activity": data["activity"]})
    return {"ok": True, "job": job}

@router.post("/api/ai/jobs/{job_id}/run")
async def api_ai_job_run(job_id: str):
    """Run one job now (analysis for all/selected channels)."""
    data = load_ai_jobs()
    if job_id not in data["jobs"]:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    if job_id == "slug_repair":
        # delegate to the alias-resolver's bounded pass
        from tgju_core.alias_routes import resolver_pass
        res = await asyncio.to_thread(resolver_pass)
        return res
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

@router.get("/api/command_center")
def api_command_center():
    """Aggregated Command Center data: health, runs, approvals, next actions."""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        rows = RUNTIME["last_rows"] or {}
        return ci.command_center(get_channels(), rows)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.get("/api/explain/{run_id}")
def api_explain(run_id: str):
    """Explainability: why did this run happen / post get published?"""
    try:
        import tgju_core_integration as ci
        ci.init_core()
        return ci.explain_run(run_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.get("/api/runs")
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

@router.get("/api/events")
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



# ── Unified activity feed (all platforms) ─────────────────────────────────
ACTIVITY_PATTERNS = [
    # (regex, category, icon)
    (r"scheduler posted (\S+) \((\w+)\)", "telegram", "📤"),
    (r"webapp posted (\S+) \((\w+)\)", "telegram", "📤"),
    (r"scheduler skipped (\S+) \((\w+)\)", "skip", "⏭"),
    (r"scheduler ERROR (\S+) \((\w+)\): (.*)", "error", "❌"),
    (r"scheduler ERROR (\S+): (.*)", "error", "❌"),
    (r"bale scheduler posted (\S+) \((\w+)\)", "bale", "🟢"),
    (r"bale scheduler ERROR (\S+) \((\w+)\): (.*)", "error", "❌"),
    (r"whatsapp sent to (\S+)", "whatsapp", "💬"),
    (r"whatsapp broadcast.*?(\d+) users?", "whatsapp", "💬"),
    (r"slug override saved: (\S+)", "config", "✏️"),
    (r"slug renamed: (\S+) -> (\S+)", "config", "🔄"),
    (r"channel (\S+) saved", "config", "💾"),
    (r"TGJU unreachable.*?(\d+) fallback prices", "data", "⚠️"),
    (r"refresh failed: (.*)", "data", "⚠️"),
]

def _classify_log(msg: str):
    """Map a platform.log line to (category, icon, action, channel_id, detail).

    action is a short machine-readable verb for the UI (post/skip/error/…);
    detail carries the regex captures (channel, post_type, error text).
    """
    for pat, cat, icon in ACTIVITY_PATTERNS:
        m = re.search(pat, msg)
        if m:
            g = m.groups()
            cid = ""
            action = cat
            detail = ""
            first = str(g[0]) if g else ""
            if re.match(r"(ch\d+|wa\d+|bale\w*|\+\d+)", first):
                cid = first
                if len(g) > 1 and isinstance(g[1], str):
                    action = {"prices": "قیمت", "news": "خبر", "poll": "نظرسنجی",
                              "analysis": "تحلیل", "all": "همه"}.get(g[1], g[1])
                    if len(g) > 2 and g[2]:
                        detail = str(g[2])[:200]
            elif "unreachable" in msg or "refresh failed" in msg:
                detail = msg[:200]
            return cat, icon, action, cid, detail
    if "auth:" in msg:
        return None, "", "", "", ""      # auth noise → hide
    return "system", "•", "log", "", msg[:200]

@router.get("/api/activity")
def api_activity(limit: int = 150, category: str = ""):
    """Unified activity feed across ALL platforms.

    Merges three sources, newest first:
      1. structured events  (tgju_core event bus — runs with full pipeline)
      2. run records        (orchestrated posts)
      3. platform.log       (scheduler/webapp/bale/whatsapp/AI/config lines)
    Filterable by category: telegram|whatsapp|bale|ai|config|data|error|skip.
    """
    items = []
    # 1) structured events (in-memory bus)
    try:
        import tgju_core_integration as ci
        ci.init_core()
        from tgju_core.events import get_event_bus
        for e in get_event_bus().query_events(limit=100):
            payload = getattr(e, "payload", None) or {}
            pay_str = ""
            try:
                pay_str = json.dumps(payload, ensure_ascii=False)[:300]
            except Exception:
                pass
            items.append({
                "ts": e.timestamp.isoformat(timespec="seconds"),
                "source": "core",
                "category": "event",
                "icon": "⚡",
                "action": str(getattr(e.event_type, "value", e.event_type)),
                "title": str(getattr(e.event_type, "value", e.event_type)) +
                         (f" — {e.channel_id}" if e.channel_id and e.channel_id != "*" else ""),
                "raw": pay_str,
                "channel_id": e.channel_id or "",
                "detail": e.error or (f"مدت: {e.duration_ms}ms" if getattr(e, 'duration_ms', None) else ""),
                "status": e.status or "",
            })
    except Exception:
        pass

    # 2) platform.log — the complete cross-platform history
    try:
        from tgju_engine_config import LOG_PATH
        import os as _os
        max_bytes = 400_000
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            lines = f.read().splitlines()
        for line in reversed(lines):                      # newest first
            m = re.match(r"\[([^\]]+)\] (.*)", line)
            if not m:
                continue
            ts, msg = m.group(1), m.group(2).strip()
            cat, icon, action, cid, extra = _classify_log(msg)
            if cat is None:
                continue
            title = msg
            if len(title) > 140:
                title = title[:137] + "…"
            items.append({
                "ts": ts.replace("T", " "),
                "source": "log",
                "category": cat,
                "icon": icon,
                "action": action,
                "title": title,
                "raw": msg,
                "channel_id": cid,
                "detail": extra,
                "status": "failed" if cat == "error" else ("skipped" if cat == "skip" else "success"),
            })
    except Exception:
        pass

    if category:
        items = [i for i in items if i["category"] == category]
    items.sort(key=lambda i: i["ts"], reverse=True)

    # dedupe identical consecutive entries (log repeats from retries)
    seen = set()
    out = []
    for i in items:
        key = (i["ts"], i["title"])
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return {"activity": out[:limit], "total": len(out)}



@router.get("/api/channels/{cid}/style")
def api_channel_style_get(cid: str):
    """Current tag-style templates for a channel (merged with defaults)."""
    ch = next((c for c in get_channels() if c["id"] == cid), None)
    if not ch:
        return JSONResponse({"error": "channel not found"}, status_code=404)
    from tgju_engine_format import get_style, STYLE_DEFAULTS
    return {"style": get_style(ch), "defaults": STYLE_DEFAULTS,
            "footer": ch.get("footer") or "", "with_footer": ch.get("with_footer", True)}

@router.put("/api/channels/{cid}/style")
async def api_channel_style_put(cid: str, req: Request):
    """Save tag-style templates onto the channel (style:{...} in channels.yaml).

    Only the five editable keys are accepted: rows/weekday/time/sep/star.
    Empty strings reset that tag to its default. news style stays global
    for now (news_line is built platform-wide).
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    chans = get_channels()
    ch = next((c for c in chans if c["id"] == cid), None)
    if not ch:
        return JSONResponse({"error": "channel not found"}, status_code=404)
    from tgju_engine_format import STYLE_DEFAULTS
    user_style = dict(ch.get("style") or {})
    changed = False
    for k in ("rows", "weekday", "time", "sep", "star"):
        if k in body:
            v = str(body[k] or "").strip()
            if not v or v == STYLE_DEFAULTS[k]:
                user_style.pop(k, None)          # reset to default
            else:
                # basic sanity: must keep the required sub-var so output isn't blank
                need = {"rows": "{price}", "weekday": "{weekday}", "time": "{time}",
                        "star": "{star_name}"}.get(k)
                if need and need not in v:
                    return JSONResponse(
                        {"error": f"الگوی {{ {k} }} باید شامل {need} باشد"},
                        status_code=400)
                user_style[k] = v
            changed = True
    if changed:
        ch["style"] = user_style
        save_channels(get_channels())
        reload_channels()
        log_line(f"channel style saved: {cid} keys={sorted(user_style.keys())}")
    return {"ok": True, "style": ch.get("style") or {}}

@router.post("/api/simulate/{cid}")
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

@router.get("/api/simulate_all")
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

@router.get("/api/approvals")
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

@router.post("/api/approvals/{approval_id}/{action}")
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



# ── Rubika platform (Bot API v3) ──────────────────────────────────────────
@router.get("/api/rubika")
def api_rubika_config():
    """Rubika full config: settings + channels (token masked)."""
    try:
        import tgju_engine_rubika as rub
        data = rub.load_rubika()
        s = dict(data["settings"])
        if s.get("access_token"):
            s["access_token"] = "***"
        return {"settings": s, "channels": data["channels"], "mock": rub.is_mock()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/rubika/settings")
async def api_rubika_settings(req: Request):
    try:
        import tgju_engine_rubika as rub
        body = await req.json()
        data = rub.load_rubika()
        s = data["settings"]
        for k in ("access_token", "auto_post"):
            if k in body:
                s[k] = body[k]
        rub.save_rubika(data)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/rubika/test")
async def api_rubika_test():
    try:
        import tgju_engine_rubika as rub
        r = await asyncio.to_thread(rub.test_credentials)
        if r.get("ok"):
            bot = r.get("bot") or {}
            return {"ok": True, "mock": bool(r.get("mock")),
                    "username": bot.get("username") or "(ثبت شد)"}
        return JSONResponse({"ok": False, "error": r.get("error", "test failed")},
                            status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.get("/api/rubika/preview/{cid}")
def api_rubika_preview(cid: str, post_type: str = "prices"):
    try:
        import tgju_engine_rubika as rub
        ch = next((c for c in rub.list_channels() if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown rubika channel %s" % cid},
                                status_code=404)
        text = rub.preview_channel(ch, post_type)
        return {"ok": True, "preview": text, "type": post_type}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/rubika/post/{cid}")
async def api_rubika_post(cid: str, req: Request):
    try:
        import tgju_engine_rubika as rub
        body = await req.json()
        post_type = (body or {}).get("post_type", "prices")
        ch = next((c for c in rub.list_channels() if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown rubika channel"}, status_code=404)
        resp = await asyncio.to_thread(rub.post_channel, ch, post_type)
        return resp if resp.get("ok") else JSONResponse(resp, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/rubika/channels")
async def api_rubika_new_channel(req: Request):
    try:
        import tgju_engine_rubika as rub
        body = await req.json()
        chans = rub.list_channels()
        cid = "r%d" % (len(chans) + 1)
        ch = rub.normalize_channel({
            "id": cid,
            "name": (body.get("name") or "").strip() or "کانال روبیکا",
            "chat_id": (body.get("chat_id") or "").strip(),
            "enabled": bool(body.get("enabled", True)),
            "schedule_minutes": int(body.get("schedule_minutes") or 30),
            "icon": (body.get("icon") or "🟣").strip(),
            "header": (body.get("header") or "").strip(),
            "section_title": (body.get("section_title") or "قیمت‌ها").strip(),
            "footer": (body.get("footer") or "به‌روزرسانی: هر ۳۰ دقیقه | منبع: tgju.org").strip(),
            "with_footer": bool(body.get("with_footer", True)),
            "with_star": bool(body.get("with_star", True)),
            "with_analysis": bool(body.get("with_analysis", True)),
            "format": body.get("format") or "chips",
            "template": body.get("template") or "",
            "news_categories": body.get("news_categories") or [],
            "analysis_tags": body.get("analysis_tags") or [],
            "slug_groups": body.get("slug_groups") or {},
            "slugs": body.get("slugs") or [],
            "post_types": [p for p in (body.get("post_types") or ["prices"]) if p != "poll"],
        })
        chans.append(ch)
        rub.save_channels(chans)
        return {"ok": True, "channel": ch}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.put("/api/rubika/channels/{cid}")
async def api_rubika_update_channel(cid: str, req: Request):
    try:
        import tgju_engine_rubika as rub
        body = await req.json()
        chans = rub.list_channels()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown channel"}, status_code=404)
        for k in ("name", "chat_id", "icon", "header", "section_title", "footer",
                  "template", "post_types", "slug_groups", "slugs", "format",
                  "news_categories", "analysis_tags"):
            if k in body:
                ch[k] = body[k]
        if "enabled" in body:
            ch["enabled"] = bool(body["enabled"])
        if "schedule_minutes" in body:
            ch["schedule_minutes"] = int(body["schedule_minutes"] or 30)
        for b in ("with_footer", "with_star", "with_analysis"):
            if b in body:
                ch[b] = bool(body[b])
        rub.save_channels(chans)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.delete("/api/rubika/channels/{cid}")
def api_rubika_delete_channel(cid: str):
    try:
        import tgju_engine_rubika as rub
        chans = [c for c in rub.list_channels() if c.get("id") != cid]
        rub.save_channels(chans)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Eitaa platform (EitaaYar API) ─────────────────────────────────────────
@router.get("/api/eitaa")
def api_eitaa_config():
    try:
        import tgju_engine_eitaa as eit
        data = eit.load_eitaa()
        s = dict(data["settings"])
        if s.get("access_token"):
            s["access_token"] = "***"
        return {"settings": s, "channels": data["channels"], "mock": eit.is_mock()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/eitaa/settings")
async def api_eitaa_settings(req: Request):
    try:
        import tgju_engine_eitaa as eit
        body = await req.json()
        data = eit.load_eitaa()
        s = data["settings"]
        for k in ("access_token", "auto_post"):
            if k in body:
                s[k] = body[k]
        eit.save_eitaa(data)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/eitaa/test")
async def api_eitaa_test():
    try:
        import tgju_engine_eitaa as eit
        r = await asyncio.to_thread(eit.test_credentials)
        if r.get("ok"):
            bot = r.get("bot") or {}
            return {"ok": True, "mock": bool(r.get("mock")),
                    "username": bot.get("username") or "(ثبت شد)"}
        return JSONResponse({"ok": False, "error": r.get("error", "test failed")},
                            status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.get("/api/eitaa/preview/{cid}")
def api_eitaa_preview(cid: str, post_type: str = "prices"):
    try:
        import tgju_engine_eitaa as eit
        ch = next((c for c in eit.list_channels() if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown eitaa channel %s" % cid},
                                status_code=404)
        text = eit.preview_channel(ch, post_type)
        return {"ok": True, "preview": text, "type": post_type}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/eitaa/post/{cid}")
async def api_eitaa_post(cid: str, req: Request):
    try:
        import tgju_engine_eitaa as eit
        body = await req.json()
        post_type = (body or {}).get("post_type", "prices")
        ch = next((c for c in eit.list_channels() if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown eitaa channel"}, status_code=404)
        resp = await asyncio.to_thread(eit.post_channel, ch, post_type)
        return resp if resp.get("ok") else JSONResponse(resp, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/eitaa/channels")
async def api_eitaa_new_channel(req: Request):
    try:
        import tgju_engine_eitaa as eit
        body = await req.json()
        chans = eit.list_channels()
        cid = "e%d" % (len(chans) + 1)
        ch = eit.normalize_channel({
            "id": cid,
            "name": (body.get("name") or "").strip() or "کانال ایتا",
            "chat_id": (body.get("chat_id") or "").strip(),
            "enabled": bool(body.get("enabled", True)),
            "schedule_minutes": int(body.get("schedule_minutes") or 30),
            "icon": (body.get("icon") or "🟠").strip(),
            "slug_groups": body.get("slug_groups") or {},
            "slugs": body.get("slugs") or [],
                        "header": (body.get("header") or "").strip(),
            "section_title": (body.get("section_title") or "قیمت‌ها").strip(),
            "footer": (body.get("footer") or "به‌روزرسانی: هر ۳۰ دقیقه | منبع: tgju.org").strip(),
            "with_footer": bool(body.get("with_footer", True)),
            "with_star": bool(body.get("with_star", True)),
            "with_analysis": bool(body.get("with_analysis", True)),
            "format": body.get("format") or "chips",
            "template": body.get("template") or "",
            "news_categories": body.get("news_categories") or [],
            "analysis_tags": body.get("analysis_tags") or [],
            "post_types": [p for p in (body.get("post_types") or ["prices"]) if p != "poll"],
        })
        chans.append(ch)
        eit.save_channels(chans)
        return {"ok": True, "channel": ch}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.put("/api/eitaa/channels/{cid}")
async def api_eitaa_update_channel(cid: str, req: Request):
    try:
        import tgju_engine_eitaa as eit
        body = await req.json()
        chans = eit.list_channels()
        ch = next((c for c in chans if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown channel"}, status_code=404)
        for k in ("name", "chat_id", "icon", "header", "section_title", "footer",
                  "template", "post_types", "slug_groups", "slugs", "format",
                  "news_categories", "analysis_tags"):
            if k in body:
                ch[k] = body[k]
        if "enabled" in body:
            ch["enabled"] = bool(body["enabled"])
        if "schedule_minutes" in body:
            ch["schedule_minutes"] = int(body["schedule_minutes"] or 30)
        for b in ("with_footer", "with_star", "with_analysis"):
            if b in body:
                ch[b] = bool(body[b])
        eit.save_channels(chans)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.delete("/api/eitaa/channels/{cid}")
def api_eitaa_delete_channel(cid: str):
    try:
        import tgju_engine_eitaa as eit
        chans = [c for c in eit.list_channels() if c.get("id") != cid]
        eit.save_channels(chans)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.get("/api/platforms")
def api_platforms():
    """Registry of manageable platforms (for the control center header)."""
    return {"platforms": [
        {"id": "telegram", "name": "تلگرام", "icon": "✈️",
         "description": "کانال‌ها، ربات‌ها و ارسال خودکار تلگرام"},
        {"id": "whatsapp", "name": "واتساپ", "icon": "💬",
         "description": "شماره‌ها و ارسال خودکار واتساپ (Meta Cloud API)"},
        {"id": "bale", "name": "بله", "icon": "✅",
         "description": "کانال‌ها و ارسال خودکار بله (Bale)"},
        {"id": "rubika", "name": "روبیکا", "icon": "🟣",
         "description": "کانال‌ها و ارسال خودکار روبیکا (Bot API v3)"},
        {"id": "eitaa", "name": "ایتا", "icon": "🟠",
         "description": "کانال‌ها و ارسال خودکار ایتا (EitaaYar API)"},
    ]}

# ── WhatsApp interactive bot (Meta Cloud API) ─────────────────────────────

@router.get("/api/whatsapp")
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

@router.post("/api/whatsapp/settings")
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

@router.post("/api/whatsapp/test")
def api_whatsapp_test():
    try:
        import tgju_engine_whatsapp as wa
        return wa.test_credentials()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.get("/api/whatsapp/categories")
def api_whatsapp_categories():
    """Bot menu categories (each with id/label/menu_code/slug_groups)."""
    try:
        import tgju_engine_whatsapp as wa
        return {"categories": wa.get_categories()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/whatsapp/categories")
async def api_whatsapp_save_categories(req: Request):
    try:
        import tgju_engine_whatsapp as wa
        body = await req.json()
        cats = body.get("categories") or []
        wa.save_categories(cats)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/whatsapp/broadcast")
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

@router.post("/api/whatsapp/simulate")
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

@router.get("/api/whatsapp/status")
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

@router.get("/api/whatsapp/webhook")
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

@router.post("/api/whatsapp/webhook")
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

@router.get("/api/bale")
def api_bale_config():
    """Bale full config: settings + channels (token masked)."""
    try:
        import tgju_engine_bale as bale
        data = bale.load_bale()
        s = dict(data["settings"])
        if s.get("access_token"):
            s["access_token"] = "***"
        chans = []
        for c in data["channels"]:
            cc = dict(c)
            cc.setdefault("chat_id", c.get("bale_id", ""))
            chans.append(cc)
        return {"settings": s, "channels": chans,
                "mock": bale.is_mock()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.post("/api/bale/settings")
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

@router.post("/api/bale/test")
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

@router.get("/api/bale/status")
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

@router.get("/api/bale/preview/{cid}")
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

@router.post("/api/bale/post/{cid}")
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

@router.post("/api/bale/channels")
async def api_bale_new_channel(req: Request):
    try:
        import tgju_engine_bale as bale
        body = await req.json()
        data = bale.load_bale()
        cid = "bale%d" % (len(data["channels"]) + 1)
        chat_id = (body.get("bale_id") or body.get("chat_id") or "").strip()
        ch = {
            "id": cid,
            "name": (body.get("name") or "").strip() or "کانال بله",
            "bale_id": chat_id,
            "chat_id": chat_id,
            "enabled": bool(body.get("enabled", True)),
            "schedule_minutes": int(body.get("schedule_minutes") or 30),
            "icon": (body.get("icon") or "🟢").strip(),
            "header": (body.get("header") or "").strip(),
            "section_title": (body.get("section_title") or "قیمت‌ها").strip(),
            "footer": (body.get("footer") or "به‌روزرسانی: هر ۳۰ دقیقه | منبع: tgju.org").strip(),
            "with_footer": bool(body.get("with_footer", True)),
            "with_star": bool(body.get("with_star", True)),
            "with_analysis": bool(body.get("with_analysis", True)),
            "format": body.get("format") or "chips",
            "template": body.get("template") or "",
            "news_categories": body.get("news_categories") or [],
            "analysis_tags": body.get("analysis_tags") or [],
            "slug_groups": body.get("slug_groups") or {},
            "slugs": body.get("slugs") or [],
            "post_types": [p for p in (body.get("post_types") or ["prices"]) if p != "poll"],
        }
        data["channels"].append(ch)
        bale.save_bale(data)
        return {"ok": True, "channel": ch}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.put("/api/bale/channels/{cid}")
async def api_bale_update_channel(cid: str, req: Request):
    try:
        import tgju_engine_bale as bale
        body = await req.json()
        data = bale.load_bale()
        ch = next((c for c in data["channels"] if c.get("id") == cid), None)
        if not ch:
            return JSONResponse({"ok": False, "error": "unknown bale channel %s" % cid},
                                status_code=404)
        if "chat_id" in body and "bale_id" not in body:
            body["bale_id"] = body["chat_id"]     # UI sends the generic name
        for k in ("name", "bale_id", "chat_id", "enabled", "schedule_minutes",
                  "icon", "header", "section_title", "footer", "template",
                  "format", "slug_groups", "slugs", "post_types",
                  "news_categories", "analysis_tags"):
            if k in body:
                ch[k] = body[k]
        for b in ("with_footer", "with_star", "with_analysis"):
            if b in body:
                ch[b] = bool(body[b])
        bale.save_bale(data)
        return {"ok": True, "channel": ch}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.delete("/api/bale/channels/{cid}")
def api_bale_delete_channel(cid: str):
    try:
        import tgju_engine_bale as bale
        data = bale.load_bale()
        data["channels"] = [c for c in data["channels"] if c.get("id") != cid]
        bale.save_bale(data)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@router.get("/", response_class=HTMLResponse)
def index():
    global UI_PAGE
    if UI_PAGE is None:
        ui_path = os.path.join(BASE, "tgju_platform_ui.html")
        try:
            UI_PAGE = open(ui_path, encoding="utf-8").read()
        except Exception:
            UI_PAGE = "<html><body><h1>UI file missing</h1></body></html>"
    return HTMLResponse(UI_PAGE, headers={"Content-Type": "text/html; charset=utf-8"})
