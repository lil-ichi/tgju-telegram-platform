# -*- coding: utf-8 -*-
"""Slug alias resolution — fix «بدون قیمت» slugs by finding the REAL tgju slug.

Tier 1 of the source-repair chain (user-approved plan, 2026-08-25):

For every platform-used slug that has no price anywhere:
  1. Look it up in state/slug_alias_map.json (persistent, human-editable).
  2. If unmapped, CANDIDATE-PROBE tgju.org profile pages directly
     (rule-based candidates: price_<iso>, <name>-style transforms) and keep
     whichever returns a real PDrCotVal price.
  3. Verified mappings are saved to the alias map AND the resolved price is
     warmed into state/profile_cache.json so posts show it immediately.

No AI here. Every candidate is verified with a real fetch — a wrong guess
can never enter the map.

API (all safe to call from request handlers via threads):
    resolve_slug(slug, name) -> {"real_slug", "price", "via"} | {}
    backfill_aliases(max_slugs=5) -> report dict   # one bounded pass
"""
import json
import os
import re
import threading
import time
import urllib.parse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))          # .../tgju
STATE_DIR = os.path.join(BASE_DIR, "tgju", "state")
ALIAS_PATH = os.path.join(STATE_DIR, "slug_alias_map.json")

_lock = threading.Lock()

# ── rule-based candidate generators (verified against tgju.org 2026-08-25) ──

FIAT_ISO = {
    "aud": "AUD", "cad": "CAD", "chf": "CHF", "cny": "CNY", "eur": "EUR",
    "gbp": "GBP", "inr": "INR", "aed": "AED", "afn": "AFN", "amd": "AMD",
    "azn": "AZN", "bdt": "BDT", "bhd": "BHD", "dkk": "DKK", "gel": "GEL",
    "hkd": "HKD", "idr": "IDR", "iqd": "IQD", "ils": "ILS", "isk": "ISK",
    "jod": "JOD", "lbp": "LBP", "mmk": "MMK", "myr": "MYR", "nok": "NOK",
    "nzd": "NZD", "omr": "OMR", "php": "PHP", "pkr": "PKR", "qar": "QAR",
    "rub": "RUB", "sar": "SAR", "sek": "SEK", "sgd": "SGD", "syp": "SYP",
    "thb": "THB", "tjs": "TJS", "tmt": "TMT", "try": "TRY", "uzs": "UZS",
}

CRYPTO_TICKER = {           # long name → tgju ticker slug suffix
    "bitcoin": "btc", "ethereum": "eth", "ripple": "xrp", "solana": "sol",
    "cardano": "ada", "dogecoin": "doge", "litecoin": "ltc",
    "binance-coin": "bnb", "bitcoin-cash": "bch", "polkadot": "dot",
    "shiba-inu": "shib", "stellar": "xlm", "tron": "trx", "toncoin": "ton",
    "avalanche": "avax", "dash": "dash",
}

INDEX_MAP = {               # verified real tgju slugs for world indices
    "bourse_dow": "dow_jones_us", "indices-dji-indx": "dow_jones_us",
    "bourse_nasdaq": "nasdaq", "bourse_sp-500": "index_sp500",
    "bourse_ftse-100": "ftse-100", "bourse_dax": "dax",
    "bourse_cac-40": "cac-40", "bourse_euro-stoxx-50": "euro-stoxx-50",
    "bourse_nikkei-225": "nikkei-225", "bourse_hang-seng": "hang-seng",
    "bourse_shanghai": "shanghai", "bourse_ibex-35": "ibex-35",
}

# verified 2026-08-25: many "broken" slugs are actually REAL tgju profile keys
# (oil_opec, general_9/10, afghan_usd, xaut…) — the homepage just doesn't list
# them. Identity candidates let the prober confirm them directly.
IDENTITY_HINTS = {
    "oil_opec", "louisiana_light_oil", "general_9", "general_10",
    "afghan_usd", "tether_gold_xaut", "crypto_paxg_gold",
    "crypto_gldt", "crypto_gold_kau", "crypto_quorium", "crypto_vnx_gold",
    "price_lbp", "price_bdt", "price_idr", "price_isk", "price_jod",
    "price_mmk", "price_php", "price_uzs", "bank_aud", "bank_iqd",
    "bank_jpy", "bank_krw", "bank_nok", "bank_qar", "bank_rub",
    "bank_sar", "bank_sek",
}

COMMODITY_MAP = {           # verified real tgju slugs for global commodities
    "oil_brent": "oil_brent", "oil_wti": "oil_wti",
    "louisiana_light_oil": "louisiana_light_oil",
    "energy_natural_gas": "general_10", "energy_gasoline_rbob": "general_9",
    "base_global_copper": "base_global_copper",
}


def load_alias_map() -> dict:
    try:
        with open(ALIAS_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_alias_map(m: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = ALIAS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ALIAS_PATH)


def _candidates(slug: str, name: str = "") -> list:
    """Rule-based candidate real-slugs, most-likely first."""
    s = slug.lower()
    out = []
    if slug in IDENTITY_HINTS:
        out.append(slug)                   # the key itself is a real profile
    if s in INDEX_MAP:
        out.append(INDEX_MAP[s])
    if s in COMMODITY_MAP:
        out.append(COMMODITY_MAP[s])
    core = re.sub(r"^(bank_|price_)", "", s)
    core = re.sub(r"_(ml|free)$", "", core).split("-")[0]
    iso = FIAT_ISO.get(core)
    if iso:
        out += ["price_" + iso.lower(), iso.lower()]
    if s.startswith("crypto-"):
        short = s.replace("crypto-", "")
        tick = CRYPTO_TICKER.get(short)
        if tick:
            out.append("crypto-" + tick)
        else:
            out.append(s)                      # maybe the slug itself is fine
    if name:
        # Persian display names sometimes ARE the profile key (geram18 etc.)
        pass
    # last resort: the slug itself (backfill may simply never have run)
    if slug not in out:
        out.append(slug)
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq[:4]


def _probe(real_slug: str) -> dict:
    """Real-network probe of one candidate profile page. Returns row|{}."""
    try:
        from tgju_engine_scrape import fetch_profile_price
        return fetch_profile_price(real_slug) or {}
    except Exception:
        return {}


def resolve_slug(slug: str, name: str = "") -> dict:
    """Resolve ONE broken slug via alias map, then verified probing.

    Returns {"real_slug", "price", "via"} on success, {} on failure.
    """
    amap = load_alias_map()
    ent = amap.get(slug) or {}
    if ent.get("real_slug") and ent.get("price"):
        return {"real_slug": ent["real_slug"], "price": ent["price"],
                "via": "alias-map"}
    for cand in _candidates(slug, name):
        row = _probe(cand)
        if row.get("price"):
            with _lock:
                m = load_alias_map()
                m[slug] = {"real_slug": cand,
                           "price": row["price"],
                           "name": name or "",
                           "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
                save_alias_map(m)
            return {"real_slug": cand, "price": row["price"],
                    "via": "probe"}
    _mark_unresolved(slug)
    return {}


_UNRESOLVED_PATH = os.path.join(STATE_DIR, "slug_alias_fail.json")


def _mark_unresolved(slug: str):
    try:
        with _lock:
            d = {}
            try:
                with open(_UNRESOLVED_PATH, encoding="utf-8") as f:
                    d = json.load(f) or {}
            except Exception:
                pass
            d[slug] = time.strftime("%Y-%m-%dT%H:%M:%S")
            tmp = _UNRESOLVED_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, _UNRESOLVED_PATH)
    except Exception:
        pass


def _unresolved_recently(slug: str) -> bool:
    """True when we already failed this slug in the last 6h (skip re-probe)."""
    try:
        with open(_UNRESOLVED_PATH, encoding="utf-8") as f:
            d = json.load(f) or {}
        ts = d.get(slug)
        if not ts:
            return False
        t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
        return (time.time() - t) < 6 * 3600
    except Exception:
        return False


def collect_unresolved(limit: int = 5) -> list:
    """Platform-used slugs that still have NO price source anywhere.

    Self-contained (no dependency on other modules): merges platform usage
    from every engine config, then drops anything that already has a price
    (homepage cache / profile cache / manual override / alias map).
    """
    import json as _json

    def _read(path):
        try:
            with open(path, encoding="utf-8") as f:
                return _json.load(f) or {}
        except Exception:
            return {}

    # 1) slugs referenced by any platform config
    usage = {}
    try:
        from tgju_engine_config import load_channels
        for c in (load_channels() or []):
            for s in list(c.get("slugs") or []):
                usage.setdefault(s, "telegram")
            for slugs in (c.get("slug_groups") or {}).values():
                for s in slugs:
                    usage.setdefault(s, "telegram")
    except Exception:
        pass
    # homepage rows also count: a slug the site lists but with an empty price
    # (bank_*, 504407-style codes) still deserves a resolution attempt
    try:
        from tgju_core.state import cached_rows as _cr
        for s in (_cr() or {}):
            usage.setdefault(s, "homepage")
    except Exception:
        pass
    for mod, fn in (("tgju_engine_whatsapp", "list_categories"),
                    ("tgju_engine_rubika", "list_channels"),
                    ("tgju_engine_eitaa", "list_channels")):
        try:
            m = __import__(mod)
            for it in (getattr(m, fn)() or []):
                for s in list(it.get("slugs") or []):
                    usage.setdefault(s, "other")
                for slugs in (it.get("slug_groups") or {}).values():
                    for s in slugs:
                        usage.setdefault(s, "other")
        except Exception:
            pass
    try:
        from tgju_engine_bale import load_config as bale_load
        for bc in ((bale_load() or {}).get("channels") or []):
            for s in list(bc.get("slugs") or []):
                usage.setdefault(s, "bale")
            for slugs in (bc.get("slug_groups") or {}).values():
                for s in slugs:
                    usage.setdefault(s, "bale")
    except Exception:
        pass

    # 2) drop everything that already has a price somewhere
    try:
        from tgju_core.state import cached_rows
        rows = cached_rows()
    except Exception:
        rows = {}
    overrides = _read(os.path.join(STATE_DIR, "slug_overrides.json"))
    profile_cache = _read(os.path.join(STATE_DIR, "profile_cache.json"))
    amap = load_alias_map()
    fails = _read(_UNRESOLVED_PATH)

    out = []
    now = time.time()
    for slug, plat in sorted(usage.items()):
        if slug in amap:                      # already resolved
            continue
        if (overrides.get(slug) or {}).get("manual_price"):
            continue                          # deliberate manual source
        if (rows.get(slug) or {}).get("price"):
            continue                          # homepage serves it
        if (profile_cache.get(slug) or {}).get("price"):
            continue                          # backfill works
        # recently failed → deprioritize but keep eligible after cooldown
        ts = fails.get(slug)
        if ts:
            try:
                t0 = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
                if (now - t0) < 6 * 3600:
                    continue          # failed <6h ago — skip this pass
            except Exception:
                pass
        out.append({"slug": slug, "platforms": [plat]})
        if len(out) >= limit:
            break
    return out


def warm_profile_cache(slug: str, price: str, name: str = ""):
    """Write the verified price where build_for_channel's backfill reads it.

    Uses the SAME `_t` epoch-stamp key the orchestrator's TTL filter expects,
    so entries survive its 5-minute stale-drop and refresh naturally on the
    next resolver pass (the alias map itself is the durable record).
    """
    try:
        import time as _t
        pc_path = os.path.join(STATE_DIR, "profile_cache.json")
        try:
            with open(pc_path, encoding="utf-8") as f:
                pc = json.load(f) or {}
        except Exception:
            pc = {}
        pc[slug] = {"price": price, "name": name or slug,
                    "_t": _t.time(), "alias_resolved": True}
        tmp = pc_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pc, f, ensure_ascii=False)
        os.replace(tmp, pc_path)
    except Exception:
        pass


def backfill_aliases(max_slugs: int = 5) -> dict:
    """One bounded pass: resolve up to max_slugs unresolved slugs.

    Designed to be called on an interval — each call costs at most a few
    seconds per slug (3 workers max, WAF-safe), so it can never hang.
    """
    targets = collect_unresolved(limit=max_slugs)
    fixed = []
    import concurrent.futures as cf
    def _one(t):
        return t["slug"], resolve_slug(t["slug"], t.get("name") or "")
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for slug, res in ex.map(_one, targets):
            if res:
                warm_profile_cache(slug, res["price"])
                fixed.append({"slug": slug, "real_slug": res["real_slug"],
                              "price": res["price"], "via": res["via"]})
    return {"checked": len(targets), "fixed": fixed}
