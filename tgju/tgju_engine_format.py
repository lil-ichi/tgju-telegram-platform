# -*- coding: utf-8 -*-
"""Message builder: Persian RTL chip-style digest per channel config."""
import re
from datetime import datetime

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
FA_WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
SEP = "_" * 66


def esc(s) -> str:
    """Escape text for Telegram's HTML parser (it rejects raw <, >, &)."""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fa_num(s) -> str:
    return str(s).translate(FA_DIGITS)


def fa_thousands(s: str) -> str:
    """Group integer part with ٬ separators, keep decimals."""
    try:
        f = float(s)
        if f == int(f):
            return fa_num(f"{int(f):,}")
        return fa_num(f"{f:,.2f}")
    except (TypeError, ValueError):
        return fa_num(s)


# ── Unit & Rial→Toman conversion ─────────────────────────────────────────
# tgju.org's data-price attribute is denominated in RIALL for every domestic
# instrument (gold, coins, forex, domestic bank rates) — the site only labels
# it تومان in the UI after an internal /10. The bot must do the same:
#   1 تومان = 10 ریال  →  تومان = ریال / 10
# Global instruments (crypto coins, precious-metals ounce, oil, commodities)
# are USD and must NEVER be divided.
# World indices (bourse_*, indices-*) are POINTS — a plain number, no
# currency — and must NEVER be divided or labelled تومان/دلار.
DOMESTIC_SLUG_PREFIXES = ()  # empty = everything not explicitly global
GLOBAL_SLUG_PREFIXES = (
    "crypto-", "ons", "silver", "platinum", "palladium", "oil_", "coin_",
    "base_global_", "commodity_", "energy_",
)
POINTS_SLUG_PREFIXES = ("bourse_", "indices-")


def slug_unit(slug: str, channel: dict = None) -> str:
    """Unit by convention:
    crypto (except tether) + metals/oil → دلار
    world indices (bourse_*, indices-*) → نقطه (points, plain number)
    tether + everything else → تومان (÷10 from Rial).
    A channel-level `unit_overrides` (dict slug → unit) wins for specific slugs;
    a slug-level override `unit` in state/slug_overrides.json also wins
    (global — the user fixes a slug once, applies everywhere).
    """
    # 1) slug-level override (global, from state/slug_overrides.json)
    try:
        from tgju_engine_config import load_slug_overrides
        so = (load_slug_overrides() or {}).get(slug) or {}
        if so.get("unit"):
            return so["unit"]
    except Exception:
        pass
    # 2) channel-level override
    if channel:
        ov = (channel.get("unit_overrides") or {}).get(slug)
        if ov:
            return ov
    # 3) convention
    if slug == "crypto-tether":
        return "تومان"
    if slug.startswith(POINTS_SLUG_PREFIXES):
        return "نقطه"
    if slug.startswith(GLOBAL_SLUG_PREFIXES):
        return "دلار"
    return "تومان"


def convert_rial_to_toman(value) -> (str, str):
    """Convert a raw tgju.org value to (toman_value_or_empty, unit).

    Domestic (تومان) items: divide the Rial figure by 10 (1 تومان = 10 ریال)
    and FLOOR to a whole تومان — matching tgju.org's own display (the site
    trims the final digit: 826,480,000 ریال → 82,648,000 تومان). Global (USD)
    items are handled by the caller via slug domain and never reach this
    conversion. Never raises — returns '' when unparseable.
    """
    try:
        f = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return "", ""
    f = int(f / 10.0)  # ریال → تومان (floor, whole تومان)
    return str(f), "تومان"


def fmt_price(slug: str, raw_price, unit: str = "") -> str:
    """Format a raw price for display: divide Rials by 10 for تومان items.

    slug decides the domain (global → USD, indices → points, untouched);
    `unit` (when given, from slug_unit) is respected for display. Returns
    the formatted string.
    """
    u = unit or slug_unit(slug)
    if u in ("دلار", "نقطه"):
        return fa_thousands(raw_price)
    val, _ = convert_rial_to_toman(raw_price)
    return fa_thousands(val) if val else "—"


def unit_label(unit: str) -> str:
    """Persian display label for a unit domain."""
    return {"تومان": "تومان", "دلار": "دلار", "نقطه": ""}.get(unit, unit)


def direction_arrow(row: dict) -> str:
    return "▲" if row.get("dir") == "high" else ("▼" if row.get("dir") == "low" else "")


# ── Tag style engine (per-channel editable tag templates) ────────────────
# Each tag can have its own mini-template with SUB-variables:
#   rows    : {name} {link_name} {url} {price} {unit} {arrow} {pct}
#   weekday : {weekday} (lets you wrap/rename, e.g. «امروز {weekday}`)
#   time    : {time}
#   star    : {star_name} {star_pct} {star_arrow} {star_lines}
#   news    : {news_url} {news_title} {news_text}
#   sep     : literal text
STYLE_DEFAULTS = {
    "rows": "▸ {link_name} : [ <b>{price} {unit}{change}</b> ]",
    "weekday": "{weekday}",
    "time": "{time}",
    "sep": "_" * 66,
    "star": ("⚡ بیشترین نوسانات\nبیشترین نوسانات : {star_name}\n"
             "بیشترین رشد/افت : [ {star_pct} {star_arrow} ]"),
    "news": "",
}

def _sub(tpl: str, ctx: dict) -> str:
    """Replace {key}s; missing keys → ''."""
    out = tpl or ""
    for k, v in ctx.items():
        out = out.replace("{%s}" % k, "" if v is None else str(v))
    return out

def get_style(channel: dict) -> dict:
    """Channel style merged over defaults (never raises)."""
    st = STYLE_DEFAULTS.copy()
    try:
        user = channel.get("style") or {}
        if isinstance(user, dict):
            for k in STYLE_DEFAULTS:
                if isinstance(user.get(k), str) and user[k].strip():
                    st[k] = user[k]
        # global default from settings.default_footer handled at call site
    except Exception:
        pass
    return st


def render_row_line(slug: str, row: dict, unit: str, profile_path: str,
                    style_tpl: str) -> str:
    """One price row rendered from a user-editable template.

    Sub-variables: {name} plain name · {link_name} name as TGJU link (or
    plain when no profile) · {url} profile URL · {price} · {unit} ·
    {arrow} ▲▼ · {pct} number+٪ · {change} ' arrow pct٪' fragment.
    """
    name = row.get("name") or slug
    if not row.get("price"):
        return _sub(style_tpl, {
            "name": esc(name), "link_name": esc(name), "url": "",
            "price": "—", "unit": "", "arrow": "", "pct": "", "change": ""})
    price = fmt_price(slug, row["price"], unit)
    if price == "—":
        return ""
    arrow = direction_arrow(row)
    pct = fa_num(row.get("change_pct") or "")
    pct_txt = (pct + "٪") if pct else ""
    change = ""
    if arrow and pct:
        change = " " + arrow + " " + pct_txt
    url = ("https://www.tgju.org/" + profile_path) if profile_path else ""
    link_name = ('<a href="%s">%s</a>' % (url, esc(name))) if url else esc(name)
    return _sub(style_tpl, {
        "name": esc(name), "link_name": link_name, "url": url,
        "price": price, "unit": unit_label(unit), "arrow": arrow,
        "pct": pct_txt, "change": change})


def chip_line(slug: str, row: dict, profile_path: str, unit: str) -> str:
    """One ▸ chip line: name link : [ price unit arrow pct ]."""
    text = plain_chip_line(slug, row, unit)
    if not text:
        return ""
    # Split the plain line into name and the bracket content to re-emit the
    # Telegram HTML form (link + <b>) exactly as before.
    m = re.match(r"^(▸ .*?) : (\[ .* \])$", text)
    if not m:
        return text
    name_txt, inner = m.group(1), m.group(2)
    name = row.get("name") or slug
    link = "https://www.tgju.org/" + profile_path if profile_path else ""
    safe_name = esc(name)
    if link:
        return "%s <a href=\"%s\">%s</a> : [ <b>%s</b> ]" % (
            "▸", link, safe_name, inner[2:-2])
    return "%s %s : [ <b>%s</b> ]" % ("▸", safe_name, inner[2:-2])


def plain_chip_line(slug: str, row: dict, unit: str) -> str:
    """One ▸ chip line WITHOUT HTML: "▸ نام : قیمت تومان ▲ ٪".

    Platform-neutral (used by WhatsApp). Empty when the price is missing
    or unparseable — callers can fill "—".
    """
    name = row.get("name") or slug
    if not row.get("price"):
        return ""
    price = fmt_price(slug, row["price"], unit)
    if price == "—":
        return ""
    pct = row.get("change_pct") or ""
    arrow = direction_arrow(row)
    unit_fa = unit_label(unit)
    inner = price + (" " + unit_fa if unit_fa else "")
    if arrow and pct:
        inner += " " + arrow + " " + (fa_num(pct) + "٪" if pct else "")
    else:
        inner += " —"
    return "▸ %s : [ %s ]" % (name, inner)


def star_block(rows: dict, slug_group_map: dict) -> str:
    """Biggest absolute % mover across the channel's slugs."""
    best = None
    label = ""
    for slug, row in rows.items():
        pct = row.get("change_pct") or ""
        try:
            apct = abs(float(pct.replace(",", "")))
        except ValueError:
            continue
        grp = slug_group_map.get(slug, "")
        if best is None or apct > best[1]:
            best = (slug, apct, row, grp)
    if best is None:
        return ""
    slug, apct, row, grp = best
    arrow = direction_arrow(row)
    pct_txt = fa_num("%.2f" % apct) + "٪"
    name = esc(row.get("name") or slug)
    line1 = "بیشترین نوسانات : %s" % name
    line2 = "بیشترین رشد/افت : [ %s %s ]" % (pct_txt, arrow)
    return "⚡ بیشترین نوسانات\n%s\n%s" % (line1, line2)


def _render_template(template: str, ctx: dict) -> str:
    """Substitute {placeholders} in a channel template with context values.

    ctx: dict of placeholder → value. Missing keys become ''. Unknown
    placeholders are left as-is (so users can keep literal braces via e.g.
    {{ }} escaping is NOT supported — keep templates simple).
    """
    out = template
    for k, v in ctx.items():
        out = out.replace("{%s}" % k, str(v))
    return out


TEMPLATE_DEFAULT = ("{icon} {header} | {weekday} {time}\n{sep}\n"
                    "{star}\n{gname}\n{rows}\n{sep}\n{news}\n{footer}")


def stale_notice(rows: dict, age_hours: float = 0.0) -> str:
    """When tgju.org is down, produce a visible Persian notice for posts.

    Added at the END of the message (after {footer}) so members clearly
    see the data is cached/old — never silently posting old numbers.
    Returns '' when data is fresh.
    """
    if age_hours <= 0:
        return ""
    if age_hours < 6:
        w = "چند ساعت"
    else:
        w = "بیش از ۶ ساعت"
    return ("⚠️ <b>اطلاع‌رسانی:</b> {source} در دسترس نیست و این اعداد "
            "{when} پیش به‌روزرسانی شده‌اند. به محض وصل شدن، قیمت‌ها اصلاح "
            "می‌شوند.").format(source="tgju.org", when=w)


def build_message(channel: dict, rows: dict, slug_group_map: dict,
                  news_line: str = "",
                  stale: bool = False, stale_age_hours: float = 0.0) -> str:
    """Assemble the full Persian RTL digest for one channel.

    A channel may set `template` — a text with {placeholders}:
        {icon} {header} {weekday} {time} {sep} {gname} {star} {rows}
        {news} {footer}
    When unset, TEMPLATE_DEFAULT reproduces the classic layout exactly.
    Empty sections are dropped (no blank lines).

    `stale` / `stale_age_hours`: when tgju.org is unreachable the caller
    passes stale=True (data came from the fallback cache). The notice is
    appended after {footer} so members always know the numbers are old.
    """
    now = datetime.now()
    style = get_style(channel)
    wd = _sub(style["weekday"], {"weekday": FA_WEEKDAYS[now.weekday()]})
    hm = _sub(style["time"], {"time": fa_num(now.strftime("%H:%M"))})
    sep = _sub(style["sep"], {})
    icon = channel.get("icon") or ""
    header_txt = channel.get("header") or channel.get("name", "TGJU بازار")
    gname = channel.get("section_title", "قیمت‌ها")

    # rows block — rendered from the per-channel {rows} style template
    rows_tpl = style["rows"]
    row_lines = []
    for slug, row in rows.items():
        unit = slug_unit(slug, channel)
        profile = slug_profile(slug)
        cl = render_row_line(slug, row, unit, profile, rows_tpl)
        if cl:
            row_lines.append(cl)
    rows_txt = "\n".join(row_lines)

    star = ""
    if channel.get("with_star", True) and rows:
        best = None
        for slug, row in rows.items():
            pct = row.get("change_pct") or ""
            try:
                apct = abs(float(pct.replace(",", "")))
            except ValueError:
                continue
            if best is None or apct > best[1]:
                best = (slug, apct, row)
        if best is not None:
            _, apct, brow = best
            arrow_b = direction_arrow(brow)
            star = _sub(style["star"], {
                "star_name": esc(brow.get("name") or ""),
                "star_pct": fa_num("%.2f" % apct) + "٪",
                "star_arrow": arrow_b,
            })

    ctx = {
        "icon": esc(icon),
        "header": esc(header_txt),
        "weekday": wd,
        "time": hm,
        "sep": sep,
        "gname": esc(gname),
        "star": star,
        "rows": rows_txt,
        "news": news_line or "",
        "footer": esc(channel.get("footer") or "به‌روزرسانی: هر ۱۰ دقیقه | منبع: tgju.org") if channel.get("with_footer", True) else "",
    }
    tpl = channel.get("template") or TEMPLATE_DEFAULT
    text = _render_template(tpl, ctx)

    # drop empty placeholder lines (blank rows from missing sections)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


def slug_profile(slug: str) -> str:
    """Profile path for a slug.

    Honors manual overrides (state/slug_overrides.json — custom profile_url)
    and SLUG_ALIASES (wrong homepage slugs that 404 on their own path).
    """
    from tgju_engine_config import load_slug_overrides
    from tgju_engine_scrape import SLUG_ALIASES
    ov = (load_slug_overrides() or {}).get(slug) or {}
    purl = (ov.get("profile_url") or "").strip()
    if purl:
        # allow full URL or bare path; normalize to path
        if "://" in purl:
            from urllib.parse import urlparse
            purl = urlparse(purl).path.lstrip("/")
        return purl
    alias = SLUG_ALIASES.get(slug, slug)
    if slug.startswith("crypto-"):
        return "crypto/currency/" + alias.replace("crypto-", "")
