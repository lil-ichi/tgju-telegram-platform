# -*- coding: utf-8 -*-
"""Independent Telegram AI assistant configuration and grounded Q&A engine.

This module intentionally does not import or use ``tgju_engine_bot``.  The
assistant token and runtime are separate from the price-publishing bot.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Callable, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSISTANT_CONFIG_PATH = os.path.join(BASE_DIR, "state", "ai_assistant.json")
ASSISTANT_CONVERSATIONS_PATH = os.path.join(BASE_DIR, "state", "assistant_conversations.json")

DEFAULT_SYSTEM_PROMPT = (
    "تو دستیار هوشمند و تحلیل‌گر ارشد TGJU (شبکه اطلاع‌رسانی طلا و ارز) هستی. "
    "نام تو فقط «دستیار هوشمند TGJU» است — هرگز خودت را Muse Spark، Spark، Meta AI، Claude، ChatGPT یا نام دیگری معرفی نکن. "
    "پاسخ‌هایت باید بسیار روان، گرم، مؤدبانه، طبیعی و انسانی باشند؛ مانند یک کارشناس آگاه و همکار صمیمی در بازار مالی ایران، "
    "نه یک ربات ماشینی یا فرم‌محور. از مقدمه‌های کلیشه‌ای و تکراری («به عنوان یک هوش مصنوعی...»، «بر اساس اطلاعات دریافتی...») "
    "پرهیز کن و پاسخ را مستقیم و شفاف بده. در پاسخ به پرسش‌های بازار و قیمت، از داده‌های زنده و دانش مستند استفاده کن و اگر موضوعی "
    "ثبت نشده بود صادقانه و محترمانه راهنمایی کن."
)
DEFAULT_ASSISTANT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "token": "",
    "bot_username": "",
    "provider_mode": "shared",
    "provider": "",
    "model": "",
    "api": {
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout_seconds": 60,
        "max_tokens": 1400,
    },
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "welcome_message": "سلام! در خدمت شما هستم. پرسش خود را درباره بازارها و اطلاعات TGJU بفرمایید.",
    "fallback_message": "در حال حاضر اطلاعات مستندی در این زمینه ثبت نشده است. اگر مایلید درباره قیمت‌های لحظه‌ای یا خدمات TGJU سوالی دارید بفرمایید.",
    "knowledge": {
        "enabled": True,
        "top_k": 4,
        "max_context_chars": 3000,
        "answer_only_from_kb": False,
        "show_sources": True,
    },
    "conversation": {
        "history_messages": 6,
        "max_question_chars": 1200,
        "private_chats": True,
        "group_chats": False,
    },
    "polling": {
        "enabled": False,
        "timeout_seconds": 25,
        "offset": 0,
    },
    "stats": {
        "questions": 0,
        "answered": 0,
        "fallbacks": 0,
        "last_message_at": "",
    },
}


def _deep_merge(base: dict, incoming: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif value is not None:
            merged[key] = value
    return merged


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def normalize_assistant_config(raw: Optional[dict], existing: Optional[dict] = None) -> dict:
    """Merge saved/admin input with defaults and enforce safe limits."""
    prior = _deep_merge(DEFAULT_ASSISTANT_CONFIG, existing or {})
    incoming = dict(raw or {})
    if not (incoming.get("token") or "").strip() and prior.get("token"):
        incoming.pop("token", None)
    incoming_api = dict(incoming.get("api") or {})
    if not str(incoming_api.get("api_key") or "").strip() and prior["api"].get("api_key"):
        incoming_api.pop("api_key", None)
    if "api" in incoming:
        incoming["api"] = incoming_api
    cfg = _deep_merge(prior, incoming)

    cfg["enabled"] = bool(cfg.get("enabled", False))
    cfg["token"] = str(cfg.get("token") or "").strip()
    cfg["bot_username"] = str(cfg.get("bot_username") or "").strip().lstrip("@")
    cfg["provider_mode"] = "custom" if cfg.get("provider_mode") == "custom" else "shared"
    cfg["provider"] = str(cfg.get("provider") or "").strip()
    cfg["model"] = str(cfg.get("model") or "").strip()
    api = cfg["api"]
    api["base_url"] = str(api.get("base_url") or "").strip().rstrip("/")
    api["api_key"] = str(api.get("api_key") or "").strip()
    api["model"] = str(api.get("model") or "").strip()
    api["timeout_seconds"] = _bounded_int(api.get("timeout_seconds"), 60, 8, 180)
    api["max_tokens"] = _bounded_int(api.get("max_tokens"), 1400, 128, 8000)
    cfg["system_prompt"] = str(cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT).strip()
    cfg["welcome_message"] = str(cfg.get("welcome_message") or "").strip()
    cfg["fallback_message"] = str(
        cfg.get("fallback_message") or DEFAULT_ASSISTANT_CONFIG["fallback_message"]
    ).strip()

    knowledge = cfg["knowledge"]
    knowledge["enabled"] = bool(knowledge.get("enabled", True))
    knowledge["top_k"] = _bounded_int(knowledge.get("top_k"), 4, 1, 10)
    knowledge["max_context_chars"] = _bounded_int(
        knowledge.get("max_context_chars"), 3000, 500, 12000
    )
    knowledge["answer_only_from_kb"] = bool(knowledge.get("answer_only_from_kb", False))
    knowledge["show_sources"] = bool(knowledge.get("show_sources", True))

    conversation = cfg["conversation"]
    conversation["history_messages"] = _bounded_int(
        conversation.get("history_messages"), 6, 0, 20
    )
    conversation["max_question_chars"] = _bounded_int(
        conversation.get("max_question_chars"), 1200, 100, 4000
    )
    conversation["private_chats"] = bool(conversation.get("private_chats", True))
    conversation["group_chats"] = bool(conversation.get("group_chats", False))

    polling = cfg["polling"]
    polling["enabled"] = bool(polling.get("enabled", False))
    polling["timeout_seconds"] = _bounded_int(
        polling.get("timeout_seconds"), 25, 5, 50
    )
    polling["offset"] = _bounded_int(polling.get("offset"), 0, 0, 2_147_483_647)
    return cfg


def load_assistant_config() -> dict:
    try:
        with open(ASSISTANT_CONFIG_PATH, encoding="utf-8") as handle:
            saved = json.load(handle)
    except Exception:
        saved = {}
    return normalize_assistant_config(saved)


def save_assistant_config(updates: dict) -> dict:
    existing = load_assistant_config()
    cfg = normalize_assistant_config(updates, existing=existing)
    os.makedirs(os.path.dirname(ASSISTANT_CONFIG_PATH), exist_ok=True)
    with open(ASSISTANT_CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, ensure_ascii=False, indent=2)
    return cfg


def load_conversations() -> dict:
    try:
        with open(ASSISTANT_CONVERSATIONS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_conversations(data: dict) -> None:
    os.makedirs(os.path.dirname(ASSISTANT_CONVERSATIONS_PATH), exist_ok=True)
    with open(ASSISTANT_CONVERSATIONS_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def conversation_history(chat_id: Any) -> List[dict]:
    return list(load_conversations().get(str(chat_id)) or [])


def remember_exchange(chat_id: Any, question: str, answer: str, history_messages: int) -> None:
    if history_messages <= 0:
        return
    data = load_conversations()
    key = str(chat_id)
    history = list(data.get(key) or [])
    history.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ])
    data[key] = history[-(history_messages * 2):]
    save_conversations(data)


def clear_conversation(chat_id: Any) -> None:
    data = load_conversations()
    data.pop(str(chat_id), None)
    save_conversations(data)


def test_assistant_token(token: str) -> dict:
    """Validate only the assistant token via Telegram getMe."""
    import urllib.error
    import urllib.request

    clean = str(token or "").strip()
    if not clean:
        return {"ok": False, "error": "token required"}
    request = urllib.request.Request(
        "https://api.telegram.org/bot%s/getMe" % clean,
        headers={"User-Agent": "tgju-ai-assistant/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        if not payload.get("ok"):
            return {"ok": False, "error": payload.get("description") or "invalid token"}
        bot = payload.get("result") or {}
        return {
            "ok": True,
            "bot_id": bot.get("id"),
            "bot_name": bot.get("first_name") or "",
            "bot_username": bot.get("username") or "",
        }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "HTTP %d: %s" % (exc.code, exc.reason)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _token_preview(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 10:
        return token[:3] + "…" + token[-2:]
    return token[:6] + "…" + token[-4:]


def public_assistant_config(config: Optional[dict] = None) -> dict:
    """Return dashboard-safe settings without exposing the bot token."""
    cfg = copy.deepcopy(config or load_assistant_config())
    token = str(cfg.pop("token", "") or "")
    cfg["token_set"] = bool(token)
    cfg["token_preview"] = _token_preview(token)
    api = cfg.setdefault("api", {})
    api_key = str(api.pop("api_key", "") or "")
    api["api_key_set"] = bool(api_key)
    api["api_key_preview"] = (api_key[:4] + "…" + api_key[-4:]) if api_key else ""
    return cfg


def assistant_provider_config(overrides: Optional[dict] = None) -> dict:
    cfg = load_assistant_config()
    api = dict(cfg.get("api") or {})
    supplied = dict(overrides or {})
    if not str(supplied.get("api_key") or "").strip():
        supplied.pop("api_key", None)
    api.update({key: value for key, value in supplied.items() if value is not None})
    return {
        "label": "Assistant custom API",
        "kind": "openai_compat",
        "enabled": True,
        "base_url": str(api.get("base_url") or "").strip().rstrip("/"),
        "api_key": str(api.get("api_key") or "").strip(),
        "model": str(api.get("model") or "").strip(),
    }


def list_assistant_models(overrides: Optional[dict] = None) -> dict:
    from tgju_engine_ai import list_provider_models
    return list_provider_models(assistant_provider_config(overrides))


def test_assistant_provider(overrides: Optional[dict] = None) -> dict:
    from tgju_engine_ai import test_provider
    return test_provider(assistant_provider_config(overrides))


def _source_summaries(matches: List[dict]) -> List[dict]:
    return [
        {
            "id": str(item.get("source_id") or ""),
            "title": str(item.get("title") or ""),
            "score": item.get("score", 0),
        }
        for item in matches
    ]


_TGJU_TERMS = (
    "tgju", "طلا", "سکه", "دلار", "یورو", "درهم", "ارز", "بورس", "سهام",
    "رمزارز", "ارز دیجیتال", "بیت کوین", "بیت‌کوین", "تتر", "نفت", "انس", "نقره",
    "مظنه", "آبشده", "شاخص بازار", "نرخ ارز", "بازار مالی", "فلزات", "فارکس",
    "gold", "dollar", "crypto", "bitcoin", "stock market", "exchange rate",
    "امامی", "بهار آزادی", "نیم سکه", "ربع سکه", "سکه گرمی", "حباب", "حباب سکه",
    "سبزه میدان", "سبزه میدون", "هرات", "سلیمانیه", "دوبی", "صرافی", "صرافی ملی",
    "قیمت", "نرخ", "ارزش", "بازار", "تورم", "سود بانکی", "کارمزد", "تکنیکال", "فاندامنتال",
    "اتریوم", "ethereum", "tether", "usdt", "btc", "اونس", "گرم طلا", "عیار",
)


def is_tgju_related(text: str) -> bool:
    """Route market/TGJU questions to retrieval; leave ordinary chat model-only."""
    normalized = str(text or "").lower().replace("\u200c", " ")
    return any(term.replace("\u200c", " ") in normalized for term in _TGJU_TERMS)


def get_live_market_context(question: str) -> str:
    """Extract relevant live prices from TGJU runtime or fallback cache."""
    q_norm = str(question or "").lower().replace("\u200c", " ")
    triggers = (
        "چند", "چنده", "قیمت", "نرخ", "ارزش", "بازار", "مظنه", "بالا", "پایین",
        "امروز", "الان", "لحظه", "چقدر", "سکه", "طلا", "دلار", "ارز", "تتر", "بیت",
        "euro", "dollar", "ons", "gold", "coin", "tether", "bitcoin", "price"
    )
    if not any(trig in q_norm for trig in triggers):
        return ""

    rows = {}
    try:
        from tgju_core.state import cached_rows
        rows = cached_rows() or {}
    except Exception:
        pass
    if not rows:
        try:
            from tgju_engine_fallback import PRICES_FILE
            if os.path.exists(PRICES_FILE):
                with open(PRICES_FILE, encoding="utf-8") as f:
                    rows = json.load(f)
                    rows.pop("_saved_at", None)
        except Exception:
            pass

    if not rows:
        return ""

    from tgju_engine_format import fmt_price, slug_unit, direction_arrow

    mapping = [
        (("سکه", "امامی", "بهار آزادی", "تمام سکه"), ["sekee", "sekeb"]),
        (("نیم سکه", "نیم"), ["nim"]),
        (("ربع سکه", "ربع"), ["rob"]),
        (("سکه گرمی", "گرمی"), ["gerami"]),
        (("طلای ۱۸", "طلا 18", "گرم طلا", "طلا ۱۸"), ["geram18"]),
        (("مظنه", "آبشده", "مثقال"), ["mesghal"]),
        (("انس", "اونس", "طلای جهانی"), ["ons"]),
        (("دلار", "usd", "dollar"), ["price_dollar_rl"]),
        (("یورو", "eur", "euro"), ["price_eur"]),
        (("درهم", "aed"), ["price_aed"]),
        (("پوند", "gbp"), ["price_gbp"]),
        (("تتر", "usdt", "tether"), ["crypto-tether"]),
        (("بیت کوین", "بیت‌کوین", "btc", "bitcoin"), ["crypto-bitcoin"]),
        (("اتریوم", "eth", "ethereum"), ["crypto-ethereum"]),
        (("نقره", "silver"), ["silver"]),
        (("نفت", "برنت"), ["oil_brent"]),
        (("بورس", "شاخص"), ["gc30"]),
    ]

    matched_slugs = []
    for keywords, slugs in mapping:
        if any(kw in q_norm for kw in keywords):
            for s in slugs:
                if s in rows and s not in matched_slugs:
                    matched_slugs.append(s)

    if not matched_slugs and any(w in q_norm for w in ("قیمت ها", "قیمتها", "وضعیت بازار", "نرخ ها", "نرخها", "بازار امروز")):
        core = ["sekee", "geram18", "mesghal", "price_dollar_rl", "crypto-tether", "crypto-bitcoin", "ons"]
        matched_slugs = [s for s in core if s in rows]

    if not matched_slugs:
        return ""

    lines = []
    for s in matched_slugs[:8]:
        item = rows.get(s) or {}
        p = item.get("price")
        if not p:
            continue
        name = item.get("name") or s
        unit = slug_unit(s)
        formatted_price = fmt_price(s, p, unit)
        pct = item.get("change_pct") or ""
        arrow = direction_arrow(item)
        pct_str = f" ({pct}% {arrow})" if pct else ""
        lines.append(f"• {name}: {formatted_price} {unit}{pct_str}")

    return "\n".join(lines)


_IDENTITY_LEAK_PATTERNS = [
    "Muse Spark", "MuseSpark", "muse spark",
    "Meta AI", "MetaAI",
]

def _sanitize_identity(text: str) -> str:
    """Strip upstream model self-identification; force TGJU identity."""
    import re
    out = str(text or "")
    for pat in _IDENTITY_LEAK_PATTERNS:
        if pat.lower() in out.lower():
            out = re.sub(re.escape(pat), "دستیار هوشمند TGJU", out, flags=re.IGNORECASE)
    out = re.sub(r"(?i)\bI am Muse\b[^.\n]*", "من دستیار هوشمند TGJU هستم", out)
    out = re.sub(r"(?i)من Muse[^\n.]*", "من دستیار هوشمند TGJU هستم", out)
    out = re.sub(r"(?i)I am (?:Meta AI|Claude|ChatGPT|Gemini)[^.\n]*", "من دستیار هوشمند TGJU هستم", out)
    return out.strip()


def _build_prompt(
    config: dict,
    question: str,
    matches: List[dict],
    history: Optional[List[dict]] = None,
    use_knowledge: bool = True,
    live_prices: str = "",
) -> str:
    context_parts = []
    remaining = config["knowledge"]["max_context_chars"]
    for index, item in enumerate(matches, 1):
        header = "منبع %d — %s:\n" % (index, item.get("title") or "بدون عنوان")
        if remaining <= len(header):
            break
        text = (item.get("text") or "").strip()
        allowed = remaining - len(header)
        clipped = text[:allowed]
        context_parts.append(header + clipped)
        remaining -= len(header) + len(clipped) + 2
        if len(clipped) < len(text):
            break
    context = "\n\n".join(context_parts)
    if use_knowledge and matches:
        response_rule = (
            "از دانش بازیابی شده به عنوان مرجع اصلی استفاده کن، اما جواب را طبیعی و یکپارچه "
            "بنویس و بی‌دلیل درباره فرایند بازیابی صحبت نکن."
        )
        knowledge_section = f"دانش بازیابی شده:\n{context}"
    elif use_knowledge:
        response_rule = (
            "منبع مرتبطی در پایگاه دانش پیدا نشد. با دانش عمومی خود طبیعی پاسخ بده، اما "
            "قیمت یا داده لحظه‌ای را حدس نزن و محدودیت اطلاعات را فقط در صورت نیاز روشن کن."
        )
        knowledge_section = "منبع مرتبطی در پایگاه دانش پیدا نشد."
    else:
        response_rule = (
            "این یک گفت‌وگوی عادی است. طبیعی و انسانی پاسخ بده، لحن کاربر را بفهم، "
            "از پاسخ قالبی و اشاره بی‌دلیل به TGJU یا پایگاه دانش خودداری کن."
        )
        knowledge_section = ""
    if config["knowledge"].get("answer_only_from_kb", False) and use_knowledge:
        response_rule = "فقط بر اساس دانش ارائه شده پاسخ بده و هیچ واقعیتی را حدس نزن."
    history_lines = []
    for item in history or []:
        role = "کاربر" if item.get("role") == "user" else "دستیار"
        content = str(item.get("content") or "").strip()
        if content:
            history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines) or "(بدون سابقه)"
    knowledge_block = (knowledge_section + "\n\n") if knowledge_section else ""
    live_block = (f"نرخ‌های لحظه‌ای بازار (سامانه TGJU):\n{live_prices}\n\n") if live_prices else ""
    return (
        f"راهنمای این پاسخ: {response_rule}\n"
        "پاسخ را مستقیم و روان بنویس؛ از مقدمه‌ها و هشدارهای تکراری خودداری کن.\n\n"
        f"{live_block}"
        f"{knowledge_block}"
        f"سابقه کوتاه گفت‌وگو:\n{history_text}\n\n"
        f"پرسش کاربر:\n{question.strip()}\n\n"
        "پاسخ نهایی:"
    )


def answer_question(
    question: str,
    *,
    config: Optional[dict] = None,
    kb_search: Optional[Callable[..., List[dict]]] = None,
    provider_runner: Optional[Callable[..., dict]] = None,
    ai_config: Optional[dict] = None,
    history: Optional[List[dict]] = None,
) -> dict:
    """Retrieve KB evidence, live prices and answer through the configured AI provider."""
    cfg = normalize_assistant_config(config or load_assistant_config())
    clean_question = str(question or "").strip()
    if not clean_question:
        return {"ok": False, "error": "question is required"}
    max_chars = cfg["conversation"]["max_question_chars"]
    if len(clean_question) > max_chars:
        return {"ok": False, "error": "question is too long", "max_chars": max_chars}

    related = is_tgju_related(clean_question)
    use_knowledge = bool(related and cfg["knowledge"].get("enabled", True))
    matches: List[dict] = []
    if use_knowledge:
        if kb_search is None:
            from tgju_engine_kb import search_kb
            kb_search = search_kb
        matches = kb_search(clean_question, top_k=cfg["knowledge"]["top_k"])

    sources = _source_summaries(matches)
    if use_knowledge and cfg["knowledge"].get("answer_only_from_kb", False) and not matches:
        return {
            "ok": True,
            "answer": cfg["fallback_message"],
            "grounded": False,
            "fallback": True,
            "sources": [],
            "provider": "",
            "model": "",
            "latency_ms": 0,
        }

    live_prices = get_live_market_context(clean_question) if related else ""

    if cfg.get("provider_mode") == "custom":
        custom = {
            "label": "Assistant custom API",
            "kind": "openai_compat",
            "enabled": True,
            "base_url": cfg["api"]["base_url"],
            "api_key": cfg["api"]["api_key"],
            "model": cfg["api"]["model"],
        }
        ai_cfg = {"providers": {"assistant_custom": custom}}
        preferred = "assistant_custom"
        max_tokens = cfg["api"]["max_tokens"]
        timeout_s = cfg["api"]["timeout_seconds"]
        response_model = cfg["api"]["model"]
    else:
        if ai_config is None:
            from tgju_engine_ai import load_ai_config
            ai_config = load_ai_config()
        ai_cfg = copy.deepcopy(ai_config)
        preferred = cfg.get("provider") or None
        if preferred and preferred in (ai_cfg.get("providers") or {}) and cfg.get("model"):
            ai_cfg["providers"][preferred]["model"] = cfg["model"]
        max_tokens = 1400
        timeout_s = 60
        response_model = cfg.get("model") or ""

    if provider_runner is None:
        from tgju_engine_ai import try_providers
        provider_runner = try_providers
    prompt = _build_prompt(
        cfg, clean_question, matches, history=history, use_knowledge=use_knowledge, live_prices=live_prices
    )
    _system_prompt = cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    try:
        result = provider_runner(
            ai_cfg,
            prompt,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            job_id="assistant_answer",
            preferred_provider=preferred,
            system=_system_prompt,
        )
    except TypeError:
        result = provider_runner(
            ai_cfg,
            prompt,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            job_id="assistant_answer",
            preferred_provider=preferred,
        )
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "AI provider did not answer",
            "grounded": bool(matches),
            "sources": sources,
            "provider": result.get("provider") or "",
            "model": result.get("model") or "",
            "latency_ms": result.get("latency_ms", 0),
            "ttft_ms": result.get("ttft_ms", result.get("latency_ms", 0)),
        }
    raw_answer = (result.get("text") or "").strip()
    raw_answer = _sanitize_identity(raw_answer)
    latency_ms = result.get("latency_ms", 0)
    ttft_ms = result.get("ttft_ms", latency_ms)
    return {
        "ok": True,
        "answer": raw_answer,
        "grounded": bool(matches),
        "fallback": False,
        "sources": sources,
        "provider": result.get("provider") or preferred or "",
        "model": result.get("model") or response_model,
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms,
        "route": "tgju" if related else "conversation",
        "knowledge_used": bool(matches),
        "live_prices_used": bool(live_prices),
    }


def answer_question_stream(
    question: str,
    *,
    config: Optional[dict] = None,
    kb_search: Optional[Callable[..., List[dict]]] = None,
    ai_config: Optional[dict] = None,
    history: Optional[List[dict]] = None,
):
    """Generator streaming answer tokens and telemetry for real-time SSE."""
    cfg = normalize_assistant_config(config or load_assistant_config())
    clean_question = str(question or "").strip()
    if not clean_question:
        yield {"type": "error", "error": "question is required"}
        return

    max_chars = cfg["conversation"]["max_question_chars"]
    if len(clean_question) > max_chars:
        yield {"type": "error", "error": "question is too long", "max_chars": max_chars}
        return

    related = is_tgju_related(clean_question)
    use_knowledge = bool(related and cfg["knowledge"].get("enabled", True))
    matches: List[dict] = []
    if use_knowledge:
        if kb_search is None:
            from tgju_engine_kb import search_kb
            kb_search = search_kb
        matches = kb_search(clean_question, top_k=cfg["knowledge"]["top_k"])

    sources = _source_summaries(matches)
    if use_knowledge and cfg["knowledge"].get("answer_only_from_kb", False) and not matches:
        yield {
            "type": "done",
            "answer": cfg["fallback_message"],
            "grounded": False,
            "fallback": True,
            "sources": [],
            "provider": "",
            "model": "",
            "ttft_ms": 0,
            "latency_ms": 0,
        }
        return

    live_prices = get_live_market_context(clean_question) if related else ""

    if cfg.get("provider_mode") == "custom":
        custom = {
            "label": "Assistant custom API",
            "kind": "openai_compat",
            "enabled": True,
            "base_url": cfg["api"]["base_url"],
            "api_key": cfg["api"]["api_key"],
            "model": cfg["api"]["model"],
        }
        provider_dict = custom
        max_tokens = cfg["api"]["max_tokens"]
        timeout_s = cfg["api"]["timeout_seconds"]
        provider_name = "assistant_custom"
    else:
        if ai_config is None:
            from tgju_engine_ai import load_ai_config
            ai_config = load_ai_config()
        ai_cfg = copy.deepcopy(ai_config)
        preferred = cfg.get("provider") or None
        from tgju_engine_ai import _enabled_providers_order
        ordered = _enabled_providers_order(ai_cfg, preferred_provider=preferred)
        if not ordered:
            yield {"type": "error", "error": "no AI provider configured"}
            return
        provider_name, provider_dict = ordered[0]
        if cfg.get("model"):
            provider_dict["model"] = cfg["model"]
        max_tokens = 1400
        timeout_s = 60

    prompt = _build_prompt(
        cfg, clean_question, matches, history=history, use_knowledge=use_knowledge, live_prices=live_prices
    )
    system_prompt = cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT

    from tgju_engine_ai import stream_chat_completion
    full_text = ""
    ttft_ms = 0
    latency_ms = 0
    try:
        for chunk in stream_chat_completion(
            provider_dict, prompt, max_tokens=max_tokens, timeout=timeout_s, system=system_prompt
        ):
            if chunk["type"] == "token":
                if chunk.get("is_first"):
                    ttft_ms = chunk.get("ttft_ms", 0)
                    yield {
                        "type": "meta",
                        "ttft_ms": ttft_ms,
                        "provider": provider_name,
                        "model": provider_dict.get("model", ""),
                        "sources": sources,
                        "grounded": bool(matches or live_prices),
                    }
                token_text = chunk.get("content", "")
                full_text += token_text
                yield {"type": "token", "content": token_text, "ttft_ms": ttft_ms}
            elif chunk["type"] == "done":
                latency_ms = chunk.get("latency_ms", 0)
                if not ttft_ms:
                    ttft_ms = chunk.get("ttft_ms", latency_ms)
                break
    except Exception as exc:
        yield {"type": "error", "error": str(exc)[:200]}
        return

    clean_answer = _sanitize_identity(full_text.strip())
    yield {
        "type": "done",
        "answer": clean_answer,
        "grounded": bool(matches or live_prices),
        "fallback": False,
        "sources": sources,
        "provider": provider_name,
        "model": provider_dict.get("model", ""),
        "ttft_ms": ttft_ms,
        "latency_ms": latency_ms,
        "route": "tgju" if related else "conversation",
        "knowledge_used": bool(matches),
        "live_prices_used": bool(live_prices),
    }


ASSISTANT_RUNTIME = {
    "status": "stopped",
    "last_poll_at": "",
    "last_message_at": "",
    "last_error": "",
    "processed": 0,
}


def _telegram_api_call(token: str, method: str, payload: dict, timeout: int = 35) -> dict:
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    from tgju_engine_ai import get_http_session
    session = get_http_session()
    if session:
        try:
            resp = session.post(url, json=payload, timeout=timeout, headers={"User-Agent": "tgju-ai-assistant/1.0"})
            result = resp.json()
        except Exception as exc:
            import urllib.error
            import urllib.request
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "tgju-ai-assistant/1.0"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    result = json.loads(response.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as hexc:
                detail = hexc.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError("Telegram HTTP %d: %s" % (hexc.code, detail)) from hexc
    else:
        import urllib.error
        import urllib.request
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "tgju-ai-assistant/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError("Telegram HTTP %d: %s" % (exc.code, detail)) from exc

    if not result.get("ok"):
        raise RuntimeError(result.get("description") or "Telegram API request failed")
    return result


def telegram_get_updates(token: str, offset: int, timeout_seconds: int) -> List[dict]:
    result = _telegram_api_call(
        token,
        "getUpdates",
        {
            "offset": offset,
            "timeout": timeout_seconds,
            "allowed_updates": ["message"],
        },
        timeout=timeout_seconds + 10,
    )
    return result.get("result") or []


def telegram_send_message(token: str, chat_id: Any, text: str) -> dict:
    return _telegram_api_call(
        token,
        "sendMessage",
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=25,
    )


def telegram_send_chat_action(token: str, chat_id: Any, action: str = "typing") -> dict:
    """Send immediate typing indicator to Telegram so the user perceives zero delay."""
    try:
        return _telegram_api_call(
            token,
            "sendChatAction",
            {"chat_id": chat_id, "action": action},
            timeout=8,
        )
    except Exception:
        return {}


def _reply_text(config: dict, message_text: str, answer_fn: Callable[..., dict], history: Optional[List[dict]] = None) -> tuple:
    command = message_text.strip().split()[0].lower()
    if command.startswith("/start"):
        return config["welcome_message"], None
    if command.startswith("/help"):
        return (
            "پرسش خود را درباره بازارها و اطلاعات موجود در پایگاه دانش TGJU بنویسید.\n"
            "دستورها: /start شروع · /help راهنما · /reset پاک کردن حافظه گفتگو",
            None,
        )
    if command.startswith("/reset"):
        return "حافظه این گفت‌وگو پاک شد.", None

    result = answer_fn(message_text, config=config, history=history or [])
    if not result.get("ok"):
        return "در حال حاضر پاسخ‌گویی ممکن نیست. لطفاً کمی بعد دوباره تلاش کنید.", result
    answer = result.get("answer") or config["fallback_message"]
    if config["knowledge"].get("show_sources", True) and result.get("sources"):
        titles = []
        for source in result["sources"]:
            title = str(source.get("title") or source.get("id") or "").strip()
            if title and title not in titles:
                titles.append(title)
        if titles:
            answer += "\n\nمنابع: " + "، ".join(titles)
    return answer, result


def _split_message(text: str, limit: int = 4000) -> List[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    parts = []
    while len(clean) > limit:
        cut = clean.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = clean.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(clean[:cut].strip())
        clean = clean[cut:].strip()
    if clean:
        parts.append(clean)
    return parts


def poll_assistant_once(
    *,
    config: Optional[dict] = None,
    get_updates: Callable[[str, int, int], List[dict]] = telegram_get_updates,
    send_message: Callable[[str, Any, str], dict] = telegram_send_message,
    send_chat_action: Callable[[str, Any, str], dict] = telegram_send_chat_action,
    answer_fn: Callable[..., dict] = answer_question,
) -> dict:
    """Run one getUpdates cycle. Dependency injection keeps it deterministic in tests."""
    cfg = normalize_assistant_config(config or load_assistant_config())
    token = cfg.get("token") or ""
    offset = cfg["polling"].get("offset", 0)
    updates = get_updates(token, offset, cfg["polling"]["timeout_seconds"])
    processed = ignored = errors = 0
    next_offset = offset

    for update in updates:
        update_id = int(update.get("update_id") or 0)
        next_offset = max(next_offset, update_id + 1)
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type") or "private"
        if not text or chat_id is None:
            ignored += 1
            continue
        if chat_type == "private" and not cfg["conversation"].get("private_chats", True):
            ignored += 1
            continue
        if chat_type != "private" and not cfg["conversation"].get("group_chats", False):
            ignored += 1
            continue
        try:
            if token and chat_id and not text.startswith("/"):
                try:
                    send_chat_action(token, chat_id, "typing")
                except Exception:
                    pass
            command = text.split()[0].lower()
            if command.startswith("/reset"):
                clear_conversation(chat_id)
                history = []
            else:
                history = conversation_history(chat_id)
            reply, answer_result = _reply_text(cfg, text, answer_fn, history=history)
            for part in _split_message(reply):
                send_message(token, chat_id, part)
            if answer_result and answer_result.get("ok"):
                remember_exchange(
                    chat_id,
                    text,
                    answer_result.get("answer") or cfg["fallback_message"],
                    cfg["conversation"]["history_messages"],
                )
            processed += 1
        except Exception:
            errors += 1
    return {
        "processed": processed,
        "ignored": ignored,
        "errors": errors,
        "next_offset": next_offset,
        "updates": len(updates),
    }


async def assistant_polling_loop():
    """Dedicated long-poll receiver for the conversational Telegram bot."""
    import asyncio
    import time

    ASSISTANT_RUNTIME.update(status="starting", last_error="")
    while True:
        try:
            cfg = load_assistant_config()
            if not cfg.get("enabled") or not cfg.get("token") or not cfg["polling"].get("enabled"):
                ASSISTANT_RUNTIME["status"] = "disabled"
                await asyncio.sleep(2)
                continue
            ASSISTANT_RUNTIME["status"] = "polling"
            result = await asyncio.to_thread(poll_assistant_once, config=cfg)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            ASSISTANT_RUNTIME["last_poll_at"] = now
            ASSISTANT_RUNTIME["last_error"] = ""
            if result["processed"]:
                ASSISTANT_RUNTIME["last_message_at"] = now
                ASSISTANT_RUNTIME["processed"] += result["processed"]
            if result["next_offset"] != cfg["polling"].get("offset"):
                save_assistant_config({"polling": {"offset": result["next_offset"]}})
        except asyncio.CancelledError:
            ASSISTANT_RUNTIME["status"] = "stopped"
            raise
        except Exception as exc:
            ASSISTANT_RUNTIME["status"] = "error"
            ASSISTANT_RUNTIME["last_error"] = str(exc)[:240]
            await asyncio.sleep(3)

