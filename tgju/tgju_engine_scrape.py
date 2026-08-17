# -*- coding: utf-8 -*-
"""TGJU scraper: homepage fetch -> parse ALL slugs -> profile backfill."""
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HOME_URL = "https://www.tgju.org/"

# Some slugs on the homepage 404 on their own profile path, but a DIFFERENT
# slug resolves. Map them here so posts use the working URL (verified live
# 2026-08-15): bourse_dow -> dow_jones_us, bourse_euro-stoxx-50 -> stoxx50.
SLUG_ALIASES = {
    "bourse_dow": "dow_jones_us",
    "bourse_euro-stoxx-50": "stoxx50",
}


def fetch_html(url: str = HOME_URL, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fa,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_rows(html: str) -> dict:
    """Return {slug: {name, price, change_pct, change_amt, dir}} for ALL slugs.

    Uses a quote-aware scanner for the <tr> open tag: some rows carry a
    data-title attribute containing HTML with '>' inside, so a regex that
    stops at the first '>' would truncate attrs and lose the price.
    """
    out = {}
    pos = 0
    while True:
        start = html.find("<tr", pos)
        if start < 0:
            break
        tag_end = _tag_close(html, start)
        if tag_end < 0:
            break
        open_tag = html[start:tag_end]
        m = re.search(r'data-market-nameslug="([^"]+)"', open_tag)
        if not m:
            pos = tag_end
            continue
        slug = m.group(1)
        row_end = html.find("</tr>", tag_end)
        body = html[tag_end:row_end] if row_end >= 0 else ""
        price_m = re.search(r'data-price="([^"]*)"', open_tag + body)
        price = price_m.group(1).strip() if price_m else ""
        if not price:
            # crypto rows: live price in the data-title tooltip rows
            tm = re.search(r"data-title=\"[^\"]*?([\d,\.]+)\s+در\s+\d{1,2}:\d{2}",
                           open_tag, re.S)
            if tm:
                price = tm.group(1).strip()
        name_m = re.search(r'<th[^>]*>(.*?)</th>', body, re.S)
        name = re.sub(r'<[^>]+>', "", name_m.group(1)).strip() if name_m else slug
        # Strict change-cell match: the nf change cell is
        #   <span class="high|low">(0.6%) 26.39</span>  (double-quoted class)
        # Scanning the WHOLE row body (not anchored to the first nf td — some
        # rows like sekee have a plain price nf td BEFORE the change nf td).
        # Class vocabulary verified 2026-08-11: exactly {high, low}.
        change_m = re.search(
            r'<span class="(high|low)">\(([\d\.,\-]+)\s*%?\)\s*([\d\.,\-]*)</span>',
            body, re.S)
        if change_m:
            direction = change_m.group(1)
            pct = change_m.group(2).replace("%", "").replace("٪", "").strip()
            amt = change_m.group(3).strip()
        else:
            direction, pct, amt = "", "", ""
        # IMPORTANT: some slugs appear TWICE (e.g. crypto-bitcoin) — once with a
        # real data-price, once with data-price="". Keep the first non-empty
        # price; skip empty duplicates so they don't clobber the good row.
        if not price and slug in out:
            pos = row_end if row_end >= 0 else tag_end
            continue
        out[slug] = {
            "name": name, "price": price.replace(",", "").strip(),
            "change_pct": pct, "change_amt": amt, "dir": direction}
        pos = row_end if row_end >= 0 else tag_end
    return out


def _tag_close(html: str, start: int) -> int:
    """Index just after the '>' that closes the <tag ...> open tag, or -1.
    Tracks quotes so '>' inside attribute values (e.g. data-title with HTML)
    does not terminate the tag early."""
    i = start
    n = len(html)
    quote = None
    while i < n:
        ch = html[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == ">":
            return i + 1
        i += 1
    return -1


def slash_price(row: dict) -> dict:
    """Fill missing price/delta fields with empty strings (render later)."""
    return {"name": row.get("name", ""), "price": row.get("price") or "",
            "change_pct": row.get("change_pct") or "",
            "change_amt": row.get("change_amt") or "",
            "dir": row.get("dir") or ""}


def fetch_profile_price(slug: str, name: str | None = None) -> dict:
    """Backfill crypto/metals with empty homepage price from the profile page.

    `name` (optional) is the display name from the homepage row (e.g. «لایت کوین»).
    It is normalized into a ZWNJ/space-tolerant pattern so multi-word crypto
    names match the FAQ text even when the FAQ spells them with spaces where
    the homepage uses ZWNJ (e.g. «لایت کوین» vs «لایت‌کوین»). When no name is
    given we fall back to the old single-word pattern.
    """
    # NAVBAR profile path is the reliable page (verified 2026-08-10);
    # crypto/currency/<name> is flaky, so it is only a fallback URL.
    # Wrong homepage slugs (e.g. bourse_dow) resolve via SLUG_ALIASES.
    fetch_slug = SLUG_ALIASES.get(slug, slug)
    urls = [
        "https://www.tgju.org/profile/" + urllib.parse.quote(fetch_slug, safe=""),
        "https://www.tgju.org/crypto/currency/" + fetch_slug.replace("crypto-", ""),
    ]
    html = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "fa,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", errors="replace")
            break
        except Exception:
            continue
    if not html:
        return {}
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    if name:
        # ZWNJ/space-tolerant pattern: «لایت کوین» matches لایت‌کوین and vice versa
        name_pat = r"\s*".join(re.escape(ch)
                               for ch in name.replace("\u200c", " "))
    else:
        name_pat = r"[^ ]+"
    # FAQ sentence (verified 2026-08-11): «قیمت هر لایت کوین 45.07 دلار می باشد»
    # — there is NO «در حال حاضر» prefix; the old pattern never matched.
    m = re.search(r"قیمت هر " + name_pat + r"\s*([\d,\.]+)\s*دلار", text)
    if not m:
        # World indices & some global commodities have NO FAQ price sentence.
        # Their live value is in the header block (verified 2026-08-15):
        #   <span class="price" data-col="info.last_trade.PDrCotVal">5,623.5</span>
        m = re.search(
            r'data-col="info\.last_trade\.PDrCotVal">\s*([\d,\.]+)\s*<',
            html)
        if m:
            price = m.group(1).replace(",", "")
            # change follows right after in a change-percentage span
            ch = re.search(
                r'last_change_percentage">\s*<span class="change[^"]*"[^>]*>'
                r'\s*([^<]*?)\s*<', html, re.S)
            pct = ""
            if ch:
                # TGJU uses Arabic minus sign "−" for negative changes (e.g.
                # "−0.12"), but a BARE "-" means "no change data" (many world
                # indices). Only treat a sign as meaningful when digits follow.
                dirty = ch.group(1).strip()
                digits = re.sub(r"[^\d\.]", "", dirty)
                if digits:
                    sign = "low" if ("−" in dirty or "-" in dirty) else \
                           ("high" if "+" in dirty else "")
                    pct = (("-" if sign == "low" else "+") + digits) if digits else ""
                else:
                    sign = ""
            return {"name": name or slug, "price": price, "change_pct": pct,
                    "change_amt": "", "dir": sign}
        return {}
    price = m.group(1).replace(",", "")
    # change: «درصد تغییر نسبت به روز گذشته 0.18% میزان تغییر نسبت به روز گذشته 0.08»
    ch = re.search(
        r"درصد تغییر نسبت به روز گذشته\s*([\d,\.]+)%?\s*"
        r"میزان تغییر نسبت به روز گذشته\s*([\d\.,]+)", text)
    pct = ""
    amt = ""
    if ch:
        pct = ch.group(1).replace(",", "")
        amt = ch.group(2).replace(",", "")
    # direction: derive from prev-day price («نرخ روز گذشته 45.15»)
    prev = re.search(r"نرخ روز گذشته\s*([\d,\.]+)", text)
    direction = ""
    if prev:
        try:
            direction = ("high" if float(price) > float(prev.group(1).replace(",", ""))
                         else ("low" if float(price) < float(prev.group(1).replace(",", "")) else ""))
        except ValueError:
            direction = ""
    return {"name": name or m.group(0).split("قیمت هر ")[-1].split()[0],
            "price": price, "change_pct": pct,
            "change_amt": amt, "dir": direction}


def get_all_prices() -> dict:
    """Fetch homepage once, return {slug: row} for every slug present."""
    try:
        html = fetch_html()
        return parse_rows(html)
    except Exception:
        return {}