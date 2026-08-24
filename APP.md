# 🛰️ TGJU Telegram Platform — Agent App Reference (APP.md)

> **The single canonical, agent-facing document for the TGJU-Telegram app.**
> Read THIS file (not the codebase) to understand the app fast. It is kept
> accurate by a watchdog cron (see §2) and by a hard rule: **any agent that
> changes the app must update this file in the same turn.**

**Repo:** `D:\Hermes\TGJU-Telegram` • **Webapp:** FastAPI on **:8791** • **Doc language:** English (app UI/messages are Persian RTL)

## 1. What this app is

**Multi-platform control center** for TGJU market data (tgju.org): the
original Telegram automation (9 channels ch1..ch9 posting price tables (chip
format), TGJU news links and polls, optional per-channel AI analysis) plus a
**WhatsApp platform** (Meta Cloud API) managed from the same dashboard. The
admin dashboard is a single RTL Persian HTML page served at `/` with a big
header platform menu (تلگرام / واتساپ). The scheduler and the background
price refresher run inside the same FastAPI process.

## 2. Update protocol (READ FIRST — you are bound by this)

1. **After ANY change to app structure** (Python modules, UI HTML,
   channels.yaml, settings defaults, launcher scripts, new API endpoints,
   config keys) **update this file in the same turn** — fix the affected
   section(s) **and** append a dated line to §21 Changelog.
2. Changes to runtime *state* (`state/*.json` data, logs, runs/events) do NOT
   need a doc update — only structural / config-surface changes do.
3. This rule is encoded in the `tgju-telegram-platform` Hermes skill — any
   agent loading that skill (the mandatory loading rule for TGJU work) is
   bound by it. There is no cron/watchdog; the doc stays fresh because the
   updating agent updates it.
4. If you change code and find APP.md wrong anywhere, fix it — no drift.
5. `README.md` (Persian, user-facing) is separate; do not duplicate it here.

## 3. Quick start

```bash
# Windows double-click (venv python is hard-coded; bare `python` from
# Explorer resolves to a WindowsApps stub with no fastapi)
D:\Hermes\TGJU-Telegram\start-platform.bat
# or git-bash
bash D:\Hermes\TGJU-Telegram\start-platform.sh
# then open http://localhost:8791
```

- **Interpreter:** any Python 3.11+ with `fastapi`, `uvicorn`, `PyYAML`
  (see `requirements.txt`); the launcher scripts resolve it automatically
  (local `.venv` → `python` on PATH, or `TGJU_PYTHON` override).
- **Verify up:** `curl -s localhost:8791/api/status` → expect `rows` ~230, small `fetch_age_seconds`.
- **Restart after code/UI edits:** find PID via `netstat -ano | grep :8791`, then
  `taskkill /PID <pid> /F`, then relaunch. The UI HTML is cached in the module
  global `UI_PAGE` on first request — a stale process serves the OLD dashboard.
- **Compile gate:** `python -m py_compile` every `platform/*.py` + `platform/tgju_core/*.py`.
- **Ports:** 8791 platform • 8788 Hermes LLM gateway (often down) • 8790 News Studio.

## 4. Repo layout

| Path | Role |
|---|---|
| `tgju/tgju_platform.py` | FastAPI app (~2,600 lines): all `/api/*` routes, RUNTIME cache, refresher_loop + scheduler_loop (Telegram + WhatsApp ticks), UI serving |
| `tgju/tgju_platform_ui.html` | Single-file RTL Persian dashboard (CSS + JS inline, Vazirmatn base64 font) — control-center header with platform menu |
| `tgju/channels.yaml` | Telegram channel definitions (slug groups, headers, news cats, schedule, post_types) — Telegram config untouched by the WhatsApp platform |
| `tgju/tgju_engine_config.py` | channels.yaml load/save (json.dumps quoting), per-channel state `state/analysis_ch*.json`, slug overrides, `rename_slug()` |
| `tgju/tgju_engine_scrape.py` | tgju.org fetch + parse (230+ slugs), profile backfill, SLUG_ALIASES |
| `tgju/tgju_engine_format.py` | Message builders: chips/star/template/footer, unit conversion (ريال→تومان), `esc()`, `plain_chip_line()` (platform-neutral) |
| `tgju/tgju_engine_orchestrator.py` | `get_channel_rows` (slug pool + overrides + throttled backfill), `build_for_channel` |
| `tgju/tgju_engine_news.py` | Category/tag pages → rotating article → TGJU og:description hyperlink |
| `tgju/tgju_engine_ai.py` | AI providers, `run_analysis`, `_chat_completion`, AI Orchestrator jobs + activity |
| `tgju/tgju_engine_functions.py` | Interval functions (analysis/poll/news) in `state/functions.json` |
| `tgju/tgju_engine_bot.py` | Multi-bot profiles `state/bot_profile.json`, legacy .env migration |
| `tgju/tgju_engine_whatsapp.py` | **WhatsApp interactive bot (NEW)**: config `state/whatsapp.json`, menus/categories, Meta Cloud API sender + mock, webhook processing, conversation sim, keyword lookup |
| `tgju/tgju_engine_bale.py` | **Bale (بله) platform**: config `state/bale.json`, Bot API sender (`tapi.bale.ai`), channel CRUD, preview, per-channel state — mirrors Telegram orchestration |
| `tgju/tgju_engine_fallback.py` | Disk fallback caches (prices/news/analysis, 12h max age) for tgju.org outages |
| `tgju/tgju_core/` | v2 architecture: types, events, runs, channels, orchestrator (decision engine), health, secrets, idempotency, approval, simulation |
| `tgju/tgju_core_integration.py` | Legacy↔core bridge: `orchestrated_post()`, `explain_run()`, `command_center()` |
| `tgju/tgju_core_sources.py` | Data sources: TgjuPricesSource / TgjuNewsSource / TgjuProfileSource |
| `tgju/tgju_multi.py` | CLI: `--list`, `--preview ch1`, `--post ch1 --real` (routes through core) |
| `tgju/state/` | settings.json, ai_config.json, ai_jobs.json, functions.json, bot_profile.json, polls.json, slug_overrides.json, profile_cache.json, fallback_*.json, platform.log, runs/, events/, approvals/, idempotency/ |
| `APP.md` | This file |
| `start-platform.bat` / `.sh` | Launchers |

## 5. Architecture & runtime behavior

- **Process model:** one FastAPI process runs everything. On startup:
  `refresher_loop()` (background asyncio task — re-fetches tgju.org every
  `fetch_ttl_seconds`, default 60) and `scheduler_loop()` (background asyncio
  task — ticks every `scheduler_interval_seconds`, default 45).
- **Non-blocking contract (user #1 requirement):** request handlers NEVER
  touch the network. `/api/status` reads `RUNTIME["last_rows"]` instantly;
  `POST /api/refresh` only schedules a background refresh and returns
  immediately. Previews/post use `cached_rows()`; AI runs via
  `asyncio.to_thread`. A 503 with a clear Persian message beats a 30s hang.
- **Fallback chain on TGJU outage:** live fetch → disk fallback
  (`state/fallback_prices.json`, 12h max age) → `RUNTIME["degraded"]=True` →
  posts get a visible "⚠️ اطلاعات..." stale notice appended after the footer.
- **Post pipeline (core):** scheduler/CLI price posts route through
  `tgju_core_integration.orchestrated_post()`:
  snapshot → ContentOrchestrator decision → legacy `build_for_channel` →
  idempotency guard → send. Produces a RunRecord + structured events.
  `explain_run(run_id)` explains "why did this post happen?" — the source of
  truth for "wrong post" complaints.

## 6. Data flow for one price post

```
tgju.org ──fetch_html──▶ parse_rows (230+ slugs, homepage)
        │
        ▼
RUNTIME["last_rows"]  (refresher_loop keeps warm; /api/status serves this)
        │
        ▼
get_channel_rows(channel, rows)     # slug pool + overrides + backfill
        │  (missing prices → profile backfill, 3 workers, 0.6s WAF retry)
        ▼
build_for_channel → build_message  # template-based chip digest
        │
        ▼
send_telegram (HTML parse_mode, esc() everything)  → api.telegram.org
```

## 7. Channels (default config, channels.yaml)

| ID | Name | tg_id set | Enabled | Focus |
|---|---|---|---|---|
| ch1 | نرخ ارز | ✅ | ✅ | دلار/یورو/پوند/درهم/تتر + ارز بانکی |
| ch2 | قیمت ابشده طلا | ✅ | ✅ | آبشده، مثقال، ۱۸/۲۴ عیار |
| ch3 | قیمت فلزات | — | ❌ | انس/نقره/پلاتین/پالادیوم + فلزات پایه |
| ch4 | قیمت سکه | — | ❌ | امامی/بهار/نیم/ربع/گرمی |
| ch5 | قیمت ارز ها دیجیتال | — | ❌ | بیتکوین/اتریوم + ۱۵+ ارز |
| ch6 | بازار جهانی | ✅ | ✅ | فلزات + شاخصهای جهانی |
| ch7 | نفت و انرژی | — | ❌ | برنت/اوپک/انرژی |
| ch8 | اخبار گزارش و تحلیل ها | — | ❌ | خلاصه اخبار TGJU |
| ch9 | قیمت ها | — | ✔* | سوپرست قیمتها (ch9 shows in status as enabled=False but posts when toggled) |

*ch9 is `enabled: false` in channels.yaml; it posted earlier via manual/scheduler history — treat config as source of truth.

Each channel: `id, name, telegram_id, enabled, icon, header, section_title,
slug_groups (group→[slugs]), slugs, news_categories, analysis_tags,
schedule_minutes, poll_enabled, with_star, with_analysis, with_footer, footer,
format, post_types, template, custom_data`.

## 8. Post types & the scheduler

- `prices` — full chip digest (header + chips + TGJU analysis hyperlink + footer)
- `news` — TGJU analysis_line hyperlink only (TGJU's own words, never AI)
- `poll` — native Telegram `sendPoll`, `is_anonymous: true` MANDATORY in channels
- `analysis` — AI text ONLY if `analysis.enabled` + real provider, else 400
- `all` — prices then poll

**Scheduler priority (`_next_post_type`)**: analysis-due > poll-boundary > news-due > rotation.
- Polls fire on the hour boundary when `poll_interval_hours` divides `now.hour`
  (default 4 → 0,4,8,12,16,20), deduped by `last_poll_at` in channel state so a
  10-min channel posts ONE poll per window, not six.
- Analysis/news are interval functions (functions.json, default 6h), tracked by
  `last_analysis_at` / `last_news_at`; enabling them never steals price slots.

## 9. Data model & the unit contract (CRITICAL — never violate)

**tgju.org `data-price` is RIALL for every domestic instrument.** The site
displays تومان after an internal ÷10 (**1 تومان = 10 ریال**). The bot must do
the same. Global instruments are USD. World indices are POINTS (bare number).

`slug_unit(slug, channel)` decides the domain:
| Domain | Rule | Examples |
|---|---|---|
| تومان | default (÷10, FLOOR to whole تومان) | price_dollar_rl, bank_usd, sekee, geram18, crypto-tether |
| دلار | prefix match (UNDIVIDED) | crypto-*, ons, silver, platinum, palladium, oil_*, coin_*, base_global_*, commodity_*, energy_* |
| نقطه | prefix match (bare number, NO label) | bourse_*, indices-* |

Overrides win in this order: `state/slug_overrides.json` unit → channel
`unit_overrides[slug]` → convention. **Always render via
`fmt_price(slug, raw, unit)` / `slug_unit()` — never output raw rial with a
تومان label.** `convert_rial_to_toman` FLOORS (`bank_usd` 1,536,655 → 153,665).

## 10. Manual slug overrides (`state/slug_overrides.json`)

Per-slug admin override: `name` (display), `profile_url` (custom link),
`manual_price`, `change_pct`, `change_amt`, `dir` (`high`/`low`), `unit`.
- `manual_price` wins over homepage + backfill; `name` override applies even
  without `manual_price` (but missing rows are still backfilled first).
- A bare "-" in a change span means **no data** — never map it to `dir=low`.
- CRUD via `/api/slugs` + the «دادهها و لینکها» dashboard tab.
- `SLUG_ALIASES` (in scrape module) maps homepage slugs that 404 on their own
  profile path: `bourse_dow → dow_jones_us`, `bourse_euro-stoxx-50 → stoxx50`.
- `rename_slug(old, new)` rewrites channels.yaml + overrides + profile cache.
- Profile backfill cache: `state/profile_cache.json`, TTL 300s — **clear it
  when iterating scraper code**.

## 11. Templates & message format

Per-channel `template` (placeholder string; `TEMPLATE_DEFAULT` when empty):
```
{icon} {header} | {weekday} {time}\n{sep}\n{star}\n{gname}\n{rows}\n{sep}\n{news}\n{footer}
```
- Placeholders: `{icon} {header} {weekday} {time} {sep} {gname} {star} {rows}
  {news} {footer}` — missing values become '' and empty lines are dropped.
- Chip row: `▸ <a href=profile>name</a> : [ <b>price unit ▲▼ pct٪</b> ]`
- Star block: biggest absolute % mover (⚡ بیشترین نوسانات).
- `SEP` = 66 underscores. `esc()` everything before `parse_mode=HTML`.
- Numbers render with Persian digits (`fa_num`) — `numeral_system` setting.

## 12. Settings (`state/settings.json`)

| Key | Default | Meaning |
|---|---|---|
| auto_post | true | Scheduler master kill-switch |
| fetch_ttl_seconds | 60 | Background refresher cadence |
| poll_interval_hours | 4 | Poll boundary window |
| max_profile_workers | 6 | Backfill parallelism (3 actually used in orchestrator) |
| scheduler_interval_seconds | 45 | Scheduler tick |
| post_retry_seconds | 0 | Delay before retrying a failed post |
| numeral_system | fa | fa \| en digit rendering |
| default_footer | "" | Appended to every price post |
| price_decimals | 0 | 0 \| 2 |
| star_chars | ⭐ | Star line symbol |
| news_max_items | 3 | Max news items per news post |
| poll_options_count | 4 | 2..10 options per generated poll |
| poll_anonymous | true | Native Telegram anonymous polls |
| telegram_timeout_seconds | 30 | HTTP timeout to api.telegram.org |
| telegram_retry_count | 2 | Retries on transient Telegram errors |

PUT validation: ints ≥1 (except `price_decimals`/`telegram_retry_count` — 0 is
valid), bools for auto_post/poll_anonymous, unknown keys dropped.

## 13. AI subsystem (`state/ai_config.json` + `state/ai_jobs.json`)

- **Analysis is OPT-IN per channel**: `cfg["channels"][cid]["analysis"] =
  {enabled, provider, model}` — default OFF. Company rule: the TGJU analysis
  line stays TGJU's own words unless AI is explicitly enabled.
- Providers in `ai_config.providers` (name → label/kind/base_url/api_key/model/
  enabled); kind ∈ mock | openai | openai_compat. `mock` = "no AI".
- `test_provider` does a REAL chat/completions probe (1-token) — /models alone
  misses bad keys. `list_provider_models` populates the UI model `<select>`s.
- `_chat_completion` POSTs `<base>/chat/completions`. **max_tokens ≥ 1000-2000
  required**: local reasoning routers (deepseek-v4-flash-free) spend tokens on
  `reasoning_content` BEFORE content — starved models return EMPTY text.
  `_parse_json_response` tolerates concatenated JSON objects.
- **AI Orchestrator jobs** (`ai_jobs.json`, editable via AI tab):
  `analysis` (max_tokens 2000, timeout 90) • `poll_select` (500, 25) •
  `poll_generate` (2000, 90) • `news_summary` (500, 60, currently disabled).
  Activity: 50-entry ring buffer, `record_ai_activity()`; API
  `GET /api/ai/orchestrator`, `POST /api/ai/jobs/{id}`, `POST /api/ai/jobs/{id}/run`.
- **Analysis prompt**: channel-aware — `_channel_domain()` maps name/tags/
  slugs to طلا / نفت و انرژی / فلزات جهانی / ارزهای دیجیتال / بورس و سهام /
  ارز و صرافی / جهانی و بینالملل (name+tags first, slugs fallback). Effort
  `standard` (3-5 sentences) / `deep` (8-12). Template
  `📈 تحلیل بازار {name} | {weekday} {time}`.
- 429 bursts → retry 3× with 5s backoff (poll generate); `content: null` →
  `.get("content") or ""`.

## 14. Polls (`state/polls.json` + POLL_POOL)

- Built-in 24-question Persian `POLL_POOL` (formal «شما», engagement-only —
  never signals/news/company risk) + store `{questions: [...], fixed:
  {cid: [...]}}`; fixed polls (per-channel) cycle with absolute priority.
- `pick_poll(channel, ai_pick)` → fixed → AI-select (if enabled+configured,
  hard 25s budget, `/models` 2s probe, then `_chat_completion`) → random
  fallback. No repeats of the last 8 (`_RECENT_POLLS`).
- **`ai_pick=True` ONLY on real posts** (scheduler/webapp POST). Preview
  endpoints MUST use `ai_pick=False` — instant, never blocks HTTP.
- Native Telegram `sendPoll`: `is_anonymous: true` MANDATORY (channels reject
  non-anonymous: `400: non-anonymous polls can't be sent to channel chats`).
  JSON body only (urlencode `"false"` 400s).
- AI generation (`POST /api/polls/generate`): returns questions for REVIEW —
  never auto-added; `POLL_GEN_PROMPT` asks for exact JSON.

## 15. Functions (`state/functions.json` — وظایف tab)

Schedulable interval tasks, INDEPENDENT of the prices/news rotation:
`analysis` (تحلیل بازار, 6h, template, effort standard|deep, max_tokens 2000,
timeout 90) • `poll` (نظرسنجی, 4h) • `news` (خبر, 6h). Each has a master
`enabled` + per-channel `channels.{cid}.{enabled, interval_hours}`.
Scheduler priority: analysis-due > poll-boundary > news-due > rotation —
prices never stolen. APIs: `GET/PUT /api/functions`,
`POST /api/functions/analysis/run/{cid}` (posts now).
Legacy channel `poll_enabled` still works as a fallback for polls.

## 16. Bot profiles (`state/bot_profile.json`)

Multi-bot support: `{active_id, profiles: [{id, name, token, bot_username,
bot_name}]}` — connect ANY Telegram bot; switching is instant (no restart).
Legacy `.env` `TELEGRAM_BOT_TOKEN` auto-migrates into a `default` profile on
first load (line-anchored `^TELEGRAM_BOT_TOKEN\s*=\s*(\S+)` — a commented
example must NOT match). Prereq: bot must be channel admin. APIs:
`GET/POST /api/bot`, `POST /api/bot/activate/{id}`, `DELETE /api/bot/{id}`,
`POST /api/bot/test`, `GET /api/bot/status`.

## 17. WhatsApp bot (interactive — `state/whatsapp.json` — 💬 tab)

The WhatsApp platform is a **SINGLE interactive bot**, not broadcast
channels. A user writes to the business number; Meta Cloud API webhook →
backend → bot replies with menus:

    WhatsApp User ─▶ WhatsApp Bot (webhook) ─▶ Backend ─▶ Price/News/AI
                                                     └────────▶ reply

**Model:** WhatsApp Business Platform (Meta Cloud API)
`graph.facebook.com/v22.0/<PHONE_NUMBER_ID>/messages`. Plain text only (the
Cloud API has NO HTML/markdown). `mock` mode (no credentials or
`settings.mock:true`) → no network, canned `wa-mock-<n>` ids — the whole
bot is demoable from the UI without a Meta app.

**Config surface** (`state/whatsapp.json`):
- `settings`: `access_token`, `phone_number_id`, `verify_token`, `mock`
  (default true), `welcome` (main menu text, 7 numbered options), `about`.
- `categories[]`: `id`, `label`, `menu_code` (1..7), `slug_groups`
  (group name → [slugs]). Defaults: 1 ارز (آزاد/بانکی), 2 طلا و سکه,
  3 بازار جهانی (فلزات/شاخصها/انرژی), 4 ارز دیجیتال, 5 اخبار, 6 تحلیل,
  7 درباره.
- `users{}`: phone → state (last category, updated_at). Registered when a
  user interacts; broadcast targets.

**Bot conversation model:**
- User sends `1..7` → category menu (numbered groups); `1-2` → group prices;
  `0` / «منو» → main menu; «قیمت X» → keyword price lookup (name match).
- News (`5`) reuses the TGJU news engine (`channel_articles`, 5 items,
  stripped of HTML). Analysis (`6`) reuses `run_analysis` with a synthetic
  channel (`ch6` بازار جهانی) — requires AI analysis enabled for ch6 in
  ai_config. About (`7`) returns the about text.
- Prices render via `fmt_price()/slug_unit()` with the unit label appended
  (fmt_price converts but never labels) + ▲▼ arrow + pct.

**Endpoints:** `GET /api/whatsapp` (settings+categories, token masked) •
`POST /api/whatsapp/settings` • `POST /api/whatsapp/test` (credential probe,
mock-aware) • `GET/POST /api/whatsapp/categories` • `POST
/api/whatsapp/simulate` (no-network conversation sim) • `POST
/api/whatsapp/broadcast` (send to one phone or all known users, mock-aware) •
`GET /api/whatsapp/status` • **Webhook**: `GET /api/whatsapp/webhook`
(hub.mode/verify_token/challenge verification) + `POST /api/whatsapp/webhook`
(parse inbound → `process_webhook` → replies via Cloud API, to_thread —
never blocks).

**CLI:** `python -m tgju_multi --platform whatsapp --list | --preview menu |
--send-menu <phone> [--real]` (mock-aware).

**No auto-broadcast scheduler** — WhatsApp is request/response via webhook.
Telegram config (`channels.yaml`) is NEVER touched by WhatsApp work.

## 17b. Bale platform (بله — `state/bale.json` — 🟢 tab)

Iranian messenger with a **Telegram-compatible Bot API**
(`https://tapi.bale.ai/bot<TOKEN>/<method>`). The platform mirrors the
Telegram channel-orchestration model: a single bot connected to N channels
(the bot must be channel admin), the backend orchestrates and posts
prices/news/polls/analysis to every channel on schedule or manually.

**Config surface** (`state/bale.json`, LEGACY-independent of channels.yaml):
- `settings`: `access_token` (Bale bot token), `auto_post` (master switch).
- `channels[]`: `id` (bale1..), `name`, `bale_id` (`@channel` or numeric),
  `enabled`, `schedule_minutes`, `icon`, `slug_groups`, `slugs`,
  `post_types` (prices/news/poll/analysis).
- Per-channel state: `state/bale_<id>.json` (last_post_at / last_error /
  message_id / last_type).

**Endpoints:** `GET /api/bale` (config, token masked) • `POST
/api/bale/settings` • `POST /api/bale/test` (getMe probe, mock-aware) •
`GET /api/bale/status` (channels + state, instant) • `GET
/api/bale/preview/{cid}?post_type=` (instant, reuses Telegram builders) •
`POST /api/bale/post/{cid}` (live send, network via to_thread) • `POST/PUT/
DELETE /api/bale/channels[/{cid}]` (CRUD).

**Scheduler:** `_bale_scheduler_tick()` runs inside `scheduler_loop()`
right after the Telegram tick — same cadence, same `_next_post_type()`
rotation, only posts when `auto_post` true AND a real token is set (never in
mock). Reuses `cached_rows()`, `build_for_channel`, `channel_articles`,
`run_analysis` — the exact same message builders as Telegram.

**Mock mode:** absent token → all previews work, `test` returns a mock bot,
posts return canned `bale-mock-<n>` ids without network. UI shows
«🧪 حالت آزمایشی».

## 18. API surface (all under :8791)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Dashboard (single HTML file, cached in `UI_PAGE` global) |
| GET | `/api/status` | All channels + cache age (INSTANT, no network) |
| GET | `/api/channels` • POST | Channel CRUD (also `/api/channels/{id}` PUT/DELETE, `/api/templates`) |
| GET/POST/PUT/DELETE | `/api/channels[/{id}]` | Channel management |
| GET | `/api/types` • PUT `/api/types/{cid}` | post_types catalog + per-channel set |
| GET | `/api/preview/{cid}?type=prices\|news\|poll\|analysis\|all&template=` | Preview (poll preview = instant random) |
| POST | `/api/post/{cid}` | Live post, body `{post_type, telegram_id?}` |
| POST | `/api/refresh` | Schedule background refresh, returns instantly |
| GET/PUT | `/api/settings` | System settings |
| GET | `/api/slugs` • PUT/DELETE `/api/slugs/{slug}` • POST `/api/slugs/rename` • POST `/api/slugs/test` | Slug inventory + manual overrides |
| GET/POST/DELETE | `/api/polls` (+ `/api/polls/{index}`, `/api/polls/delete-fixed`, `/api/polls/generate`) | Poll store |
| GET | `/api/categories` • POST/DELETE | Extra categories |
| GET/POST | `/api/ai`, `/api/ai/providers`, `/api/ai/models`, `/api/ai/test/{name}`, `/api/ai/run/{cid}`, `/api/ai/channel/{cid}`, `/api/ai/routing[/build]`, `/api/ai/orchestrator`, `/api/ai/jobs/{id}[/run]` | AI config + orchestrator |
| GET/POST/DELETE | `/api/bot`, `/api/bot/activate/{id}`, `/api/bot/test`, `/api/bot/status` | Bot profiles |
| GET/PUT | `/api/functions` • POST `/api/functions/analysis/run/{cid}` | Interval functions |
| GET | `/api/connections` • `/api/connections/probe` | Live Bot API probe (getMe/getChat/getChatMember, 60s cache) |
| GET/POST | `/api/bale` • `/api/bale/settings` • `/api/bale/test` | Bale config (token masked) + getMe probe (mock-aware) |
| GET/POST/PUT/DELETE | `/api/bale/channels[/{cid}]` | Bale channel CRUD (bale1..) |
| GET | `/api/bale/preview/{cid}?post_type=` • POST `/api/bale/post/{cid}` | Bale preview (instant) + live post (to_thread) |
| GET | `/api/bale/status` | Bale channels + per-channel state (instant) |
| GET/POST | `/api/whatsapp` • `/api/whatsapp/settings` • `/api/whatsapp/test` | WhatsApp bot config (token masked) + credential probe |
| GET/POST | `/api/whatsapp/categories` | Bot menu categories (label/menu_code/slug_groups) |
| POST | `/api/whatsapp/simulate` • `/api/whatsapp/broadcast` | No-network conversation sim + send to one/all users (mock-aware) |
| GET/POST | `/api/whatsapp/webhook` | Meta webhook verification (GET) + inbound message handling (POST) |
| GET | `/api/whatsapp/status` | WhatsApp bot status: mock, category count, known users (instant) |
| GET | `/api/command_center` | Health + runs + approvals + next actions (one call) |
| GET | `/api/explain/{run_id}` | "Why did this post happen?" trace |
| GET | `/api/runs` • `/api/events` | Observability (RunRecords + structured events) |
| POST | `/api/simulate/{cid}` • GET `/api/simulate_all` | Dry runs (no delivery) |
| GET | `/api/approvals` • POST `/api/approvals/{id}/{action}` | Content approvals |
| GET | `/api/health` • `/api/secret_health` | Health scoring + secrets status |
| GET | `/api/logs` | Last 8KB of platform.log |

## 18. tgju_core (v2 architecture)

`tgju/tgju_core/` — the "proper" architecture alongside the legacy engine:
`types.py` (ContentType/TriggerType/RunStatus/EventType enums, Snapshot,
OrchestrationDecision, RunRecord, ChannelDefinition — full v2 schema with
schedule/max_posts_per_day/formatting/ai/delivery), `orchestrator.py`
(ContentOrchestrator decision engine: config → schedule → daily limit → data →
trigger checks, per-content-type reasons), `channels.py` (ChannelManager with
version history/rollback; the bridge registers channels IN MEMORY — never
writes `state/channels_v2.json`), `runs.py`, `events.py`, `health.py`
(HealthScorer), `secrets.py` (falls back to parsing the .env),
`idempotency.py` (hash + minute-slot keys), `approval.py`, `simulation.py`.
Bridge: `tgju_core_integration.py` — `orchestrated_post()` (used by the
scheduler for price posts and by the CLI), `explain_run()`, `command_center()`.

## 19. Known pitfalls & environment facts

- **Iran → api.telegram.org timeouts** (WinError 10060): environmental, NOT a
  code bug. Retries + idempotency keys prevent duplicate posts on retry.
- **UI stale-server gotcha**: `UI_PAGE` caches the HTML on first request — after
  editing the HTML you MUST kill + relaunch the server and verify via `curl` on
  the LIVE port (disk state ≠ what the running server serves).
- **channels.yaml save**: all string values are json.dumps-quoted — bare `@test`
  or unquoted `: #` corrupts the YAML → ScannerError → PUT 500 ("saves don't
  work"). Never `yaml.safe_dump` per scalar.
- **Profile backfill WAF**: throttle to 3 workers + 0.6s retry; 6-parallel
  intermittently trips tgju.org's rate limiter.
- **Homepage duplicates**: some slugs appear twice (one empty `data-price=""`)
  — parser keeps the first non-empty price.
- **Interpreter**: only the hermes-agent venv Python 3.11 has
  fastapi/uvicorn/yaml. `C:\Python314` is pip-enabled but lacks these.
- **Gateway**: local LLM at :20128 (hermes-2) is slow (10-17s) + intermittent
  empty responses — retry + fallback everywhere; gateway :8788 often down.
- **Dashboard rules (user)**: FULL management UI (never read-only), white
  theme, edit forms open INLINE under each row/card (never at page bottom),
  `settings-grid` 2×2 layout, 44 onclick handlers / 71 JS fns must stay valid
  (div balance 265/265).
- **search_files/rg can fail with IO errors on `/d/...` paths** — fall back to
  `grep -n` via terminal.
- **Portable pack**: `D:\TGJU-Telegram-Portable` (~35 files/528KB) is rebuilt
  after every change (kept in sync with the main repo; state stripped to
  configs only; `TGJU_PORT` env, default 8791; verify boot on :8792).

## 20. Hard rules (user expectations — never violate)

1. **Full-stack implementation**: spec changes apply to engines, platform,
   CLI, UI, state — every module that touches the feature. Never one-file
   fixes.
2. **Never block the dashboard on network** in request handlers.
3. **Unit conversion** via `fmt_price()/slug_unit()` — never raw rial as تومان.
4. **`esc()` everything** channel-supplied before `parse_mode=HTML`.
5. **Restart + curl-verify** after UI/code edits (UI_PAGE cache).
6. **AI analysis default OFF** — TGJU's own words are primary.
7. **Polls**: anonymous, formal «شما», engagement-only — never signals/news/
   company risk. `ai_pick=True` never in previews.
8. **Inline edit forms** in the dashboard, never page-bottom.
9. UI/docs Persian RTL; Persian digits (`fa_num`), `_` separators, ZWNJ.
10. **Update this file** after any structural change (see §2).

## 21. Changelog

- **2026-08-17** — Form-row alignment fix: `.row.form-row` (align-items:
  flex-end) + `.btn-group` class. Preview/send buttons now sit on the same
  baseline as their selects in EVERY panel (Bale پیش‌نمایش و ارسال, Telegram
  پیش‌نمایش و ارسال). Previously `align-items:center` centered the short
  button div against taller label+select columns → buttons floated mid-
  height. Verified via CDP: button top/bottom == select top/bottom (delta 0).
- **2026-08-17** — **Per-platform sidebar groups**: the sidebar now mirrors
  the Telegram sub-menu model for EVERY platform — «✈️ تلگرام» (کانالها،
  پیش‌نمایش، نظرسنجی، دادهها، دادههای سفارشی، ربات)، «🟢 بله» (کانالها،
  پیش‌نمایش و ارسال، ربات)، «💬 واتساپ» (اتصال و ربات، منو و دستهها،
  شبیهساز و ارسال) + always-visible «⚙️ سامانه» (هوش مصنوعی، وظایف،
  تنظیمات، فعالیتها). Platform switch swaps the group; header menu drives
  the active platform. Full control of every platform from the sidebar.
- **2026-08-17** — **Bale (بله) platform added** — Iranian messenger with a
  Telegram-compatible Bot API (`https://tapi.bale.ai/bot<TOKEN>/...`).
  Mirrors the Telegram channel-orchestration model: single bot → N channels
  (admin), scheduler auto-post (prices/news/poll/analysis with the same
  `_next_post_type` rotation), channel CRUD + preview + live post via
  `/api/bale/*`, mock mode (no token). Header menu + `panel_bale_home` UI.
  Telegram/WhatsApp untouched. Backup:
  `D:\Hermes\_wayback-TGJU-Telegram-Bale-20260817-120420`.
- **2026-08-17** — UI control-center polish: removed the sidebar «پلتفرمها»
  group (platform switching lives in the centered header menu), removed the
  🏠 داشبورد panel entirely (it hid the header menu — bug), sidebar now
  shows only the active platform's sub-menus (Telegram group fully hidden on
  WhatsApp, title included). Landing = Telegram channels panel.
- **2026-08-17** — WhatsApp redesigned from broadcast channels to a SINGLE
  **interactive bot** with user menus: 7 categories (ارز/طلا و سکه/بازار
  جهانی/ارز دیجیتال/اخبار/تحلیل/درباره) with per-group slug lists, keyword
  lookup («قیمت دلار»), news + AI analysis replies, Meta webhook
  (GET verify + POST inbound → reply), conversation simulator + broadcast in
  the UI, `--send-menu` CLI. Removed: channel model, auto-post scheduler,
  preview/post endpoints (broadcast replaced them). Telegram untouched.
  Backup: `D:\Hermes\_wayback-TGJU-Telegram-WhatsAppBot-20260817-111930`.
- **2026-08-17** — Multi-platform control center: WhatsApp platform added
  (`tgju_engine_whatsapp.py`, `state/whatsapp.json`, Meta Cloud API sender +
  mock mode, `/api/whatsapp/*` + `/api/platforms`, WhatsApp scheduler tick,
  CLI `--platform whatsapp`). Dashboard got a big header platform menu
  (تلگرام/واتساپ) + sidebar platform groups; Telegram panels and API are
  untouched (verified live). Shared format refactor: `plain_chip_line()`
  platform-neutral chip builder (Telegram HTML output unchanged — regression-
  tested). WhatsApp posts are plain text (Cloud API has no HTML); Telegram
  keeps HTML chips/polls. Backup: `D:\Hermes\_wayback-TGJU-Telegram-20260817-103614`.
- **2026-08-17** — Created APP.md as the single agent-facing reference. The
  rule "update APP.md in the same turn after any structural change" is
  encoded in the tgju-telegram-platform skill (mandatory for TGJU work), so
  every agent that changes the app keeps the doc in sync. No cron/watchdog.
- **2026-08-24** — Multi-theme system + dark/light switch in the dashboard UI
  (`tgju_platform_ui.html`): token-based skins switched via
  `<html data-theme="emerald|ocean|amber" data-mode="light|dark">` — 3 hue
  families × light/dark = 6 skins. Header pill switcher (3 round swatches +
  mode toggle) persists choice in `localStorage` (`tgju_theme`, `tgju_mode`);
  first visit respects OS `prefers-color-scheme`. All hardcoded surface /
  semantic colors converted to tokens (`--surface`, `--ok-bg/--bad-bg/…`,
  `--glow-1/2`, `--track`, `--row-hover`, `--on-accent`). Previous skin
  preserved as default (`emerald` light = «Porcelain & Emerald»). Backup:
  `tgju/tgju_platform_ui.html.bak-porcelain-20260824`. 70 tests pass.
- **2026-08-24 (2)** — «داده‌ها و لینک‌ها» tab now shows the FULL tgju.org
  slug inventory, not just channel-used slugs. `GET /api/slugs?scope=all`
  (default) merges live homepage rows + profile-cache + channel pools
  (Telegram/WhatsApp/Bale usage map per slug) + manual overrides into one
  table with `source` ∈ {homepage, profile, manual, missing}, auto-unit,
  and per-platform usage chips; `?scope=used` = legacy behavior. UI adds
  search box + source/platform filters. NEW shared
  `tgju_engine_orchestrator.apply_slug_overrides(slug, row, overrides)` is
  THE single merge point — WhatsApp `_current_rows()` now applies overrides
  too, so a manual price/name set in the dashboard flows to Telegram,
  WhatsApp AND Bale identically (Bale already routed through
  `build_for_channel` → `get_channel_rows`). Convention: `manual_price` is
  RIAL like `data-price` (÷10 for تومان items). Homepage `<th>` cells that
  embed onclick JS are cleaned by `_clean_display_name()` (keeps longest
  pure-Persian chunk). Verified E2E: PUT override → visible in WhatsApp
  keyword reply + Telegram builds; tests 69/70 pass (1 pre-existing failure:
  channels.yaml now carries a real ch1 telegram_id from user's own UI edit —
  conflicts with the repo-safety placeholder test, unrelated to this change).
- **2026-08-24 (3)** — Scheduler bug fixes + UI polish. ROOT CAUSE of the
  "3 polls in a row" bug: `tgju_engine_news.pick_rotating()` REPLACED the
  whole channel-state file (`state/analysis_ch1.json`) with only its own
  keys, WIPING `last_poll_at`/`last_analysis_at`/`last_news_at` on every news
  rotation → interval dedupe broke (news also fired every ~2min instead of
  6h). Fix: pick_rotating now MERGES into the loaded state (keys renamed to
  `news_used`, reads legacy `used` too) and stamps `last_news_at` itself.
  Poll dedupe hardened with a `last_poll_slot` fingerprint
  ("YYYY-MM-DD/window-start-hour") recorded on success — a poll can never
  repeat inside one interval window even if timestamps are lost; polls still
  fire only at boundary hours (`hour % poll_interval_hours == 0`, default
  0/4/8/12/16/20). EMPTY-POST guard: `_scheduler_tick` skips prices/analysis
  when the rows cache is empty, and skips any non-prices post whose build is
  empty — logged as «scheduler skipped …», NOT marked posted, retried next
  tick (this was the "auto posts empty on telegram" symptom: AI analysis ran
  with no price table and posted «داده‌ای در دسترس نیست» filler / Telegram
  400s during tgju.org timeouts). UI: «🔗 داده‌ها و لینک‌ها» moved from the
  Telegram sidebar group to ⚙️ سامانه (cross-platform tab); platform usage
  chips restyled as token-based `.pf-chip` pills (colored dot + count,
  theme-aware light/dark); inline slug editor card now uses `--surface-2`
  (was hardcoded #fafafa + undefined var). Verified: state-merge unit test
  passes, `_next_post_type` simulation correct at/below boundaries, 69/70
  tests pass (same pre-existing placeholder failure).
- **2026-08-24 (4)** — Activity tab rebuilt as a UNIFIED cross-platform feed +
  final poll-rotation fix. ROOT CAUSE of the remaining polls (11:50, 11:52):
  `post_types: [prices, poll]` + rotation `idx = hour % len(pts)` meant
  rotation itself emitted polls on non-boundary hours. Fix: rotation can
  NEVER return "poll" — polls are interval-only (boundary hours + slot
  dedupe). NEW `GET /api/activity?limit=&category=` merges platform.log
  (last ~400KB, classified via ACTIVITY_PATTERNS into telegram/whatsapp/
  bale/config/data/error/skip; auth noise hidden), core event-bus buffer,
  and run records into one newest-first feed with dedupe. UI «📋 فعالیت‌ها»:
  live feed (auto-refresh every 15s, toggleable), category filter dropdown,
  health+runs demoted into a collapsed <details> section. Verified live:
  feed returns telegram/data/config/error/event entries; rotation simulation
  shows zero polls across all 20 non-boundary hours; 69/70 tests pass.
- **2026-08-24 (5)** — Activity feed detail view + AI tab control + template
  tag editor. Activity rows are now expandable (click → detail card with the
  raw log line / event payload JSON + structured fields: action verb,
  channel pill, status pill failed/skipped/success) and searchable; backend
  `/api/activity` items gained `action`, `raw`, structured `detail`.
  Template editor (📝 پیش‌نمایش و ارسال → 🧩 قالب پیام کانال): variables are
  clickable chips that insert at the cursor; `{footer}` is a special dashed
  chip that opens an INLINE editor under the tag row showing the channel's
  current footer text + with_footer toggle, saved via PUT /api/channels/{cid}
  (answers «چطور مقدار {footer} را عوض کنم؟» — the tag is a placeholder, its
  VALUE lives on the channel). AI tab: per-job search filter, channel-count
  pill, effort (استاندارد/عمیق) selector on the analysis job — `effort` now
  persisted via POST /api/ai/jobs and consumed by run_analysis (overrides
  functions.json), activity list gets a 10/25/50 limit selector with richer
  rows (chars, picked poll question).
- **2026-08-24 (6)** — TAG STYLE ENGINE («🎨 استایل تگ‌ها» card in the
  preview tab). Every tag's own TEXT is now editable per channel:
  `style:{rows,weekday,time,sep,star}` on the channel object. Sub-variables:
  rows → {name} {link_name} {url} {price} {unit} {arrow} {pct} {change};
  weekday → {weekday}; time → {time}; star → {star_name} {star_pct}
  {star_arrow}. Engine: `get_style()` merges user style over STYLE_DEFAULTS;
  `render_row_line()` builds each price row from the template; build_message
  renders weekday/time/sep/star through it — default templates reproduce the
  classic output byte-for-byte (31/31 format tests pass). Persistence:
  channels.yaml stores `style_json` (flat JSON string) written by
  save_channels and parsed back by load_channels (line-based YAML writer
  untouched). API: GET/PUT `/api/channels/{cid}/style` (empty value = reset
  to default; PUT rejects templates missing their required sub-var, e.g.
  rows without {price}). UI: style editor card with per-tag inputs, live
  preview button, reset-to-default; validation errors shown in Persian.
  Verified E2E: PUT «امروز {weekday}» + custom sep → preview showed
  «امروز دوشنبه» + ━ separator → reset restored defaults.

