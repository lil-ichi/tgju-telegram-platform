# -*- coding: utf-8 -*-
"""Channel news fetch: category/tag pages -> rotating article + lead (TGJU's own words)."""
import json
import os
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
    """Pick the newest unused article; persist used-ids per channel.

    Robustness: tracks the last-picked id per channel so consecutive posts
    NEVER repeat the same article — even after the daily `used` reset and
    even when every top article has been seen (then it picks the next one
    after the last-picked, never the same one again).
    """
    if not arts:
        return {}
    state = load_channel_state(channel_id)
    used = set(state.get("news_used") or state.get("used") or [])
    dayid = datetime.now().timetuple().tm_yday
    if state.get("day") != dayid:
        used = set()  # fresh day -> allow reusing older articles
        state["day"] = dayid
    last_id = state.get("last_news_id", "")
    # keep the last-picked id OUT of the candidate set so consecutive
    # posts can't repeat the identical headline
    candidates = [a for a in arts if a["id"] != last_id]
    pick = None
    for a in candidates:
        if a["id"] not in used:
            pick = a
            break
    if pick is None and candidates:
        # every top article was seen -> rotate to the next one after
        # the last-picked instead of always arts[0]; if the last-picked is
        # at the end, wrap to the newest unseen-by-last
        try:
            last_pos = next(i for i, a in enumerate(candidates)
                            if a["id"] == last_id)
        except StopIteration:
            last_pos = -1
        pick = candidates[(last_pos + 1) % len(candidates)]
    if pick is None:
        pick = arts[0]
    used.add(pick["id"])
    state["last_news_id"] = pick["id"]
    # MERGE into the existing channel state — this file also carries
    # last_poll_at / last_analysis_at / last_news_at (scheduler dedupe).
    # Replacing the whole dict here used to WIPE those timestamps, which
    # broke poll/news interval tracking (polls fired every tick).
    try:
        from tgju_engine_config import load_channel_state as _load_full
        full = _load_full(channel_id) or {}
    except Exception:
        full = {}
    full.update({"day": dayid,
                 "news_used": sorted(used)[-80:],
                 "last_news_id": pick["id"],
                 "last_news_at": datetime.now().isoformat(timespec="seconds")})
    save_channel_state(channel_id, full)
    return pick


def analysis_line(channel_id: str, categories: list, tags: list) -> str:
    """One hyperlinked TGJU sentence for the channel's news feed."""
    try:
        from tgju_platform import load_settings
        limit = max(1, int(load_settings().get("news_max_items", 3)) + 3)
    except Exception:
        limit = 6
    arts = channel_articles(categories, tags, limit=limit)
    # Fallback: if network failed and no articles, load from disk cache
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