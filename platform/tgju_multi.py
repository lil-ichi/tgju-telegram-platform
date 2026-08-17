# -*- coding: utf-8 -*-
"""tgju_multi.py — CLI for the TGJU Telegram multi-channel platform.

Usage:
    python -m platform.tgju_multi --list
    python -m platform.tgju_multi --preview ch1
    python -m platform.tgju_multi --post ch1 [--real]
    (--real sends to the channel via bot; default prints only)
"""
import argparse
import re
import sys
import urllib.parse
import urllib.request

from tgju_engine_config import load_channels, log_line
from tgju_engine_orchestrator import build_for_channel
from tgju_engine_scrape import get_all_prices


def get_bot_token() -> str:
    env_path = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env")
    try:
        env = open(env_path, encoding="utf-8").read()
    except Exception:
        sys.exit("cannot read " + env_path)
    m = re.search(r"^TELEGRAM_BOT_TOKEN\s*=\s*(\S+)", env, re.M)
    if not m:
        sys.exit("TELEGRAM_BOT_TOKEN not found in " + env_path)
    return m.group(1).strip()


def send_telegram(chat_id: str, text: str) -> None:
    token = get_bot_token()
    url = ("https://api.telegram.org/bot%s/sendMessage" % token)
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(body).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = r.read().decode()
    import json
    ok = json.loads(resp).get("ok")
    if not ok:
        raise RuntimeError("telegram error: %s" % resp[:200])


def _post_via_core(ch: dict, real: bool = False) -> None:
    """Route a manual post through the core pipeline (runs/events/idempotency)."""
    from tgju_core_integration import init_core, orchestrated_post
    from tgju_core.types import TriggerType
    init_core()
    rows = get_all_prices()
    if not rows:
        sys.exit("ERROR: no rows parsed from tgju.org")

    send_fn = send_telegram if real else None
    res = orchestrated_post(ch, rows, TriggerType.MANUAL, send_fn=send_fn)
    print("run %s | %s | status=%s | msg=%d chars" % (
        res.get("run_id"), ch.get("id"), res.get("status"),
        len(res.get("message") or "")))
    if not res.get("ok") and res.get("error"):
        sys.exit("ERROR: %s" % res.get("error"))


def main():
    ap = argparse.ArgumentParser(description="TGJU Telegram multi-channel")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--preview", metavar="CH", help="channel id (ch1..ch9)")
    ap.add_argument("--post", metavar="CH", help="channel id to post")
    ap.add_argument("--real", action="store_true", help="actually send (default: print)")
    ap.add_argument("--platform", default="telegram", choices=["telegram", "whatsapp"],
                    help="platform to target (default: telegram)")
    args = ap.parse_args()

    if args.platform == "whatsapp":
        _cli_whatsapp(args)
        return

    channels = load_channels()

    if args.list:
        for c in channels:
            n = sum(len(v) for v in (c.get("slug_groups") or {}).values())
            n += len(c.get("slugs") or [])
            print("%-4s %-28s %-12s %2d slug   %-4s  %s" % (
                c.get("id"), c.get("name"), c.get("telegram_id") or "(not set)",
                n, "ON" if c.get("enabled") else "OFF",
                c.get("schedule_minutes")))
        return

    target = args.preview or args.post
    if not target:
        ap.print_help()
        return
    ch = next((c for c in channels if c.get("id") == target), None)
    if not ch:
        sys.exit("unknown channel: %s" % target)

    rows = get_all_prices()
    if not rows:
        sys.exit("ERROR: no rows parsed from tgju.org")

    if args.post:
        if not ch.get("telegram_id"):
            sys.exit("channel %s has no telegram_id — set it in channels.yaml" % target)
        if not args.real:
            print("PREVIEW ONLY — pass --real to post to %s" % ch.get("telegram_id"))
            print("---")
            print(build_for_channel(ch, rows))
            return
        # Route through the core pipeline for full observability
        _post_via_core(ch, real=True)
        log_line("posted %s -> %s" % (target, ch.get("telegram_id")))
    else:
        print(build_for_channel(ch, rows))


def _cli_whatsapp(args):
    """WhatsApp bot CLI: --list (categories) / --preview (menu) / --send-menu <phone> [--real]."""
    import tgju_engine_whatsapp as wa
    data = wa.load_whatsapp()
    if args.list:
        print("WhatsApp interactive bot — categories:")
        for c in data["categories"]:
            n = sum(len(v) for v in (c.get("slug_groups") or {}).values())
            print("  %s %s (code %s, %d slugs)" % (
                c.get("menu_code"), c.get("label"), c.get("id"), n))
        print("mock=%s users=%d" % (data["settings"].get("mock"),
                                    len(data.get("users") or {})))
        return
    target = args.preview or args.post
    if not target:
        print("usage: --platform whatsapp --list | --preview menu | --send-menu <phone> [--real]")
        return
    if target == "menu":
        print(wa.main_menu())
        return
    # --send-menu <phone>
    rows = get_all_prices()
    text = wa.main_menu()
    if not args.real:
        print("PREVIEW ONLY — pass --real to send the menu to %s" % target)
        print("---")
        print(text)
        return
    resp = wa.send_whatsapp(wa.normalize_phone(target), text)
    if not resp.get("ok"):
        sys.exit("ERROR: %s" % resp.get("error"))
    log_line("whatsapp sent menu -> %s mock=%s" % (target, bool(resp.get("mock"))))
    print("sent menu -> %s message_id=%s mock=%s" % (
        target, resp.get("message_id"), bool(resp.get("mock"))))


if __name__ == "__main__":
    main()