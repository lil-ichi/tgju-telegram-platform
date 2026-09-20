# -*- coding: utf-8 -*-
"""Channel news fetch: category/tag pages -> random never-repeated article (TGJU's own words).

The footer hyperlink on every price post is drawn RANDOMLY from an
accumulating per-channel pool of articles and recorded in the channel's
used-history, so the same news never appears twice.
"""
import hashlib
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

from tgju_engine_config import channel_state_path, load_channel_state, save_channel_state, BASE_DIR
from tgju_engine_scrape import UA

_ARTICLE_CACHE = {}
_ARTICLE_CACHE_TTL = 300  # 5 min
_CACHE_FILE = os.path.join(BASE_DIR, "state", "article_cache.json")
_CACHE_LOADED = False

# ── news pool / rotation constants ──────────────────────────────────────────
NEWS_FETCH_SEED = 15      # deep one-time fetch that seeds an empty pool
NEWS_FETCH_MIN = 8        # articles fetched per build (floor over news_max_items)
NEWS_FETCH_TOPUP = 3      # per-build fetch once the pool is already deep
NEWS_POOL_WARM = 20       # pool size above which only a top-up fetch is done
NEWS_POOL_CAP = 400       # articles remembered per channel (FIFO)
NEWS_RECENT_GUARD = 12    # headlines blocked right after a full-cycle reset
NEWS_POOL_MAX_AGE = 21 * 86400   # drop pool articles older than 21 days
_POOL_FILE_NAME = "news_pool.json"


def _pool_path() -> str:
    """Pool file lives next to the per-channel state files.

    Resolved at call time through channel_state_path so a redirected
    STATE_DIR (tests, portable packs) is honored automatically.
    """
    return os.path.join(os.path.dirname(channel_state_path("_pool")), _POOL_FILE_NAME)


def _load_pool() -> dict:
    try:
        with open(_pool_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_pool(pool: dict):
    try:
        os.makedirs(os.path.dirname(_pool_path()), exist_ok=True)
        with open(_pool_path(), "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False)
    except Exception:
        pass


def pool_add(channel_id: str, arts: list) -> int:
    """Merge freshly fetched articles into the channel pool (newest first).

    Returns how many NEW articles were added. The pool is the memory that
    lets the picker offer a different headline for a long time without
    re-fetching every article page on every post.
    """
    if not arts:
        return 0
    pool = _load_pool()
    prior = [str(a["id"]) for a in (pool.get(channel_id) or []) if a.get("id")]
    existing = {}
    for a in (pool.get(channel_id) or []):
        if a.get("id"):
            existing[str(a["id"])] = a
    merged, added, now = [], 0, time.time()
    for a in arts:
        aid = str(a.get("id") or "")
        text = (a.get("text") or "").strip()
        if not aid or not text:
            continue
        if aid not in existing:
            added += 1
        else:
            existing.pop(aid, None)
        merged.append({"id": aid, "url": a.get("url", ""),
                       "text": text, "_t": now})
    # keep the rest of the pool underneath, newest pool entries first
    merged.extend(existing.values())
    cutoff = now - NEWS_POOL_MAX_AGE
    merged = [a for a in merged
              if not a.get("_t") or a["_t"] >= cutoff][:NEWS_POOL_CAP]
    pool[channel_id] = merged
    if added == 0 and [a["id"] for a in merged] == prior:
        return 0        # nothing new — skip the disk write entirely
    _save_pool(pool)
    return added


def pool_articles(channel_id: str) -> list:
    """All remembered articles for a channel, newest first."""
    return [a for a in (_load_pool().get(channel_id) or []) if a.get("id")]


def _text_key(text: str) -> str:
    """Stable fingerprint of a headline so identical texts never repeat."""
    norm = re.sub(r"\s+", " ", (text or "")).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def _dedupe_tail(items: list, cap: int) -> list:
    """Keep item order, drop earlier duplicates, keep only the last `cap`."""
    out = []
    for x in items:
        if x in out:
            out.remove(x)
        out.append(x)
    return out[-cap:]


def _load_article_cache():
    global _ARTICLE_CACHE, _CACHE_LOADED
    if _CACHE_LOADED:
        return
    _CACHE_LOADED = True
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            _ARTICLE_CACHE = json.load(f)
        now = time.time()
        _ARTICLE_CACHE = {k: v for k, v in _ARTICLE_CACHE.items()
                          if now - v.get("_t", 0) < _ARTICLE_CACHE_TTL}
    except Exception:
        _ARTICLE_CACHE = {}


def _save_article_cache():
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_ARTICLE_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


def _fetch_text(url: str, timeout: int = 30) -> str:
    # serve from cache immediately (5-min TTL)
    _load_article_cache()
    hit = _ARTICLE_CACHE.get(url)
    if hit and hit.get("_html"):
        return hit["_html"]
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "fa,en;q=0.8",
        "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", errors="replace")
    _ARTICLE_CACHE[url] = {"_html": html, "_t": time.time()}
    _save_article_cache()
    return html


def _article_links(html: str) -> list:
    return list(dict.fromkeys(
        m.group(1) for m in re.finditer(r'href="(/news/\d+/[^"]+)"', html)))


def _og_description(ahtml: str) -> str:
    m = re.search(r'<meta[^>]+(?:name|property)="og:description"[^>]+content="([^"]+)"',
                  ahtml)
    return m.group(1).strip() if m else ""


_CATEGORY_IDS = {}          # category-slug -> numeric id (discovered once)
_CATEGORY_IDS_TTL = 86400   # 24h (category ids are stable)
_CATEGORY_IDS_AT = 0.0


def _category_id_map() -> dict:
    """Discover /news/category/<id>/<slug> mapping from the tgju.org homepage.

    tgju.org now serves category links with numeric ids
    (e.g. /news/category/93965/اخبار-ارزی); plain /news/category/<slug>
    returns 404. Discovered once per 24h, cached in-process.
    """
    global _CATEGORY_IDS, _CATEGORY_IDS_AT
    now = time.time()
    if _CATEGORY_IDS and now - _CATEGORY_IDS_AT < _CATEGORY_IDS_TTL:
        return _CATEGORY_IDS
    ids = {}
    try:
        html = _fetch_text("https://www.tgju.org/")
    except Exception:
        html = ""
    for m in re.finditer(r'href="(/news/category/(\d+)/([^"/]+))"', html):
        slug = urllib.parse.unquote(m.group(3))
        ids.setdefault(slug, m.group(2))
    if ids:
        _CATEGORY_IDS = ids
        _CATEGORY_IDS_AT = now
    return ids


def _category_url(category: str) -> str:
    """/news/category/<id>/<slug> when the id is known, else the plain URL."""
    cid = _category_id_map().get(category)
    if cid:
        return ("https://www.tgju.org/news/category/%s/%s"
                % (cid, urllib.parse.quote(category)))
    # fallback: old-style URL (may 404; caller tolerates that)
    return "https://www.tgju.org/news/category/" + urllib.parse.quote(category)


def channel_articles(categories: list, tags: list, limit: int = 6) -> list:
    """Return list of {url, text, id} from category pages then tag pages (newest first)."""
    arts = []
    seen = set()
    sources = []
    for c in categories or []:
        sources.append(_category_url(c))
    for t in tags or []:
        sources.append("https://www.tgju.org/news/tag/" + urllib.parse.quote(t))
    if not sources:
        sources.append("https://www.tgju.org/news")
    for src in sources:
        try:
            html = _fetch_text(src)
        except Exception:
            continue
        for path in _article_links(html):
            art_id = re.search(r"/news/(\d+)/", path)
            if not art_id or art_id.group(1) in seen:
                continue
            seen.add(art_id.group(1))
            url = "https://www.tgju.org" + urllib.parse.quote(path, safe="/%")
            try:
                ahtml = _fetch_text(url)
                text = _og_description(ahtml)
                if text:
                    arts.append({"url": url, "text": text, "id": art_id.group(1)})
                    # Cache successful article fetches to disk for TGJU
                    # outage recovery (channel_articles doesn't know the
                    # channel_id — cache under the category/tag key).
                    try:
                        from tgju_engine_fallback import save_fallback_news
                        save_fallback_news("_global_" + "_".join((categories or [])[:1] + (tags or [])[:1]), arts)
                    except Exception:
                        pass
            except Exception:
                continue
            if len(arts) >= limit:
                return arts
    return arts


def pick_rotating(channel_id: str, arts: list) -> dict:
    """Pick a RANDOM article that was never posted before (channel-scoped).

    Guarantees, in order:
      1. the picked article id AND its headline text are absent from the
         channel's used-history — the footer hyperlink never repeats;
      2. never the same headline as the previous post;
      3. once the whole pool has been consumed the history resets, but the
         most recent NEWS_RECENT_GUARD headlines stay blocked so there is
         still no immediate repeat (a fresh full cycle begins).

    History is persisted FIFO in the channel state file (which also carries
    last_poll_at / last_analysis_at / last_news_at — always MERGEd, never
    replaced, or the scheduler interval dedupe breaks).
    """
    if not arts:
        return {}
    state = load_channel_state(channel_id)
    used = [str(x) for x in (state.get("news_used") or state.get("used") or [])]
    used_texts = [str(x) for x in (state.get("news_used_texts") or [])]
    last_id = str(state.get("last_news_id") or "")

    blocked_ids = set(used)
    blocked_texts = set(used_texts)

    def _available(a: dict) -> bool:
        aid = str(a.get("id") or "")
        return bool(aid) and aid not in blocked_ids \
            and _text_key(a.get("text", "")) not in blocked_texts

    candidates = [a for a in arts if _available(a)]
    if not candidates:
        # Full cycle consumed: forget older history but keep a recent guard
        # window, so recycling the pool still never repeats back-to-back.
        recent_ids = set(used[-NEWS_RECENT_GUARD:])
        blocked_ids = recent_ids or ({last_id} if last_id else set())
        blocked_texts = {_text_key(a.get("text", "")) for a in arts
                         if str(a.get("id")) in blocked_ids}
        candidates = [a for a in arts if _available(a)]
    if not candidates:
        # Pool smaller than the guard window — at minimum never repeat the
        # headline that was posted just before.
        candidates = [a for a in arts if str(a.get("id")) != last_id] or list(arts)

    pick = random.choice(candidates)
    pick_id = str(pick.get("id") or "")
    used = _dedupe_tail([i for i in used if i] + [pick_id], NEWS_POOL_CAP)
    used_texts = _dedupe_tail(used_texts + [_text_key(pick.get("text", ""))],
                             NEWS_POOL_CAP)
    full = load_channel_state(channel_id) or {}
    full.pop("used", None)     # legacy key migrated into news_used
    full.update({"news_used": used,
                 "news_used_texts": used_texts,
                 "last_news_id": pick_id,
                 "last_news_at": datetime.now().isoformat(timespec="seconds")})
    save_channel_state(channel_id, full)
    return pick


def analysis_line(channel_id: str, categories: list, tags: list) -> str:
    """One hyperlinked TGJU sentence (random, never-repeated) for the footer."""
    try:
        from tgju_platform import load_settings
        limit = max(NEWS_FETCH_MIN,
                    int(load_settings().get("news_max_items", 3)) + 5)
    except Exception:
        limit = NEWS_FETCH_MIN
    arts = []
    try:
        # First build seeds a deep pool (one-time cost); afterwards the pool
        # supplies the variety and each build only tops up newest headlines.
        pooled = len(pool_articles(channel_id))
        if pooled == 0:
            want = NEWS_FETCH_SEED
        elif pooled < NEWS_POOL_WARM:
            want = NEWS_FETCH_MIN
        else:
            want = max(1, min(limit, NEWS_FETCH_TOPUP))
        arts = channel_articles(categories, tags, limit=want)
    except Exception:
        arts = []
    if arts:
        pool_add(channel_id, arts)          # remember for future random picks
    # The pool is the candidate source (fresh articles included) so the
    # picker can stay random for hundreds of posts without re-fetching.
    arts = pool_articles(channel_id) or arts
    # Fallback: if network failed and the pool is empty, load from disk cache
    if not arts:
        try:
            from tgju_engine_fallback import load_fallback_news
            arts = load_fallback_news(channel_id)
        except Exception:
            pass
    pick = pick_rotating(channel_id, arts)
    if not pick:
        # Last resort: if we got nothing at all, use the last-known analysis
        # from disk so the news line doesn't vanish during a TGJU outage.
        try:
            from tgju_engine_fallback import load_fallback_analysis
            fb = load_fallback_analysis(channel_id)
            if fb:
                return fb
        except Exception:
            pass
        return ""
    # escape the article text for Telegram's HTML parser (og:description may
    # contain & / < / >); the href itself is a plain URL
    text = (pick["text"] or "").replace("&", "&amp;").replace("<", "&lt;") \
        .replace(">", "&gt;")
    result = '<a href="%s">%s</a>' % (pick["url"], text)
    # Save the last analysis result to disk per-channel for fallback
    try:
        from tgju_engine_fallback import save_fallback_analysis
        save_fallback_analysis(channel_id, result)
    except Exception:
        pass
    return result