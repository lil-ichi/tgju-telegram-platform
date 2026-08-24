# -*- coding: utf-8 -*-
"""Rubika (روبیکا) — Iranian messenger platform engine.

Official Bot API v3 (Telegram-style):
    https://botapi.rubika.ir/v3/{token}/{method}

Bots are created via @BotFather inside Rubika. The API is a close clone of
Telegram's: sendMessage with chat_id/text, getMe, getUpdates/webhook.
Channel/group IDs are opaque strings (e.g. "g0xxxx..." / "c0xxxx...").

Config lives in `state/rubika.json` — same shape as bale.json:
    {"settings": {"access_token", "auto_post", "schedule_minutes"},
     "channels": [{"id", "name", "chat_id", "enabled", "icon", "header",
                   "section_title", "slug_groups", "slugs",
                   "news_categories", "analysis_tags", "schedule_minutes",
                   "with_footer", "footer", "template", "post_types"}]}

All price/news/analysis formatting reuses the shared tgju engine modules
(same chip tables, unit conversion, news rotation, AI analysis) — one
TGJU source for every platform. Plain text delivery (Rubika supports a
markdown subset; we send the plain chip format like WhatsApp to keep
rendering predictable across clients).
"""

import json
import os
import time
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "state", "rubika.json")

API_BASE = "https://botapi.rubika.ir/v3/{token}/{method}"

DEFAULT_RUBIKA = {
    "settings": {
        "access_token": "",
        "auto_post": False,
        "schedule_minutes": 30,
    },
    "channels": [],
}


def _load_default():
    return json.loads(json.dumps(DEFAULT_RUBIKA))


def load_rubika() -> dict:
    """Load state/rubika.json; merge over defaults so new keys appear."""
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


def save_rubika(data: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def get_rubika_token() -> str:
    return (load_rubika().get("settings") or {}).get("access_token", "").strip()


def is_configured() -> bool:
    return bool(get_rubika_token())


def is_mock() -> bool:
    return not is_configured()


def _call(method: str, payload: dict, timeout: int = 30):
    """Call Rubika Bot API v3. Returns (ok, data).

    Rubika wraps responses as {"status": "OK", "data": {...}} (OK uppercase).
    Errors come back {"status": "ERROR", "message": ...}.
    """
    token = get_rubika_token()
    if not token:
        return False, {"error": "no rubika token configured"}
    url = API_BASE.format(token=token, method=method)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        status = str(resp.get("status", "")).upper()
        if status == "OK" or resp.get("ok") is True:
            return True, resp.get("data") if "data" in resp else resp.get("result")
        return False, resp
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
        except Exception:
            err = {"error": "HTTP %s" % e.code}
        return False, err
    except Exception as e:
        return False, {"error": str(e)}


def send_rubika(chat_id: str, text: str, retries: int = 2,
                timeout: int = 45) -> dict:
    """Send a plain-text message to a Rubika chat/channel.

    Returns {ok, message_id, error}. Mock-aware: without a token the send
    is simulated so the whole pipeline is testable with zero credentials.
    """
    if not text or not text.strip():
        return {"ok": False, "error": "empty text"}
    if is_mock():
        mid = abs(hash((chat_id, text, time.time()))) % (10 ** 8)
        return {"ok": True, "mock": True, "message_id": str(mid)}
    payload = {"chat_id": chat_id, "text": text}
    last = None
    for attempt in range(retries + 1):
        ok, data = _call("sendMessage", payload, timeout=timeout)
        if ok:
            mid = None
            if isinstance(data, dict):
                mid = (data.get("message_update") or {}).get("message_id") \
                    or data.get("message_id")
            return {"ok": True, "message_id": mid}
        last = data
        err_text = json.dumps(last)[:200].lower()
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
    if ok and isinstance(data, dict):
        bot = data.get("bot") or data
        return {"ok": True, "mock": False, "bot": {
            "username": bot.get("username"),
            "first_name": bot.get("first_name") or bot.get("display_name"),
        }}
    return {"ok": False, "error": json.dumps(data)[:300]}


# ── channels ──────────────────────────────────────────────────────────────
def _default_channel(cid: str) -> dict:
    return {
        "id": cid,
        "name": "کانال جدید",
        "chat_id": "",
        "enabled": True,
        "icon": "📢",
        "header": "",
        "section_title": "قیمت‌ها",
        "slug_groups": {},
        "slugs": [],
        "news_categories": [],
        "analysis_tags": [],
        "schedule_minutes": 30,
        "with_footer": True,
        "footer": "به‌روزرسانی: هر ۳۰ دقیقه | منبع: tgju.org",
        "template": "",
        "post_types": ["prices"],
    }


def normalize_channel(c: dict) -> dict:
    base = _default_channel(c.get("id") or "r1")
    base.update(c or {})
    if "custom_data" not in base:
        base["custom_data"] = {}
    return base


def list_channels() -> list:
    chans = load_rubika().get("channels") or []
    seen = set()
    out = []
    i = 0
    for c in chans:
        i += 1
        c = normalize_channel(c)
        if not c.get("id"):
            c["id"] = "r%d" % i
        while c["id"] in seen:
            c["id"] += "_"
        seen.add(c["id"])
        out.append(c)
    return out


def save_channels(channels: list):
    data = load_rubika()
    data["channels"] = channels
    save_rubika(data)


# ── content build (shared single source) ─────────────────────────────────
def preview_channel(channel: dict, post_type: str = "prices") -> str:
    """Build the message text WITHOUT sending — mirrors Bale's approach."""
    try:
        from tgju_platform import cached_rows
        rows = cached_rows() or {}
    except Exception:
        rows = {}
    if post_type == "prices":
        # Rubika renders Telegram HTML poorly → strip to plain chips
        try:
            from tgju_engine_orchestrator import build_for_channel
            import re as _re
            html = build_for_channel(channel, rows) or ""
            text = _re.sub(r"</?b>", "*", html)
            text = _re.sub(r"<a [^>]*>", "", text)
            text = text.replace("</a>", "")
            return text
        except Exception as e:
            return "error: %s" % e
    if post_type == "news":
        try:
            from tgju_engine_news import channel_articles
            items = channel_articles(channel, rows) or []
            return "\n".join("%s\n%s" % (it.get("title", ""), it.get("url", ""))
                             for it in items[:1])
        except Exception as e:
            return "error: %s" % e
    if post_type == "analysis":
        try:
            from tgju_engine_ai import run_analysis, load_ai_config
            res = run_analysis(load_ai_config(), channel, rows)
            return res.get("text") if res.get("ok") else ("error: %s" % res.get("error"))
        except Exception as e:
            return "error: %s" % e
    return ""


def post_channel(channel: dict, post_type: str = "prices") -> dict:
    """Build + deliver one post to the channel's chat_id."""
    chat_id = channel.get("chat_id") or ""
    if not chat_id:
        return {"ok": False, "error": "بدون chat_id — ابتدا کانال را متصل کنید"}
    text = preview_channel(channel, post_type)
    if not text or text.startswith("error:"):
        return {"ok": False, "error": text or "متن خالی"}
    return send_rubika(chat_id, text)


# ── CLI (parity with other engines) ──────────────────────────────────────
if __name__ == "__main__":
    import sys
    if "--list" in sys.argv:
        for c in list_channels():
            print(c["id"], c["name"], c.get("chat_id") or "-")
    elif "--preview" in sys.argv:
        i = sys.argv.index("--preview") + 1
        cid = sys.argv[i]
        ch = next((c for c in list_channels() if c["id"] == cid), None)
        print(preview_channel(ch, "prices") if ch else "no channel")
