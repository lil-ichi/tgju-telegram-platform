# -*- coding: utf-8 -*-
"""Bale (بله) — Iranian messenger platform engine.

Bale's Bot API is Telegram-compatible:
    https://tapi.bale.ai/bot<BOT_TOKEN>/<method>

So the Bale platform mirrors the Telegram channel-orchestration model:
a single bot (profile token) connected to N channels, the backend
orchestrates (scheduler + manual) and posts price/news/poll/analysis to
every channel the bot is admin of.

Config lives in `state/bale.json` (LEGACY-independent of channels.yaml).
All prices/news/analysis formatting reuses the shared tgju engine modules
(same chip tables, unit conversion, news rotation, AI analysis).
"""

import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "state", "bale.json")

API_BASE = "https://tapi.bale.ai/bot{token}/{method}"

DEFAULT_BALE = {
    "settings": {
        "access_token": "",
        "auto_post": False,
        "schedule_minutes": 30,
    },
    "channels": [],
}


def _load_default():
    return json.loads(json.dumps(DEFAULT_BALE))


def load_bale() -> dict:
    """Load state/bale.json; merge over defaults so new keys appear."""
    data = _load_default()
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, encoding="utf-8") as f:
                disk = json.load(f)
            for k in ("settings", "channels"):
                if k in disk:
                    data[k] = disk[k]
    except Exception:
        pass
    return data


def save_bale(data: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def get_bale_token() -> str:
    return (load_bale().get("settings") or {}).get("access_token", "").strip()


def is_configured() -> bool:
    return bool(get_bale_token())


def is_mock() -> bool:
    """Mock when no token configured."""
    return not is_configured()


def _call(method: str, payload: dict, timeout: int = 30):
    """Call Bale Bot API. Returns (ok, data)."""
    token = get_bale_token()
    if not token:
        return False, {"error": "no bale token configured"}
    url = API_BASE.format(token=token, method=method)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("ok"):
            return True, resp.get("result")
        return False, resp
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
        except Exception:
            err = {"error": "HTTP %s" % e.code}
        return False, err
    except Exception as e:
        return False, {"error": str(e)}


def send_bale(chat_id: str, text: str, parse_mode: str = "HTML",
              retries: int = 2, timeout: int = 45) -> dict:
    """Send a message to a Bale chat/channel. Returns {ok, message_id, error}."""
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    last = None
    for attempt in range(retries + 1):
        ok, data = _call("sendMessage", payload, timeout=timeout)
        if ok:
            mid = None
            if isinstance(data, dict):
                mid = data.get("message_id")
            return {"ok": True, "message_id": mid}
        last = data
        # transient (429 / 5xx) -> backoff
        err_text = json.dumps(data)[:200].lower()
        if any(k in err_text for k in ("429", "5", "timed out", "timeout")):
            time.sleep(2 * (attempt + 1))
            continue
        break
    return {"ok": False, "error": json.dumps(last)[:300]}


def test_credentials() -> dict:
    """Probe the bot via getMe. Mock-aware."""
    if is_mock():
        return {"ok": True, "mock": True, "bot": {"username": "(mock)"}}
    ok, data = _call("getMe", {})
    if ok:
        return {"ok": True, "mock": False, "bot": data}
    return {"ok": False, "error": json.dumps(data)[:300]}


def _post_build(channel: dict) -> str:
    """Build the channel body (prices) — mirrors Telegram build_for_channel."""
    try:
        from tgju_engine_orchestrator import build_for_channel
        rows = {}
        try:
            from tgju_platform import cached_rows
            rows = cached_rows() or {}
        except Exception:
            rows = {}
        text = build_for_channel(channel, rows)
        return text or ""
    except Exception:
        return ""


# ── per-channel state ──────────────────────────────────────────────────────
def _cid_file(cid: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in cid)
    return os.path.join(BASE_DIR, "state", "bale_%s.json" % safe)


def load_channel_state(cid: str) -> dict:
    try:
        with open(_cid_file(cid), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_channel_state(cid: str, st: dict):
    try:
        with open(_cid_file(cid), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def preview_channel(channel: dict, post_type: str = "prices") -> str:
    """Build a preview without sending (reuses Telegram builders)."""
    try:
        from tgju_platform import cached_rows
        rows = cached_rows() or {}
    except Exception:
        rows = {}
    if post_type == "prices":
        try:
            from tgju_engine_orchestrator import build_for_channel
            return build_for_channel(channel, rows) or ""
        except Exception as e:
            return "error: %s" % e
    if post_type == "news":
        try:
            from tgju_engine_news import channel_articles
            items = channel_articles(channel, rows)
            return "\n".join(
                "<b>%s</b>\n<a href=\"%s\">%s</a>" % (it.get("title", ""),
                                it.get("url", ""), it.get("title", ""))
                for it in items) or ""
        except Exception as e:
            return "error: %s" % e
    if post_type == "analysis":
        try:
            from tgju_engine_ai import run_analysis
            text = run_analysis(channel, rows)
            return text or ""
        except Exception as e:
            return "error: %s" % e
    return ""