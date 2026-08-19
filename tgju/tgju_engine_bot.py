# -*- coding: utf-8 -*-
"""Bot profile management — store multiple Telegram bot tokens, switch
between them. Replaces the hardcoded .env TELEGRAM_BOT_TOKEN lookup.

state/bot_profile.json structure::

    {
      "active_id": "default",
      "profiles": [
        {"id": "default", "name": "My Bot", "token": "1234:ABC...", "added_at": "2026-08-15T..."}
      ]
    }
"""
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_PROFILES_PATH = os.path.join(BASE_DIR, "state", "bot_profile.json")


def load_bot_profiles() -> dict:
    """Load bot profiles. Falls back to legacy .env token if no profiles."""
    try:
        with open(BOT_PROFILES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"active_id": "default", "profiles": []}
    if not data.get("active_id"):
        data["active_id"] = "default"
    if not isinstance(data.get("profiles"), list):
        data["profiles"] = []
    # Auto-migrate legacy .env token into a "default" profile
    if not data["profiles"]:
        legacy_token = _read_legacy_env_token()
        if legacy_token:
            data["profiles"].append({
                "id": "default",
                "name": "پیش‌فرض (Legacy)",
                "token": legacy_token,
                "added_at": "",
            })
            data["active_id"] = "default"
            save_bot_profiles(data)
    return data


def save_bot_profiles(data: dict):
    with open(BOT_PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_active_token() -> str:
    """Get the token of the currently active bot profile."""
    cfg = load_bot_profiles()
    active = cfg["active_id"]
    for p in cfg["profiles"]:
        if p["id"] == active:
            return p.get("token", "")
    # Fallback: first profile
    if cfg["profiles"]:
        return cfg["profiles"][0].get("token", "")
    # Fallback: legacy env
    return _read_legacy_env_token()


def get_active_profile() -> dict:
    """Get the full active profile dict, or empty dict."""
    cfg = load_bot_profiles()
    active = cfg["active_id"]
    for p in cfg["profiles"]:
        if p["id"] == active:
            return p
    if cfg["profiles"]:
        return cfg["profiles"][0]
    return {}


def _read_legacy_env_token() -> str:
    """Read TELEGRAM_BOT_TOKEN from the legacy .env location."""
    # Try multiple common locations
    env_paths = [
        os.path.join(os.environ.get("APPDATA", ""), "hermes", ".env"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", ".env"),
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env"),
    ]
    for env_path in env_paths:
        try:
            env = open(env_path, encoding="utf-8").read()
        except Exception:
            continue
        m = re.search(r"^TELEGRAM_BOT_TOKEN\s*=\s*(\S+)", env, re.M)
        if m:
            return m.group(1).strip()
    return ""


def test_bot_token(token: str) -> dict:
    """Call getMe to validate a bot token. Returns {ok, bot_name, bot_username, error}."""
    import urllib.request
    import urllib.error
    import json as _json
    url = "https://api.telegram.org/bot%s/getMe" % token
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = _json.loads(r.read().decode())
        if resp.get("ok"):
            result = resp.get("result", {})
            return {
                "ok": True,
                "bot_id": result.get("id"),
                "bot_name": result.get("first_name", ""),
                "bot_username": result.get("username", ""),
            }
        return {"ok": False, "error": resp.get("description", "unknown error")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %d: %s" % (e.code, e.reason)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
