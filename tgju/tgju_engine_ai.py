# -*- coding: utf-8 -*-
"""AI config + content routing for the TGJU Telegram platform.

Stores per-provider and per-channel AI settings in state/ai_config.json so
the UI can manage pipeline AI (analysis/summary/translation) without code edits.

The analysis provider config per channel::

    cfg["channels"]["ch1"]["analysis"] = {
        "enabled": true, "provider": "gateway", "model": "gpt-4o-mini"}

``enabled`` defaults to false for channels that have no analysis config —
AI-generated analysis is OFF by default (user mandate: the TGJU analysis
line must stay TGJU's own words unless the user explicitly enables AI).
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AI_CONFIG_PATH = os.path.join(BASE_DIR, "state", "ai_config.json")

DEFAULT_CONFIG = {
    "providers": {
        "mock": {"label": "بدون هوش مصنوعی", "kind": "mock",
                 "base_url": "", "api_key": "", "model": "", "enabled": True},
        "gateway": {"label": "Hermes LLM Gateway", "kind": "openai_compat",
                    "base_url": "http://localhost:8788/v1",
                    "api_key": "", "model": "gpt-4o-mini", "enabled": True},
    },
    "channels": {},   # ch_id -> {"analysis": {"provider": ..., "model": ...}}
    "routing": {},    # category -> [channel_ids]
}

ANALYSIS_PROMPT = (
    "تو یک تحلیل‌گر بازار {domain} هستی. بر اساس داده‌های جدول زیر (فقط همین داده‌ها، "
    "هیچ عدد یا ادعایی خارج از آن اضافه نکن)، وضعیت بازار {domain} را در {length} "
    "به زبان فارسی و با لحن حرفه‌ای و بی‌طرفانه خلاصه کن. به بیشترین رشدها و "
    "افت‌ها اشاره کن، ارزش‌های قابل توجه را ذکر کن، و هیچ توصیه‌ای برای خرید "
    "یا فروش نده. از داده نبودن چیزی خیال‌پردازی نکن.\n\n"
    "{table}"
)

POLL_GEN_PROMPT = (
    "تو مدیر کانال تلگرام بازار طلا و ارز هستی. بر اساس وضعیت بازار زیر، %d "
    "سؤال نظرسنجی جذاب و مرتبط با مخاطب بساز (به فارسی، بدون تکرار، هر سؤال "
    "۲ تا ۴ گزینه). خروجی را دقیقاً به صورت JSON بده:\n"
    '[{"question": "...", "options": ["...", "..."]}]\n\n'
    "وضعیت بازار:\n%s"
)

# ── AI Orchestrator: jobs + activity ─────────────────────────────────────
AI_JOBS_PATH = os.path.join(BASE_DIR, "state", "ai_jobs.json")

DEFAULT_JOBS = {
    "analysis": {
        "label": "تحلیل بازار",
        "desc": "تولید تحلیل فارسی از داده‌های زنده هر کانال",
        "enabled": True,
        "provider": "",
        "model": "",
        "max_tokens": 2000,
        "timeout_s": 90,
        "channels": [],          # empty = همه کانال‌های فعال AI
    },
    "poll_select": {
        "label": "انتخاب هوشمند نظرسنجی",
        "desc": "AI بهترین سؤال نظرسنجی را بر اساس وضعیت بازار انتخاب می‌کند",
        "enabled": True,
        "provider": "",
        "model": "",
        "max_tokens": 500,
        "timeout_s": 25,
        "channels": [],
    },
    "poll_generate": {
        "label": "تولید نظرسنجی",
        "desc": "ساخت سؤال‌های جدید نظرسنجی از وضعیت بازار",
        "enabled": True,
        "provider": "",
        "model": "",
        "max_tokens": 2000,
        "timeout_s": 90,
        "default_count": 3,
    },
    "news_summary": {
        "label": "خلاصه خبر",
        "desc": "خلاصه‌سازی اخبار با AI (فعلاً غیرفعال)",
        "enabled": False,
        "provider": "",
        "model": "",
        "max_tokens": 500,
        "timeout_s": 60,
        "channels": [],
    },
}

_ACTIVITY_MAX = 50  # ring buffer size


def load_ai_jobs() -> dict:
    """Return {"jobs": {id: config}, "activity": [...]} merged with defaults."""
    try:
        with open(AI_JOBS_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    jobs = {}
    for jid, jdef in DEFAULT_JOBS.items():
        saved = (d.get("jobs") or {}).get(jid) or {}
        merged = dict(jdef)
        merged.update({k: v for k, v in saved.items() if v is not None})
        jobs[jid] = merged
    return {"jobs": jobs, "activity": d.get("activity") or []}


def save_ai_jobs(jobs: dict):
    os.makedirs(os.path.dirname(AI_JOBS_PATH), exist_ok=True)
    with open(AI_JOBS_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=1)


def record_ai_activity(entry: dict):
    """Append one activity entry (ring buffer, persisted)."""
    entry.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    data = load_ai_jobs()
    act = data["activity"]
    act.append(entry)
    if len(act) > _ACTIVITY_MAX:
        act = act[-_ACTIVITY_MAX:]
    save_ai_jobs({"jobs": data["jobs"], "activity": act})


def resolve_job(cfg: dict, job_id: str) -> dict:
    """Job config with provider/model filled from provider defaults."""
    jobs = load_ai_jobs()["jobs"]
    job = dict(jobs.get(job_id) or DEFAULT_JOBS.get(job_id, {}))
    if not job.get("provider"):
        # default to the first enabled non-mock provider
        for pname, p in (cfg.get("providers") or {}).items():
            if p.get("kind") != "mock" and p.get("base_url"):
                job["provider"] = pname
                job["model"] = job.get("model") or p.get("model") or ""
                break
    return job


def load_ai_config() -> dict:
    try:
        with open(AI_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    base = {k: (v if isinstance(v, dict) else dict(v))
            for k, v in DEFAULT_CONFIG.items()}
    base.update({k: v for k, v in cfg.items() if v is not None})
    base.setdefault("providers", DEFAULT_CONFIG["providers"])
    base.setdefault("channels", {})
    base.setdefault("routing", {})
    return base


def save_ai_config(cfg: dict):
    os.makedirs(os.path.dirname(AI_CONFIG_PATH), exist_ok=True)
    with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def test_provider(provider: dict) -> dict:
    """Minimal health check against an OpenAI-compatible endpoint.

    ALWAYS does a real chat/completions probe (tiny 1-token prompt) — the
    /models endpoint doesn't require auth on many gateways, so a successful
    /models call alone would report a broken key as healthy. The probe
    catches invalid/stale API keys that /models misses.
    """
    kind = provider.get("kind")
    if kind == "mock":
        return {"ok": True, "detail": "mock: همیشه در دسترس"}
    base = (provider.get("base_url") or "").rstrip("/")
    key = provider.get("api_key") or ""
    if not base:
        return {"ok": False, "detail": "base_url خالی است"}
    # Real probe: chat/completions with a 1-token request — this verifies
    # the API key, not just model listing.
    try:
        _chat_completion(provider, "hi", max_tokens=1, timeout=15)
        return {"ok": True, "detail": "اتصال برقرار (chat/completions)"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False,
                    "detail": "خطای احراز هویت (HTTP %d) — API key نامعتبر یا قدیمی است" % e.code}
        return {"ok": False, "detail": "HTTP %d" % e.code}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:160]}


def list_provider_models(provider: dict) -> dict:
    """Fetch model IDs from an OpenAI-compatible /models endpoint.

    Returns {"ok": True, "models": [...]} or {"ok": False, "detail": ...}.
    /models usually works without auth on local gateways, but we still send
    the key when present.
    """
    kind = provider.get("kind")
    if kind == "mock":
        return {"ok": True, "models": []}
    base = (provider.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "detail": "base_url خالی است"}
    url = base + "/models"
    req = urllib.request.Request(url, headers={
        "User-Agent": "tgju-platform/1.0",
        "Authorization": "Bearer " + (provider.get("api_key") or ""),
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        return {"ok": True, "models": sorted(set(ids))}
    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": "HTTP %d" % e.code}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:160]}


def _chat_completion(provider: dict, prompt: str, max_tokens: int = 400,
                     timeout: int = 60) -> str:
    """POST <base_url>/chat/completions; returns the assistant text.

    Raises urllib.error.HTTPError / OSError / ValueError on failure.

    NOTE (2026-08-15): the local gateway routes to reasoning models
    (deepseek-v4-flash-free) that spend tokens on `reasoning_content`
    BEFORE producing content. Small max_tokens (<=120) starve the content
    field → empty responses. Callers that need real text should pass
    max_tokens >= 1000.
    """
    base = (provider.get("base_url") or "").rstrip("/")
    key = provider.get("api_key") or ""
    model = provider.get("model") or ""
    url = base + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "tgju-platform/1.0"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="replace")
    data = _parse_json_response(raw)
    try:
        content = data["choices"][0]["message"].get("content")
        return (content or "").strip()
    except (KeyError, IndexError, TypeError):
        raise ValueError("chat/completions response missing choices: %s"
                         % str(data)[:160])


def _parse_json_response(raw: str) -> dict:
    """Parse a JSON response, tolerant of trailing/concatenated objects.

    Some local LLM servers (Hermes gateway) stream an extra object after the
    main response, or append whitespace — json.loads fails on the extra data.
    Strategy: try strict parse first; on failure, extract the first complete
    JSON object (balanced braces) and parse that.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Find the first balanced top-level JSON object.
    depth = 0
    in_str = False
    esc = False
    start = None
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                obj = raw[start:i + 1]
                try:
                    return json.loads(obj)
                except json.JSONDecodeError:
                    break
    raise ValueError("invalid JSON in chat/completions response: %s" % raw[:200])


def _rows_to_table(channel: dict, rows: dict) -> str:
    """Compact Persian table of the channel's rows, for the AI prompt."""
    from tgju_engine_format import slug_unit, fmt_price, direction_arrow, fa_thousands
    wanted = list(channel.get("slugs") or [])
    for slugs in (channel.get("slug_groups") or {}).values():
        wanted.extend(slugs)
    lines = []
    for s in dict.fromkeys(wanted):
        row = rows.get(s) or {}
        if not row.get("price"):
            continue
        name = row.get("name") or s
        unit = slug_unit(s, channel)
        pct = row.get("change_pct") or ""
        arrow = direction_arrow(row)
        lines.append("%s | %s %s | %s%% %s" % (
            name, fmt_price(s, row["price"], unit), unit,
            fa_thousands(pct) if pct else "—", arrow))
    return "\n".join(lines) or "(داده‌ای در دسترس نیست)"


def _channel_domain(channel: dict) -> str:
    """Derive the market domain for a channel from its name/tags/slugs.
    Used to make the AI analysis channel-relevant (gold channel → gold
    analysis, energy → energy, etc.). Returns a Persian domain phrase.

    Two-pass matching: strong signals (name + analysis_tags) first, then
    slugs — so a channel named «نرخ ارز» stays an FX channel even if one
    of its slugs happens to contain a crypto keyword."""
    name = (channel.get("name") or "").lower()
    tags_txt = " ".join(channel.get("analysis_tags") or []).lower()
    slugs_txt = " ".join([
        " ".join(channel.get("slugs") or []),
        " ".join(str(s) for sl in (channel.get("slug_groups") or {}).values() for s in sl),
    ]).lower()

    domain_map = [
        ("طلا", ["طلا", "gold", "سکه", "coin", "ounce", "ons", "ابشده"]),
        ("نفت و انرژی", ["نفت", "oil", "انرژی", "energy", "گاز", "gas", "بشکه"]),
        ("فلزات جهانی", ["مس", "آلومینیوم", "روی", "نیکل", "copper", "metal", "فلز"]),
        ("ارزهای دیجیتال", ["بیت‌کوین", "bitcoin", "دیجیتال", "کریپتو", "crypto", "btc", "eth", "تتر", "usdt"]),
        ("بورس و سهام", ["بورس", "سهام", "stock", "شاخص", "تداوم", "هم وزن"]),
        ("ارز و صرافی", ["دلار", "یورو", "درهم", "ارز", "dollar", "euro", "صرافی", "پوند"]),
        ("جهانی و بین‌الملل", ["جهان", "جهانی", "داوجونز", "داو", "dow", "nasdaq", "s&p", "نزدک", "فوتسی"]),
    ]

    def match(text: str):
        for domain, keys in domain_map:
            if any(k in text for k in keys):
                return domain
        return None

    # Pass 1: channel name + analysis tags (explicit intent)
    d = match(name + " " + tags_txt)
    if d:
        return d
    # Pass 2: slugs (fallback)
    d = match(slugs_txt)
    if d:
        return d
    return "مورد نظر"


def run_analysis(cfg: dict, channel: dict, rows: dict) -> dict:
    """Run AI-generated market analysis for one channel (OPT-IN per channel).

    Only runs when cfg["channels"][cid]["analysis"]["enabled"] is true AND a
    configured provider is reachable. Returns::

        {"ok": True, "text": <persian analysis>, "provider": <name>,
         "model": <model>, "latency_ms": <int>}
    or {"ok": False, "error": <reason>}.

    This is an OPTIONAL feature — the default TGJU analysis line (hyperlink
    to TGJU's own words) is untouched and remains the primary content.
    """
    cid = channel.get("id", "ch")
    ch_cfg = (cfg.get("channels") or {}).get(cid) or {}
    analysis = ch_cfg.get("analysis") or {}
    if not analysis.get("enabled", False):
        return {"ok": False,
                "error": ("هوش مصنوعی برای این کانال فعال نیست "
                          "(analysis.enabled در ai_config)" )}
    provider_name = analysis.get("provider") or ""
    provider = (cfg.get("providers") or {}).get(provider_name)
    if not provider:
        return {"ok": False, "error": "provider «%s» پیکربندی نشده است" % provider_name}
    if provider.get("kind") == "mock":
        return {"ok": False, "error": "provider «%s» در حالت mock است — مدل واقعی انتخاب کنید" % provider_name}
    if not provider.get("base_url"):
        return {"ok": False, "error": "base_url برای provider «%s» خالی است" % provider_name}
    model = analysis.get("model") or provider.get("model") or ""
    if not model:
        return {"ok": False, "error": "مدل برای provider «%s» تعیین نشده است" % provider_name}
    prov = dict(provider)
    prov["model"] = model
    table = _rows_to_table(channel, rows)
    # Channel-aware prompt: derive domain + effort from functions config
    domain = _channel_domain(channel)
    effort = "standard"
    try:
        import tgju_engine_functions as fn_mod
        fns = fn_mod.load_functions()
        fn = fns.get("analysis") or {}
        effort = fn.get("effort") or "standard"
        ch_cfg_fn = (fn.get("channels") or {}).get(cid) or {}
        if ch_cfg_fn.get("effort"):
            effort = ch_cfg_fn["effort"]
    except Exception:
        pass
    length = "۸ تا ۱۲ جمله جامع و تفصیلی" if effort == "deep" else "۳ تا ۵ جمله"
    prompt = ANALYSIS_PROMPT.format(domain=domain, length=length, table=table)
    # Orchestrator job config (state/ai_jobs.json) can override tokens/timeout
    job = resolve_job(cfg, "analysis")
    max_tokens = int(job.get("max_tokens") or 2000)
    timeout_s = int(job.get("timeout_s") or 90)
    t0 = time.time()
    text = ""
    last_err = ""
    for attempt in range(3):
        try:
            # max_tokens must cover reasoning_content + content (2000+):
            # local reasoning routers return EMPTY content when starved.
            text = _chat_completion(prov, prompt, max_tokens=max_tokens,
                                    timeout=timeout_s)
            if text:
                break
            last_err = "پاسخ خالی از مدل دریافت شد (تلاش %d)" % (attempt + 1)
        except urllib.error.HTTPError as e:
            last_err = "HTTP %d: %s" % (e.code, e.reason)
            break  # HTTP errors won't fix themselves
        except Exception as e:
            last_err = str(e)[:200]
        time.sleep(1.0)  # brief pause between retries
    latency_ms = int((time.time() - t0) * 1000)
    if not text:
        record_ai_activity({"job": "analysis", "channel": cid,
                            "status": "error", "error": last_err,
                            "latency_ms": latency_ms, "provider": provider_name})
        return {"ok": False, "error": last_err or "پاسخ خالی از مدل دریافت شد"}
    record_ai_activity({"job": "analysis", "channel": cid, "status": "ok",
                        "latency_ms": latency_ms, "provider": provider_name,
                        "model": model, "chars": len(text)})
    return {"ok": True, "text": text, "provider": provider_name,
            "model": model, "latency_ms": latency_ms}


def route_category(cfg: dict, category: str) -> list:
    """Return channel ids whose news_categories include this category."""
    routing = cfg.get("routing") or {}
    if category in routing:
        return routing[category]
    return []


def auto_build_routing(channels: list, cfg: dict) -> dict:
    """Derive routing from each channel's news_categories."""
    routing = {}
    for ch in channels or []:
        for cat in ch.get("news_categories") or []:
            routing.setdefault(cat, [])
            if ch.get("id") not in routing[cat]:
                routing[cat].append(ch["id"])
    cfg["routing"] = routing
    return routing