# -*- coding: utf-8 -*-
"""Functions (schedulable tasks) — timing, template, AI, effort.

Each function is an interval-driven task that fires INDEPENDENTLY of the
prices/news rotation: when its interval elapses, the scheduler posts that
function's output INSTEAD of the regular rotation post for that tick.

state/functions.json structure::

    {
      "analysis": {
        "label": "تحلیل بازار",
        "desc": "...",
        "enabled": true,          # master switch for the function
        "interval_hours": 6,      # default per-channel interval
        "template": "...",
        "effort": "standard",     # standard | deep
        "max_tokens": 2000,
        "timeout_s": 90,
        "channels": {             # per-channel overrides
          "ch1": {"enabled": true, "interval_hours": 6}
        }
      },
      "poll": {
        "label": "نظرسنجی",
        "desc": "...",
        "enabled": true,
        "interval_hours": 4,
        "channels": {}
      },
      "news": {
        "label": "خبر",
        "desc": "...",
        "enabled": true,
        "interval_hours": 6,
        "channels": {}
      }
    }
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FUNCTIONS_PATH = os.path.join(BASE_DIR, "state", "functions.json")

DEFAULT_FUNCTIONS = {
    "analysis": {
        "label": "تحلیل بازار",
        "desc": "تحلیل هوشمند بازار مرتبط با هر کانال (طلا، انرژی، ارز و…)",
        "enabled": True,
        "interval_hours": 6,
        "template": "📈 تحلیل بازار {name} | {weekday} {time}",
        "effort": "standard",   # standard | deep
        "max_tokens": 2000,
        "timeout_s": 90,
        "channels": {},
    },
    "poll": {
        "label": "نظرسنجی",
        "desc": "نظرسنجی هوشمند درباره روند بازار",
        "enabled": True,
        "interval_hours": 4,
        "channels": {},
    },
    "news": {
        "label": "خبر",
        "desc": "انتخاب تصادفی خبر مرتبط با بازار",
        "enabled": True,
        "interval_hours": 6,
        "channels": {},
    },
}


def load_functions() -> dict:
    """Load functions config merged over defaults (new keys appear)."""
    try:
        with open(FUNCTIONS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    merged = {}
    for fid, d in DEFAULT_FUNCTIONS.items():
        item = dict(d)
        item.update(data.get(fid) or {})
        item["channels"] = dict((data.get(fid) or {}).get("channels") or {})
        merged[fid] = item
    return merged


def save_functions(cfg: dict):
    with open(FUNCTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def function_channel_enabled(fn_cfg: dict, cid: str) -> bool:
    """Is this function enabled FOR this channel (master + per-channel)?"""
    if not fn_cfg.get("enabled", True):
        return False
    ch = (fn_cfg.get("channels") or {}).get(cid) or {}
    return bool(ch.get("enabled", False))


def function_channel_interval(fn_cfg: dict, cid: str, default_hours: int = 6) -> int:
    """Per-channel interval (hours); falls back to function default."""
    ch = (fn_cfg.get("channels") or {}).get(cid) or {}
    iv = ch.get("interval_hours")
    if not iv:
        iv = fn_cfg.get("interval_hours") or default_hours
    return max(1, int(iv))