# -*- coding: utf-8 -*-
"""WhatsApp interactive bot engine for the TGJU multi-platform control center.

The WhatsApp platform is NOT a broadcast-channel model like Telegram — it is a
SINGLE interactive bot. A WhatsApp user writes to the business number and the
bot answers with menus:

    WhatsApp User ──▶ WhatsApp Bot (webhook) ──▶ Backend ──▶ Price/News/AI
                                                      └────────▶ reply

state/whatsapp.json:
    {
      "settings": {
        "access_token": "", "phone_number_id": "", "verify_token": "",
        "mock": true, "welcome": "سلام! ...",
      },
      "categories": [
        {"id": "currency", "label": "💱 ارز", "slug_groups": {"ارز": ["price_dollar_rl", "price_eur", "price_gbp", "price_aed"], "بانکی": ["bank_usd", "bank_eur", ...]}},
        ...
      ],
      "users": { "98912xxxxxxx": {"state": "menu", "last_menu": "main", ...} }
    }

The bot answers text (no HTML/markdown on the Cloud API). Interactive reply
buttons are NOT used — numbered menu options in plain text keep it simple and
robust (the Cloud API button payloads are fiddly and unreachable from the UI
simulator).
"""
import json
import os
import re
import time
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
WHATSAPP_PATH = os.path.join(STATE_DIR, "whatsapp.json")

GRAPH_BASE = "https://graph.facebook.com/v22.0"

# ── defaults ───────────────────────────────────────────────────────────────
DEFAULT_WELCOME = (
    "سلام 👋 به ربات TGJU خوش آمدید!\n"
    "قیمت لحظه‌ای بازارهای طلا، ارز، سکه، ارز دیجیتال و جهانی را از TGJU دریافت کنید.\n\n"
    "یک گزینه را انتخاب کنید (عدد بفرستید):\n"
    "1️⃣ 💱 قیمت ارز\n"
    "2️⃣ 🏅 قیمت طلا و سکه\n"
    "3️⃣ 📈 بازار جهانی\n"
    "4️⃣ 🪙 ارز دیجیتال\n"
    "5️⃣ 📰 آخرین اخبار\n"
    "6️⃣ 🤖 تحلیل بازار\n"
    "7️⃣ ℹ️ درباره TGJU"
)

MAX_MSG = 4096  # WhatsApp text body limit (approximate, safe)


def _empty() -> dict:
    return {
        "settings": {
            "access_token": "",
            "phone_number_id": "",
            "verify_token": "",
            "mock": True,
            "welcome": DEFAULT_WELCOME,
        },
        "categories": _default_categories(),
        "users": {},
    }


def _default_categories() -> list:
    """Category defaults derived from the real tgju.org slug inventory."""
    return [
        {
            "id": "currency",
            "label": "💱 ارز",
            "menu_code": "1",
            "slug_groups": {
                "ارز آزاد": ["price_dollar_rl", "price_eur", "price_gbp", "price_aed"],
                "ارز بانکی": ["bank_usd", "bank_eur", "bank_gbp", "bank_aed", "bank_chf", "bank_cny", "bank_inr", "bank_aud", "bank_cad"],
            },
        },
        {
            "id": "gold_coin",
            "label": "🏅 طلا و سکه",
            "menu_code": "2",
            "slug_groups": {
                "طلا": ["geram18", "geram24", "mesghal", "ons"],
                "سکه": ["sekee", "sekeb", "nim", "rob", "gerami"],
            },
        },
        {
            "id": "global",
            "label": "📈 بازار جهانی",
            "menu_code": "3",
            "slug_groups": {
                "فلزات": ["ons", "silver", "platinum", "palladium"],
                "شاخص‌ها": ["bourse_dow", "bourse_nasdaq", "bourse_nikkei-225", "bourse_cac-40", "bourse_dax", "bourse_ftse-100", "bourse_hang-seng", "bourse_euro-stoxx-50"],
                "انرژی": ["oil_brent", "oil_opec", "energy_natural_gas", "energy_gasoline_rbob"],
            },
        },
        {
            "id": "crypto",
            "label": "🪙 ارز دیجیتال",
            "menu_code": "4",
            "slug_groups": {
                "اصلی": ["crypto-bitcoin", "crypto-ethereum", "crypto-tether", "crypto-binance-coin", "crypto-solana"],
                "سایر": ["crypto-cardano", "crypto-dogecoin", "crypto-ripple", "crypto-litecoin", "crypto-polkadot"],
            },
        },
        {
            "id": "news",
            "label": "📰 آخرین اخبار",
            "menu_code": "5",
            "slug_groups": {},
        },
        {
            "id": "analysis",
            "label": "🤖 تحلیل بازار",
            "menu_code": "6",
            "slug_groups": {},
        },
        {
            "id": "about",
            "label": "ℹ️ درباره TGJU",
            "menu_code": "7",
            "slug_groups": {},
        },
    ]


# ── config IO ──────────────────────────────────────────────────────────────
def load_whatsapp() -> dict:
    try:
        with open(WHATSAPP_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    base = _empty()
    base.update({k: v for k, v in data.items() if v is not None})
    base.setdefault("settings", _empty()["settings"])
    # merge settings defaults
    s = dict(_empty()["settings"])
    s.update(base["settings"] or {})
    base["settings"] = s
    # normalize categories (fill missing defaults per id)
    cats = {c.get("id"): c for c in base.get("categories") or []}
    for d in _default_categories():
        if d["id"] not in cats:
            cats[d["id"]] = d
    base["categories"] = list(cats.values())
    base.setdefault("users", {})
    return base


def save_whatsapp(data: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(WHATSAPP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def get_wa_settings() -> dict:
    return load_whatsapp()["settings"]


def is_mock() -> bool:
    s = get_wa_settings()
    return bool(s.get("mock")) or not (s.get("access_token") and s.get("phone_number_id"))


# ── phone id / webhook helpers ─────────────────────────────────────────────
def normalize_phone(raw: str) -> str:
    """Digits only, strip leading + / 00 / spaces."""
    d = re.sub(r"\D", "", raw or "")
    return d[-15:] if len(d) > 15 else d


def public_phone(pnid: str) -> str:
    """Best-effort display phone number from a phone_number_id."""
    return pnid or ""


# ── Meta Cloud API ─────────────────────────────────────────────────────────
def _wa_api(method: str, payload: dict, timeout: int = 30) -> dict:
    s = get_wa_settings()
    url = "%s/%s/messages" % (GRAPH_BASE, s["phone_number_id"])
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % s["access_token"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def send_whatsapp(phone: str, text: str) -> dict:
    """Send a plain-text message. mock-aware (never touches the network)."""
    if not phone:
        return {"ok": False, "error": "شماره واتساپ تنظیم نشده است"}
    if is_mock():
        _id = abs(hash("wa:" + phone + ":" + text)) % 10 ** 8
        return {"ok": True, "message_id": "wa-mock-%d" % _id, "mock": True}
    try:
        resp = _wa_api("POST", {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        })
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        return {"ok": False, "error": "HTTP %d: %s" % (e.code, detail)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    mid = (resp.get("messages") or [{}])[0].get("id", "")
    return {"ok": True, "message_id": mid}


def test_credentials() -> dict:
    """GET /<phone_number_id> probe. mock-aware."""
    if is_mock():
        return {"ok": True, "mock": True,
                "message": "حالت آزمایشی (mock) فعال است — بدون توکن، بدون ارسال واقعی"}
    s = get_wa_settings()
    url = "%s/%s?fields=display_phone_number,verified_name" % (GRAPH_BASE, s["phone_number_id"])
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer %s" % s["access_token"]})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        return {"ok": True,
                "display_phone_number": data.get("display_phone_number"),
                "verified_name": data.get("verified_name")}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:300]
        except Exception:
            detail = ""
        return {"ok": False, "error": "HTTP %d: %s" % (e.code, detail)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# ── webhook ────────────────────────────────────────────────────────────────
def verify_webhook(mode: str, token: str) -> bool:
    """GET webhook verification (hub.mode=subscribe / hub.verify_token)."""
    s = get_wa_settings()
    expected = s.get("verify_token") or ""
    return mode == "subscribe" and bool(expected) and token == expected


def process_webhook(payload: dict) -> list:
    """Handle an inbound WhatsApp webhook payload.

    Returns a list of reply dicts: {to, text, message_id?} that the caller
    sends. Handles messages (text only) and echoes nothing else.
    """
    replies = []
    if not isinstance(payload, dict):
        return replies
    entries = payload.get("entry") or []
    for e in entries:
        changes = e.get("changes") or []
        for ch in changes:
            value = ch.get("value") or {}
            contacts = value.get("contacts") or []
            phone = ""
            if contacts:
                phone = normalize_phone((contacts[0].get("wa_id") or ""))
            msgs = value.get("messages") or []
            for m in msgs:
                if m.get("type") != "text":
                    continue
                text = ((m.get("text") or {}).get("body") or "").strip()
                if not phone or not text:
                    continue
                reply_text = handle_message(phone, text)
                replies.append({"to": phone, "text": reply_text,
                                "message_id": m.get("id")})
    return replies


# ── interaction state ──────────────────────────────────────────────────────
def user_state(phone: str) -> dict:
    return {"state": "menu", "last_menu": "main", "updated_at": time.time()}


def get_user(phone: str) -> dict:
    data = load_whatsapp()
    u = data["users"].get(phone) or {}
    base = user_state(phone)
    base.update(u or {})
    return base


def set_user(phone: str, us: dict):
    data = load_whatsapp()
    data["users"][phone] = us
    save_whatsapp(data)


# ── message handlers ───────────────────────────────────────────────────────
def handle_message(phone: str, text: str) -> str:
    """Entry point for one inbound message → reply text."""
    us = get_user(phone)
    us["_phone"] = phone
    low = text.strip().lower()

    # main menu navigation keywords
    if low in ("منو", "menu", "شروع", "start", "خانه", "back") or text.strip() in ("0",):
        return main_menu()

    # category selection by menu code
    if re.fullmatch(r"[1-7]", text.strip()):
        return category_menu(text.strip(), us)

    # sub-category selection (like 1-2, 1-3)
    m = re.fullmatch(r"(\d)-(\d)", text.strip())
    if m:
        return subcategory_prices(text.strip(), us)

    # batch price request (slug keywords)
    if low.startswith("قیمت "):
        return price_by_keyword(low[5:].strip(), us)

    # anything else → unknown + hint
    return "متوجه نشدم 🤔\nعدد گزینه را بفرستید یا «منو» بنویسید." + \
           "\n\n" + main_menu()


def main_menu() -> str:
    s = get_wa_settings()
    return s.get("welcome") or DEFAULT_WELCOME


def list_categories() -> list:
    return [c for c in load_whatsapp()["categories"]]


def category_menu(code: str, us: dict) -> str:
    """A user tapped a numbered top-level menu choice."""
    cats = {c.get("menu_code"): c for c in list_categories()}
    cat = cats.get(code)
    if not cat:
        return "گزینه نامعتبر است. «منو» بنویسید."
    cid = cat["id"]

    # news / analysis / about are immediate
    if cid == "news":
        return news_reply(us)
    if cid == "analysis":
        return analysis_reply(us)
    if cid == "about":
        return about_reply()

    groups = cat.get("slug_groups") or {}
    us["state"] = "category"
    us["last_category"] = cid
    set_user(us["_phone"], us)
    if not groups:
        return "⛔ این دسته هنوز آیتمی ندارد."
    lines = [cat.get("label") or cid, "برای دیدن قیمت‌ها عدد گروه را بفرستید:", ""]
    for i, (g, slugs) in enumerate(groups.items(), 1):
        lines.append("%d. %s" % (i, g))
    lines += ["", "۰ = منو"]
    return "\n".join(lines)


def subcategory_prices(code: str, us: dict) -> str:
    """A user tapped a group inside a category — e.g. '1-2' → currency group 2."""
    parts = code.split("-")
    cid_map = {c.get("menu_code"): c for c in list_categories()}
    cat = cid_map.get(parts[0])
    if not cat:
        return "گزینه نامعتبر است."
    groups = list((cat.get("slug_groups") or {}).items())
    try:
        gname, slugs = groups[int(parts[1]) - 1]
    except (ValueError, IndexError):
        return "گزینه نامعتبر است. «منو» بنویسید."
    rows = _current_rows()
    if not rows:
        return "⛔ داده قیمت در دسترس نیست (tgju.org در دسترس نیست)."
    # filter slugs that exist + have a price
    out = []
    for s in slugs:
        if s in rows and (rows[s].get("price") or rows[s].get("data-price")):
            out.append(s)
            if len(out) >= 8:
                break
    if not out:
        return "⛔ برای این گروه هنوز قیمتی ثبت نشده است."
    lines = ["──── %s ────" % gname]
    for s in out:
        lines.append(_price_line(s, rows[s]))
    lines.append("")
    lines.append("۰ = منو")
    return "\n".join(lines)


def price_by_keyword(kw: str, us: dict) -> str:
    """'قیمت دلار' → best-matching slug price."""
    rows = _current_rows()
    if not rows:
        return "⛔ داده قیمت در دسترس نیست."
    kw = kw.replace("٪", "").strip()
    hits = []
    for s, r in rows.items():
        name = (r.get("name") or "").strip()
        if not name:
            continue
        if kw == name or kw in name:
            hits.append((s, r))
    if not hits:
        return "چیزی برای «%s» پیدا نشد. «منو» بنویسید." % kw
    lines = []
    for s, r in hits[:5]:
        lines.append(_price_line(s, r))
    lines.append("")
    lines.append("۰ = منو")
    return "\n".join(lines)


def news_reply(us: dict) -> str:
    """Latest TGJU news (reuses the Telegram news engine; strips HTML)."""
    try:
        from tgju_engine_news import channel_articles, analysis_line, pick_rotating
        arts = channel_articles([], [], limit=5) or []
        if not arts:
            try:
                from tgju_engine_fallback import load_fallback_news
                arts = load_fallback_news("_whatsapp") or []
            except Exception:
                arts = []
        if not arts:
            return "⛔ خبری در دسترس نیست (tgju.org در دسترس نیست)."
        lines = ["📰 آخرین اخبار TGJU:", ""]
        for a in arts[:5]:
            lines.append("• %s\n  %s" % (a["text"], a["url"]))
        lines += ["", "۰ = منو"]
        return "\n".join(lines)
    except Exception as e:
        return "⛔ خطا در دریافت خبر: %s" % str(e)[:120]


def analysis_reply(us: dict) -> str:
    """AI market analysis (uses the shared AI engine with the global/market channel)."""
    try:
        from tgju_engine_ai import run_analysis
        from tgju_engine_config import load_ai_config
        cfg = load_ai_config()
        # Build a synthetic channel so the shared engine works unchanged
        channel = {
            "id": "ch6",
            "name": "بازار جهانی",
            "title": "بازار جهانی",
            "slug_groups": {"جهانی": ["ons", "silver", "bourse_dow", "oil_brent"]},
            "slugs": ["ons", "silver", "bourse_dow", "oil_brent"],
            "tags": [],
            "news_categories": [],
        }
        rows = _current_rows()
        res = run_analysis(cfg, channel, rows)
        if not res.get("ok"):
            return ("⛔ تحلیل در دسترس نیست: %s\n\n"
                    "فعال‌سازی: داشبورد ← هوش مصنوعی ← کانال ch6 ← تحلیل فعال شود.") % res.get("error", "نامشخص")
        outp = "🤖 تحلیل بازار (AI):\n\n" + res["text"]
        outp += "\n\n۰ = منو"
        return outp
    except Exception as e:
        return "⛔ خطا در تحلیل: %s" % str(e)[:120]


def about_reply() -> str:
    s = get_wa_settings()
    return (
        "ℹ️ TGJU — سامانه لحظه‌ای قیمت بازار ایران 🇮🇷\n\n"
        "منبع داده: tgju.org\n"
        "گزینه‌ها:\n"
        "• قیمت ارز، طلا، سکه، ارز دیجیتال و بازار جهانی\n"
        "• اخبار و تحلیل بازار\n\n"
        "نوشتن «قیمت دلار» یا «قیمت طلا» هم جواب می‌گیرید.\n\n"
        "۰ = منو"
    ) if not s.get("about") else s["about"]


def phone_key(us: dict) -> str:
    """Best-effort phone key from user state (stored by handler)."""
    return us.get("_phone") or ""


# ── shared data access ─────────────────────────────────────────────────────
def _current_rows() -> dict:
    """Live rows from RUNTIME cache (never touch the network from handlers).

    Manual slug overrides (state/slug_overrides.json — the «داده‌ها و لینک‌ها»
    tab) are applied HERE so WhatsApp serves exactly what Telegram/Bale serve:
    one TGJU source, one override layer, every platform identical.
    """
    try:
        from tgju_platform import RUNTIME
        rows = RUNTIME.get("last_rows") or {}
    except Exception:
        return {}
    try:
        from tgju_engine_orchestrator import apply_slug_overrides
        from tgju_engine_config import load_slug_overrides
        ovs = load_slug_overrides()
        if ovs and rows:
            rows = {s: apply_slug_overrides(s, r, ovs) for s, r in rows.items()}
    except Exception:
        pass
    return rows


def _fmt_price(slug: str, row: dict) -> str:
    """One line: name — price unit ▲▼ pct (plain text, Persian digits)."""
    try:
        from tgju_engine_format import fmt_price, slug_unit
        raw = row.get("price") or row.get("data-price") or ""
        unit = slug_unit(slug, {})
        price = fmt_price(slug, raw, unit)
        # append the unit label (fmt_price converts but never labels)
        if unit and unit != "نقطه":
            price = "%s %s" % (price, unit)
        # change arrow + pct
        d = row.get("dir") or ""
        pct = row.get("change_pct") or ""
        arrow = "▲" if d == "high" else ("▼" if d == "low" else "")
        extra = " %s %s٪" % (arrow, pct) if (arrow or pct) else ""
        return "• %s : %s%s" % (row.get("name") or slug, price, extra)
    except Exception:
        return "• %s : %s" % (row.get("name") or slug, row.get("price") or "")


def _price_line(slug: str, row: dict) -> str:
    return _fmt_price(slug, row)


# ── simulate a conversation (UI simulator + tests) ────────────────────────
def simulate_conversation(phone: str, steps: list) -> dict:
    """Run a sequence of user messages through handle_message.

    steps: list of strings (the user's messages). Returns
    {"replies": [{"in": ..., "out": ...}], "final": ...}.
    """
    replies = []
    for msg in steps:
        text = msg.get("text") if isinstance(msg, dict) else str(msg)
        out = handle_message(phone, text)
        replies.append({"in": text, "out": out})
    return {"replies": replies,
            "final": replies[-1]["out"] if replies else ""}


# ── categories CRUD helpers (backend convenience) ─────────────────────────
def get_categories() -> list:
    return list_categories()


def save_categories(cats: list):
    data = load_whatsapp()
    data["categories"] = cats
    save_whatsapp(data)


def about_text() -> str:
    return get_wa_settings().get("about") or about_reply()