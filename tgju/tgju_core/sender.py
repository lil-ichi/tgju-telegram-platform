# -*- coding: utf-8 -*-
"""tgju_core/sender.py — Telegram Bot API sending layer.

Extracted verbatim from tgju_platform.py (lines 216–310): bot token
resolution, the low-level _tg_api_call helper and the two public senders
(send_telegram / send_telegram_poll).  No behavior change — same retry
loop, same timeouts, same response shape.
"""
import json
import os
import re
import urllib.request

from tgju_core.settings import load_settings


def get_bot_token() -> str:
    """Get the active Telegram bot token. Priority: active bot profile in
    state/bot_profile.json → legacy .env TELEGRAM_BOT_TOKEN → ''."""
    try:
        from tgju_engine_bot import get_active_token
        tok = get_active_token()
        if tok:
            return tok
    except Exception:
        pass
    # Legacy fallback: standard Hermes .env location (resolved per-user)
    env_path = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env")
    try:
        env = open(env_path, encoding="utf-8").read()
    except Exception:
        return ""
    # line-anchored: `^TELEGRAM_BOT_TOKEN=...` only — a commented example
    # (`# TELEGRAM_BOT_TOKEN=...`) must NOT match.
    m = re.search(r"^TELEGRAM_BOT_TOKEN\s*=\s*(\S+)", env, re.M)
    return m.group(1).strip() if m else ""


def _tg_api_call(method: str, payload: dict, timeout: int = 30) -> dict:
    """POST JSON to api.telegram.org/bot<token>/<method>.

    Raises urllib.error.HTTPError / OSError / ValueError on transport or
    API errors; the API error detail is in the returned dict when HTTP 200.
    """
    import urllib.error
    token = get_bot_token()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found")
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def send_telegram(chat_id: str, text: str) -> dict:
    token = get_bot_token()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not found"}
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    timeout = max(5, int(load_settings().get("telegram_timeout_seconds", 30)))
    retries = max(0, int(load_settings().get("telegram_retry_count", 2)))
    last_err = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode())
            if resp.get("ok"):
                return resp
            last_err = str(resp.get("description") or resp)
        except Exception as e:
            last_err = str(e)
    return {"ok": False, "error": last_err}


def send_telegram_poll(chat_id: str, question: str, options: list) -> dict:
    """Native Telegram poll (sendPoll). is_anonymous=true is MANDATORY for
    channel chats (API rejects non-anonymous polls in channels)."""
    token = get_bot_token()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not found"}
    url = "https://api.telegram.org/bot%s/sendPoll" % token
    anon = bool(load_settings().get("poll_anonymous", True))
    body = {"chat_id": chat_id, "question": question, "options": options,
            "poll_type": "regular", "is_anonymous": anon}
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    timeout = max(5, int(load_settings().get("telegram_timeout_seconds", 30)))
    retries = max(0, int(load_settings().get("telegram_retry_count", 2)))
    last_err = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode())
            if resp.get("ok"):
                return resp
            last_err = str(resp.get("description") or resp)
        except Exception as e:
            last_err = str(e)
    return {"ok": False, "error": last_err}
