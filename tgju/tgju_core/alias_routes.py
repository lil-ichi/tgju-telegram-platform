# -*- coding: utf-8 -*-
"""Alias resolver background loop + API routes.

Interval worker: every tick resolves up to N still-broken slugs (bounded,
seconds per slug — can never hang the server), warms the profile cache, and
records a compact 🩺 log line on each fixed slug so the داده‌ها و لینک‌ها
table can show it inline under that slug's row.

Routes (registered on the main APIRouter):
    GET  /api/alias-resolver          — status: enabled, last run, recent fixes
    PUT  /api/alias-resolver/config   — {enabled, interval_minutes, batch_size}
    POST /api/alias-resolver/run      — one pass NOW
"""
import asyncio
import json
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))                     # repo root
STATE_DIR = os.path.join(BASE_DIR, "tgju", "state")
CFG_PATH = os.path.join(STATE_DIR, "alias_resolver.json")
LOG_PATH = os.path.join(STATE_DIR, "alias_fixes.json")

DEFAULTS = {"enabled": True, "interval_minutes": 30, "batch_size": 5}


def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f) or {})
    except Exception:
        pass
    return cfg


def save_cfg(cfg: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_PATH)


def _append_log(entries: list):
    try:
        log = []
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                log = json.load(f) or []
        except Exception:
            pass
        log = (entries + log)[:200]           # keep last 200 fix events
        tmp = LOG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LOG_PATH)
    except Exception:
        pass


def _read_log(limit: int = 30) -> list:
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            return (json.load(f) or [])[:limit]
    except Exception:
        return []


def resolver_pass(batch_size: int | None = None) -> dict:
    """One bounded resolution pass. Returns summary + writes state."""
    from tgju_core.alias_resolver import backfill_aliases, load_alias_map
    from tgju_engine_config import load_slug_overrides, save_slug_overrides
    from tgju_engine_ai import load_ai_jobs, resolve_job, load_ai_config, \
        record_ai_activity

    jobs = load_ai_jobs()["jobs"].get("slug_repair") or {}
    if jobs and jobs.get("enabled") is False:
        return {"ok": False, "skipped": "job disabled in AI tab"}
    cfg = load_cfg()
    n = int(batch_size or cfg.get("batch_size")
            or jobs.get("batch_size") or 5)
    res = backfill_aliases(max_slugs=max(1, min(n, 15)))

    # compact 🩺 line onto each fixed slug's own override (inline in the tab)
    if res.get("fixed"):
        overrides = load_slug_overrides()
        amap = load_alias_map()
        stamp = time.strftime("%m-%d %H:%M")
        for fx in res["fixed"]:
            slug = fx["slug"]
            ov = dict(overrides.get(slug) or {})
            ov["ai_log"] = "%s %s | %s ← %s" % (
                "🩺", stamp, fx["price"], fx["real_slug"])
            overrides[slug] = ov
        save_slug_overrides(overrides)

        entries = [dict(fx, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
                   for fx in res["fixed"]]
        _append_log(entries)

    result = {"ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "checked": res.get("checked", 0),
              "fixed": res.get("fixed", [])}
    cfg["last_run"] = result["ts"]
    cfg["last_checked"] = result["checked"]
    cfg["last_fixed"] = len(result["fixed"])
    save_cfg(cfg)

    # AI tab activity feed — shows WHO/WHAT did the repair
    if res.get("fixed"):
        try:
            ai_cfg = load_ai_config()
            job = resolve_job(ai_cfg, "slug_repair")
            prov_name = job.get("provider") or "?"
            prov = (ai_cfg.get("providers") or {}).get(prov_name) or {}
            record_ai_activity({
                "job": "slug_repair",
                "label": jobs.get("label") or "ترمیم منابع داده",
                "provider": prov_name,
                "model": job.get("model") or prov.get("model") or "-",
                "engine": "قانونی + تأیید شبکه (بدون توکن)",
                "fixed": len(res["fixed"]),
                "slugs": [f["slug"] for f in res["fixed"]],
            })
        except Exception:
            pass
    return result


async def resolver_loop():
    """Startup-anchored interval task — mirrors refresher_loop's pattern."""
    while True:
        cfg = load_cfg()
        delay = max(5, int(cfg.get("interval_minutes") or 30)) * 60
        if cfg.get("enabled"):
            try:
                await asyncio.to_thread(resolver_pass)
            except Exception:
                pass
        await asyncio.sleep(delay)


# ── routes ────────────────────────────────────────────────────────────────

router = APIRouter()


@router.get("/api/polling")
def api_polling_status():
    """Live status of the بله/روبیکا/ایتا message pollers."""
    try:
        import tgju_engine_polling as polling
        return {"ok": True, "pollers": polling.status_all()}
    except Exception as e:
        return {"ok": False, "error": str(e), "pollers": []}


@router.get("/api/alias-resolver")
def api_alias_status():
    cfg = load_cfg()
    return {"config": {k: cfg.get(k) for k in
                       ("enabled", "interval_minutes", "batch_size",
                        "last_run", "last_checked", "last_fixed")},
            "recent": _read_log(20),
            "alias_count": len(_safe_alias_map())}


def _safe_alias_map() -> dict:
    try:
        from tgju_core.alias_resolver import load_alias_map
        return load_alias_map()
    except Exception:
        return {}


@router.put("/api/alias-resolver/config")
async def api_alias_config(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    cfg = load_cfg()
    if isinstance(body.get("enabled"), bool):
        cfg["enabled"] = body["enabled"]
    if body.get("interval_minutes"):
        try:
            cfg["interval_minutes"] = max(5, int(body["interval_minutes"]))
        except Exception:
            pass
    if body.get("batch_size"):
        try:
            cfg["batch_size"] = max(1, min(int(body["batch_size"]), 15))
        except Exception:
            pass
    save_cfg(cfg)
    return {"ok": True, "config": {k: cfg.get(k) for k in
                                   ("enabled", "interval_minutes",
                                    "batch_size")}}


@router.post("/api/alias-resolver/run")
async def api_alias_run():
    """One pass NOW — bounded (≤15 slugs × ~1s), safe inline."""
    res = await asyncio.to_thread(resolver_pass)
    return res
