# -*- coding: utf-8 -*-
"""Unified long-polling engine for Bale / Rubika / Eitaa bots.

Research findings (2026-08-26, live API probing + ecosystem libs):

  بله (Bale)     tapi.bale.ai/bot{token}/{method}
                 Telegram Bot API fork → getUpdates(offset, timeout)
                 identical response envelope {"ok":..,"result":[..]}.

  روبیکا (Rubika) botapi.rubika.ir/v3/{token}/{method}   (Bot API v3, POST+JSON)
                 Documented getUpdates with offset/limit in the JSON body;
                 responses wrapped as {"status":"OK","data":{...}}.

  ایتا (Eitaa)    eitaayar.ir/api/{token}/{method}
                 Telegram-style envelope {"ok":false,"error_code":401,...}
                 on bad auth → getUpdates supported the Telegram way.

Design (optimized):
- One shared asyncio loop; each configured platform gets its own poller
  task with a per-platform interval (no global rate-limit coupling).
- Long-poll (timeout ~25s) where the backend supports it: near-zero
  latency without hammering the servers. On platforms/envelopes that
  reject long timeouts we fall back to short-poll at `interval`.
- Offset bookkeeping is per-platform and persisted to
  state/polling_offsets.json so restarts never reprocess old messages.
- Handlers are plain callables registered by tgju_platform.py — this
  module stays transport-only.
- Everything degrades silently when a token is missing/unconfigured:
  poller just doesn't start for that platform.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
import urllib.error
from typing import Callable, Optional

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
OFFSET_FILE = os.path.join(STATE_DIR, "polling_offsets.json")

# per-platform defaults — tuned conservatively to respect server load
DEFAULTS = {
    "bale":   {"interval": 1.0, "long_poll": True},   # Telegram fork: full long-poll support
    "rubika": {"interval": 2.0, "long_poll": False},  # v3 API: short-poll, offset via body
    "eitaa":  {"interval": 2.5, "long_poll": False},  # gateway style: short-poll safest
}

LONG_POLL_TIMEOUT = 25   # seconds, passed to APIs that support it
HTTP_TIMEOUT_PAD = 5     # extra seconds for our own HTTP client


# ── offset persistence ────────────────────────────────────────────

def _load_offsets() -> dict:
    try:
        with open(OFFSET_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_offsets(off: dict):
    try:
        tmp = OFFSET_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(off, f)
        os.replace(tmp, OFFSET_FILE)
    except Exception:
        pass


# ── low-level calls ──────────────────────────────────────────────

def _post_json(url: str, payload: dict, timeout: int = 30):
    """POST JSON. Returns (ok, parsed_dict_or_error_str)."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read().decode("utf-8"))
        except Exception:
            return False, "HTTP %s" % e.code
    except Exception as e:
        return False, str(e)


def _get_json(url: str, timeout: int = 30):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read().decode("utf-8"))
        except Exception:
            return False, "HTTP %s" % e.code
    except Exception as e:
        return False, str(e)


def _extract_updates(platform: str, ok: bool, resp) -> list:
    """Normalize each platform's envelope into a plain list of updates."""
    if not ok or not isinstance(resp, dict):
        return []
    if platform == "rubika":
        # {"status":"OK","data":{"updates":[...]}} or data being the list itself
        data = resp.get("data") if str(resp.get("status", "")).upper() == "OK" else None
        if isinstance(data, dict):
            return data.get("updates") or []
        if isinstance(data, list):
            return data
        return []
    # telegram-style: {"ok":true,"result":[...]}
    if resp.get("ok") is True:
        res = resp.get("result")
        return res if isinstance(res, list) else []
    return []


def _extract_offset(update: dict) -> Optional[int]:
    for k in ("update_id", "offset"):
        v = update.get(k)
        if isinstance(v, int):
            return v
    return None


# ── token resolution (lazy imports avoid cycles) ──────────────────

def _get_token(platform: str) -> str:
    try:
        mod = __import__("tgju_engine_%s" % platform, fromlist=["get_%s_token" % platform])
        return (getattr(mod, "get_%s_token" % platform)() or "").strip()
    except Exception:
        return ""


def _api_url(platform: str, token: str, method: str) -> str:
    if platform == "bale":
        return "https://tapi.bale.ai/bot%s/%s" % (token, method)
    if platform == "rubika":
        return "https://botapi.rubika.ir/v3/%s/%s" % (token, method)
    return "https://eitaayar.ir/api/%s/%s" % (token, method)


# ── the poller loop ──────────────────────────────────────────────

class PlatformPoller:
    def __init__(self, platform: str, handler: Callable[[dict], None],
                 interval: Optional[float] = None, long_poll: Optional[bool] = None):
        self.platform = platform
        self.handler = handler
        d = DEFAULTS.get(platform, {"interval": 2.0, "long_poll": False})
        self.interval = float(interval if interval is not None else d["interval"])
        self.long_poll = bool(long_poll if long_poll is not None else d["long_poll"])
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None
        self.updates_seen = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="poller-%s" % self.platform)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def status(self) -> dict:
        return {
            "platform": self.platform,
            "running": bool(self._thread and self._thread.is_alive()),
            "configured": bool(self._current_token()),
            "interval": self.interval,
            "long_poll": self.long_poll,
            "updates_seen": self.updates_seen,
            "last_error": self.last_error,
        }

    def _current_token(self) -> str:
        return _get_token(self.platform)

    def _run(self):
        offsets = _load_offsets()
        off = int(offsets.get(self.platform) or 0)
        consecutive_errors = 0
        while not self._stop.is_set():
            token = self._current_token()
            if not token:
                # not configured yet — idle, recheck periodically
                self._stop.wait(15)
                continue
            try:
                if self.long_poll:
                    ok, resp = _post_json(
                        _api_url(self.platform, token, "getUpdates"),
                        {"offset": off + 1 if off else None, "timeout": LONG_POLL_TIMEOUT,
                         "limit": 50},
                        timeout=LONG_POLL_TIMEOUT + HTTP_TIMEOUT_PAD)
                elif self.platform == "rubika":
                    ok, resp = _post_json(
                        _api_url(self.platform, token, "getUpdates"),
                        {"offset": off + 1 if off else 0, "limit": 50},
                        timeout=self.interval * 3 + 10)
                else:
                    ok, resp = _get_json(
                        _api_url(self.platform, token, "getUpdates?offset=%d&limit=50"
                                 % (off + 1 if off else 0)),
                        timeout=self.interval * 3 + 10)

                updates = _extract_updates(self.platform, ok, resp)
                if not ok and updates == []:
                    err = resp if isinstance(resp, str) else json.dumps(resp)[:200]
                    # 404/405 → method unsupported on this account tier; back off hard
                    if any(m in err for m in ('"error_code":404', '"error_code":405',
                                              'Not Found', 'not found')):
                        self.last_error = "getUpdates not supported: %s" % err[:120]
                        self._stop.wait(120)
                        continue
                    raise RuntimeError(err[:200])

                consecutive_errors = 0
                self.last_error = None
                for upd in updates:
                    self.updates_seen += 1
                    try:
                        self.handler(upd)
                    except Exception as e:            # handler bugs never kill the loop
                        self.last_error = "handler: %s" % e
                    new_off = _extract_offset(upd)
                    if new_off is not None and new_off > off:
                        off = new_off
                if updates:
                    offsets = _load_offsets()          # merge (other pollers write too)
                    offsets[self.platform] = off
                    _save_offsets(offsets)

                # sleep only between SHORT polls; long-poll already waited
                if not self.long_poll:
                    self._stop.wait(self.interval)
            except Exception as e:
                consecutive_errors += 1
                self.last_error = str(e)[:200]
                # exponential-ish backoff capped at 60s — be gentle to the servers
                self._stop.wait(min(60, 2 ** min(consecutive_errors, 6)))


# ── registry used by the app ─────────────────────────────────────

_POLLERS: dict = {}
_LOCK = threading.Lock()


def start_all(handler_factory):
    """Start pollers for all three platforms.

    handler_factory(platform) -> callable(update_dict) or None to skip.
    """
    for plat in ("bale", "rubika", "eitaa"):
        h = handler_factory(plat)
        if h is None:
            continue
        with _LOCK:
            p = _POLLERS.get(plat)
            if p is None:
                p = PlatformPoller(plat, h)
                _POLLERS[plat] = p
            p.start()
    return _POLLERS


def stop_all():
    with _LOCK:
        for p in _POLLERS.values():
            p.stop()


def status_all() -> list:
    with _LOCK:
        plist = list(_POLLERS.values())
    out = [p.status() for p in plist]
    # also report unstarted-but-known platforms
    seen = {s["platform"] for s in out}
    for plat in ("bale", "rubika", "eitaa"):
        if plat not in seen:
            out.append({"platform": plat, "running": False, "configured": False,
                        "interval": DEFAULTS[plat]["interval"],
                        "long_poll": DEFAULTS[plat]["long_poll"],
                        "updates_seen": 0, "last_error": None})
    return sorted(out, key=lambda s: s["platform"])
