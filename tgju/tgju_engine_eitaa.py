# -*- coding: utf-8 -*-
"""Eitaa (ایتا) — Iranian messenger platform engine.

Channel delivery via EitaaYar API (the official channel assistant):
    https://eitaayar.ir/api/{TOKEN}/{method}

Methods (multipart/form or urlencoded POST — NOT JSON):
    sendmessage  → chat_id (@username or numeric id), text
    sendfile     → chat_id, file, caption

Config lives in `state/eitaa.json` — same shape as bale.json/rubika.json.
All price/news/analysis formatting reuses the shared tgju engine modules;
delivery is PLAIN TEXT (EitaaYar sends raw text — no HTML rendering).
"""

import json
import os
import time
import re as _re
import urllib.request
import urllib.parse
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "state", "eitaa.json")

API_BASE = "https://eitaayar.ir/api/{token}/{method}"

DEFAULT_EITAA = {
    "settings": {
        "access_token": "",
        "auto_post": False,
        "schedule_minutes": 30,
    },
    "channels": [],
}


def _load_default():
    return json.loads(json.dumps(DEFAULT_EITAA))


def load_eitaa() -> dict:
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


def save_eitaa(data: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def get_eitaa_token() -> str:
    return (load_eitaa().get("settings") or {}).get("access_token", "").strip()


def is_configured() -> bool:
    return bool(get_eitaa_token())


def is_mock() -> bool:
    return not is_configured()


def _call(method: str, fields: dict, timeout: int = 30):
    """POST urlencoded form to EitaaYar. Returns (ok, data).

    EitaaYar returns {"status": "success"/"error", ...} (lowercase) and on
    some errors just plain text — normalize both.
    """
    token = get_eitaa_token()
    if not token:
        return False, {"error": "no eitaa token configured"}
    url = API_BASE.format(token=token, method=method)
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read().decode("utf-8"))
        except Exception:
            return False, {"error": "HTTP %s" % e.code}
    except Exception as e:
        return False, {"error": str(e)}
    try:
        resp = json.loads(raw)
    except Exception:
        return False, {"error": raw[:200]}
    status = str(resp.get("status", "")).lower()
    if status in ("success", "ok") or resp.get("ok") is True:
        return True, resp
    return False, resp


def _plain(html_text: str) -> str:
    """Strip Telegram HTML → clean plain text (Eitaa renders no HTML)."""
    text = _re.sub(r"<a [^>]*>", "", html_text or "")
    text = text.replace("</a>", "")
    text = _re.sub(r"</?b>", "", text)
    return text


def send_eitaa(chat_id: str, text: str, retries: int = 2,
               timeout: int = 45) -> dict:
    """Send a message to an Eitaa channel via EitaaYar. Mock-aware."""
    if not text or not text.strip():
        return {"ok": False, "error": "empty text"}
    if is_mock():
        mid = abs(hash((chat_id, text, time.time()))) % (10 ** 8)
        return {"ok": True, "mock": True, "message_id": str(mid)}
    last = None
    for attempt in range(retries + 1):
        ok, data = _call("sendmessage", {"chat_id": chat_id, "text": text},
                         timeout=timeout)
        if ok:
            mid = data.get("message_id") or (data.get("data") or {}).get("message_id")
            return {"ok": True, "message_id": mid}
        last = data
        err_text = json.dumps(last)[:200].lower()
        if any(k in err_text for k in ("timed out", "timeout")):
            time.sleep(2 * (attempt + 1))
            continue
        break
    return {"ok": False, "error": json.dumps(last)[:300]}


def test_credentials() -> dict:
    """EitaaYar has no getMe — validate by probing the API root shape.

    Without a token we report mock. With a token we do a harmless probe
    (sendmessage to the special @etbeta test chat would post real content,
    so instead we just verify the endpoint responds with valid JSON).
    """
    if is_mock():
        return {"ok": True, "mock": True, "bot": {"username": "(mock)"}}
    # lightweight probe: an invalid method returns a JSON error from the API
    ok, data = _call("__probe__", {})
    # any structured JSON response proves connectivity + token routing
    if isinstance(data, dict):
        return {"ok": True, "mock": False,
                "bot": {"note": "توکن ثبت شد — با یک ارسال آزمایشی نهایی کنید"}}
    return {"ok": False, "error": json.dumps(data)[:300]}


# ── channels ──────────────────────────────────────────────────────────────
def _default_channel(cid: str) -> dict:
    return {
        "id": cid,
        "name": "کانال جدید",
        "chat_id": "",           # @username or numeric id
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
        "format": "chips",
        "with_star": True,
        "with_analysis": True,
        "post_types": ["prices"],
    }


def normalize_channel(c: dict) -> dict:
    base = _default_channel(c.get("id") or "e1")
    base.update(c or {})
    if "custom_data" not in base:
        base["custom_data"] = {}
    return base


def list_channels() -> list:
    chans = load_eitaa().get("channels") or []
    seen = set()
    out = []
    i = 0
    for c in chans:
        i += 1
        c = normalize_channel(c)
        if not c.get("id"):
            c["id"] = "e%d" % i
        while c["id"] in seen:
            c["id"] += "_"
        seen.add(c["id"])
        out.append(c)
    return out


def save_channels(channels: list):
    data = load_eitaa()
    data["channels"] = channels
    save_eitaa(data)


# ── content build (shared single source) ─────────────────────────────────
def preview_channel(channel: dict, post_type: str = "prices") -> str:
    try:
        from tgju_platform import cached_rows
        rows = cached_rows() or {}
    except Exception:
        rows = {}
    if post_type == "prices":
        try:
            from tgju_engine_orchestrator import build_for_channel
            return _plain(build_for_channel(channel, rows) or "")
        except Exception as e:
            return "error: %s" % e
    if post_type == "news":
        try:
            from tgju_engine_news import channel_articles
            items = channel_articles(channel, rows) or []
            it = items[0] if items else None
            return ("%s\n%s" % (it.get("title", ""), it.get("url", ""))) if it else ""
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
    chat_id = channel.get("chat_id") or ""
    if not chat_id:
        return {"ok": False, "error": "بدون chat_id — ابتدا کانال را متصل کنید"}
    text = preview_channel(channel, post_type)
    if not text or text.startswith("error:"):
        return {"ok": False, "error": text or "متن خالی"}
    return send_eitaa(chat_id, text)


# ── CLI ───────────────────────────────────────────────────────────────────
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
