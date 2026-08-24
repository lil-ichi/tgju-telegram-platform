# -*- coding: utf-8 -*-
"""Config loader for the TGJU Telegram platform — channels.yaml + state dirs."""
import json
import os
import re

import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = BASE_DIR                        # platform/  (D:\Hermes\TGJU-Telegram\platform)
CONFIG_PATH = os.path.join(BASE_DIR, "channels.yaml")
STATE_DIR = os.path.join(BASE_DIR, "state")
LOG_PATH = os.path.join(STATE_DIR, "platform.log")
OVERRIDES_PATH = os.path.join(STATE_DIR, "slug_overrides.json")
PROFILE_CACHE_PATH = os.path.join(STATE_DIR, "profile_cache.json")


def ensure_dirs():
    os.makedirs(STATE_DIR, exist_ok=True)


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_channels() -> list:
    cfg = load_yaml(CONFIG_PATH)
    chans = cfg.get("channels") or []
    out = []
    for i, c in enumerate(chans):
        base = {"id": "ch%d" % (i + 1),
                "name": "کانال %d" % (i + 1),
                "telegram_id": "",
                "enabled": True,
                "header": "",
                "icon": "",
                "section_title": "قیمت‌ها",
                "slug_groups": {},
                "slugs": [],
                "news_categories": [],
                "analysis_tags": [],
                "schedule_minutes": 10,
                "poll_enabled": False,
                "poll_pool": [],
                "with_star": True,
                "with_analysis": True,
                "with_footer": True,
                "footer": "به‌روزرسانی: هر ۱۰ دقیقه | منبع: tgju.org",
                "format": "chips",
                "template": "",
                "post_count": 0,
                "last_post": None,
                "last_error": None,
                "next_run": None}
        base.update(c or {})
        # Ensure custom_data is always present
        if "custom_data" not in base:
            base["custom_data"] = {}
        # style templates: stored as style_json (flat JSON string) in YAML
        if not isinstance(base.get("style"), dict):
            try:
                sj = base.pop("style_json", "") or ""
                parsed = json.loads(sj) if sj else {}
                base["style"] = parsed if isinstance(parsed, dict) else {}
            except Exception:
                base["style"] = {}
        out.append(base)
    return out


def save_channels(channels: list):
    """Rewrite channels.yaml from the in-memory list (pretty).

    All string values are emitted with PyYAML's safe_dump (double-quoted
    when needed) so special chars like '@', ':', '#', '*' never corrupt
    the YAML file (a bare '@test' telegram_id used to break parsing).
    """
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("# TGJU Telegram Platform — channel definitions\n")
        f.write("# Edit here OR via the webapp UI (localhost:8791).\n\n")
        f.write("channels:\n")
        for c in channels:
            def _q(v):
                # Quote for YAML safely. json.dumps always emits valid
                # double-quoted YAML scalar syntax (handles '@', ':', '#',
                # unicode, newlines) without PyYAML's document-end '...'.
                return json.dumps(str(v) if v is not None else "",
                                  ensure_ascii=False)
            f.write("  - id: %s\n" % _q(c.get("id")))
            f.write("    name: %s\n" % _q(c.get("name")))
            f.write("    telegram_id: %s\n" % _q(c.get("telegram_id") or ""))
            f.write("    enabled: %s\n" % ("true" if c.get("enabled") else "false"))
            f.write("    icon: %s\n" % _q(c.get("icon") or ""))
            f.write("    header: %s\n" % _q(c.get("header") or ""))
            f.write("    section_title: %s\n" % _q(c.get("section_title", "قیمت‌ها")))
            if c.get("slug_groups"):
                f.write("    slug_groups:\n")
                for gname, slugs in c["slug_groups"].items():
                    f.write("      %s:\n" % _q(gname))
                    for s in slugs:
                        f.write("        - %s\n" % _q(s))
            if c.get("slugs"):
                f.write("    slugs:\n")
                for s in c["slugs"]:
                    f.write("      - %s\n" % _q(s))
            if c.get("news_categories"):
                f.write("    news_categories:\n")
                for s in c["news_categories"]:
                    f.write("      - %s\n" % _q(s))
            if c.get("analysis_tags"):
                f.write("    analysis_tags:\n")
                for s in c["analysis_tags"]:
                    f.write("      - %s\n" % _q(s))
            f.write("    schedule_minutes: %s\n" % c.get("schedule_minutes", 10))
            f.write("    poll_enabled: %s\n" % ("true" if c.get("poll_enabled") else "false"))
            f.write("    with_star: %s\n" % ("true" if c.get("with_star", True) else "false"))
            f.write("    with_analysis: %s\n" % ("true" if c.get("with_analysis", True) else "false"))
            f.write("    with_footer: %s\n" % ("true" if c.get("with_footer", True) else "false"))
            f.write("    footer: %s\n" % _q(c.get("footer") or ""))
            f.write("    format: %s\n" % _q(c.get("format", "chips")))
            if c.get("template"):
                f.write("    template: %s\n" % _q(c.get("template")))
            if c.get("post_types"):
                f.write("    post_types:\n")
                for p in c["post_types"]:
                    f.write("      - %s\n" % _q(p))
            if c.get("custom_data"):
                f.write("    custom_data:\n")
                for k, v in c["custom_data"].items():
                    f.write("      %s: %s\n" % (_q(k), _q(str(v))))
            if c.get("style"):
                # tag-style templates — stored as a flat JSON string so the
                # simple YAML writer stays line-based (parsed back via json)
                f.write("    style_json: %s\n" % _q(json.dumps(c["style"], ensure_ascii=False)))


def channel_state_path(channel_id: str) -> str:
    return os.path.join(STATE_DIR, "analysis_%s.json" % channel_id)


def load_channel_state(channel_id: str) -> dict:
    try:
        with open(channel_state_path(channel_id), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_channel_state(channel_id: str, state: dict):
    try:
        with open(channel_state_path(channel_id), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def log_line(msg: str):
    ensure_dirs()
    try:
        from datetime import datetime
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass


# ── Manual slug overrides (state/slug_overrides.json) ─────────────────────
# Admin can override per-slug: profile_url (custom link) and/or manual_price
# (fixed value) and/or custom display name — fixes slugs tgju.org serves
# wrong or empty. Shape:
#   {
#     "bourse_dow": {
#       "name": "داوجونز",
#       "profile_url": "https://www.tgju.org/profile/dow_jones_us",
#       "manual_price": "52900.07",
#       "change_pct": "0.5",
#       "dir": "high"
#     }, ...
#   }


def load_slug_overrides() -> dict:
    try:
        with open(OVERRIDES_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_slug_overrides(overrides: dict):
    ensure_dirs()
    try:
        with open(OVERRIDES_PATH, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def rename_slug(old: str, new: str) -> dict:
    """Rename a slug key EVERYWHERE it is referenced.

    - channels.yaml: every channel's slug_groups (all groups)
    - state/slug_overrides.json: override key
    - state/profile_cache.json: cache entries (keys + inside entries)

    Returns {"ok": True, "updated": [channel ids], "overrides": bool,
             "cache": int} or {"ok": False, "error": ...}.
    """
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new:
        return {"ok": False, "error": "slug ها را وارد کنید"}
    if old == new:
        return {"ok": False, "error": "اسلاگ جدید همان اسلاگ فعلی است"}
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", new):
        return {"ok": False, "error": "اسلاگ جدید فقط حروف، عدد، _ ، - و . می‌پذیرد"}

    chans = load_channels()
    updated = []
    for c in chans:
        groups = c.get("slug_groups") or {}
        changed = False
        for gname, slugs in groups.items():
            if old in slugs:
                slugs[slugs.index(old)] = new
                changed = True
        flat = c.get("slugs") or []
        if old in flat:
            flat[flat.index(old)] = new
            c["slugs"] = flat
            changed = True
        if changed:
            updated.append(c.get("id"))
    if updated:
        save_channels(chans)

    ov_count = 0
    overrides = load_slug_overrides()
    if old in overrides:
        overrides[new] = overrides.pop(old)
        save_slug_overrides(overrides)
        ov_count = 1

    cache_count = 0
    try:
        with open(PROFILE_CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
        if old in cache:
            cache[new] = cache.pop(old)
            cache_count = 1
        for entry in cache.values():
            if isinstance(entry, dict) and entry.get("slug") == old:
                entry["slug"] = new
        if cache_count or any(isinstance(e, dict) and e.get("slug") == new
                              for e in cache.values()):
            with open(PROFILE_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass

    return {"ok": True, "updated": updated, "overrides": ov_count,
            "cache": cache_count}


ensure_dirs()
