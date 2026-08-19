# TGJU Telegram Platform — Test Coverage Gap Analysis

> Generated: 2026-08-18 · Method: `python -m pytest tests/ --cov=tgju --cov-report=term-missing`
> Result: **47 tests pass**, total coverage **11%** (5244 statements, 4660 missed).
> Scope: read-only analysis — no code written, per task.

## 1. Summary

The existing test suite (`tests/test_core_idempotency.py`, `tests/test_format.py`, `tests/test_config.py`) covers exactly **3 of the ~25 tgju modules** — and only partially (idempotency 66%, format 47%, config 33%). Everything that actually does the platform's work — scraping, news, AI, orchestration, delivery, and the entire 2813-line webapp — has **zero test coverage**.

### Modules with ZERO coverage (the full list)

| Module | Statements | Coverage | Notes |
|---|---|---|---|
| `tgju_engine_ai.py` | 258 | **0%** | 16 functions, incl. `run_analysis`, `test_provider`, `_chat_completion`, `_parse_json_response` |
| `tgju_engine_news.py` | 165 | **0%** | article fetch, rotation, `analysis_line` |
| `tgju_engine_functions.py` | 32 | **0%** | schedulable-task config (analysis/poll/news) |
| `tgju_engine_orchestrator.py` | 147 | **0%** | channel row selection, backfill, `build_for_channel`, `post_channel` |
| `tgju_engine_scrape.py` | 121 | **0%** | HTML parsing, profile backfill |
| `tgju_core_integration.py` | 168 | **0%** | snapshot/run/event/idempotency pipeline bridge |
| `tgju_platform.py` | 1865 | **0%** | 83 API routes, settings, polls, Telegram send, scheduler |
| `tgju_engine_fallback.py` | 99 | **0%** | outage resilience (fallback prices/news/analysis) |
| `tgju_engine_bot.py` | 71 | **0%** | bot token/profile management |
| `tgju_engine_bale.py` | 135 | **0%** | Bale messenger delivery |
| `tgju_engine_whatsapp.py` | 301 | **0%** | WhatsApp delivery |
| `tgju_multi.py` | 111 | **0%** | multi-platform CLI |
| `tgju_core_sources.py` | 89 | **0%** | core data-source layer |

Partially covered: `tgju.tgju_core.idempotency` (66%), `tgju_engine_format` (47% — `build_message`, `star_block`, `chip_line`, `slug_profile`, `stale_notice`, `_render_template` untested), `tgju_engine_config` (33% — `save_channels`, `rename_slug`, `log_line` untested), and the rest of `tgju_core/*` (16–29%).

**Cannot-be-checked note:** the `tgju_core_integration` module imports `tgju_core` singletons and touches `state/` dirs, so it is counted automatically by coverage; a full integration test of it is only meaningful in a harness — its risk is assessed below (labelled where appropriate: **needs manual review** for the parts that require a running app).

## 2. Risk of shipping each module untested

The platform is a **scheduled broadcaster**: it posts to real Telegram channels on a timer. Every untested module below is a point where a regression means **subscribers get wrong prices, duplicate posts, or nothing at all — silently**.

### `tgju_engine_scrape.py` — the raw data source (0%, 121 stmts)
- **What could break:** the parser is regex-based against tgju.org's live HTML (`parse_rows` scans `<tr>` tags, quote-aware `_tag_close`, change-cell `<span class="high|low">(0.6%) 26.39</span>`). TGJU changes one class name or attribute order → every price parses as `""` → **all channels post "—" dashes or empty rows**. The homepage-duplicate-slug logic (crypto-bitcoin appears twice) and SLUG_ALIASES fallback are delicate.
- **Why the user notices:** the channel instantly shows empty/`—` prices; because `get_all_prices` swallows exceptions and returns `{}`, the failure is silent — no log error, no alert, just blank posts.
- **Suggested tests:** golden-file HTML fixtures (saved snapshots of real tgju.org pages) → assert `parse_rows` extracts expected slug/price/pct/dir; unit tests for `_tag_close` (quotes in attrs), duplicate-slug skip, `SLUG_ALIASES`, `fetch_profile_price` regexes (FAQ sentence, header-block fallback, Arabic-minus handling), `slash_price`, `get_all_prices` exception path. No network: monkeypatch `fetch_html`.

### `tgju_engine_news.py` — the news line (0%, 165 stmts)
- **What could break:** `pick_rotating` is stateful (persists used-ids per channel, day-reset, "never repeat last headline"). A bug here = **the same headline posted repeatedly** or the news line vanishing. `_og_description` regex misses a meta-tag format change → empty `text` → articles silently dropped → `analysis_line` returns "" → **news slot posts nothing**. The `_category_id_map` discovery (24h cache) failing → all category URLs 404 → empty feed.
- **Why the user notices:** news line disappears from posts (channel looks dead), or repetitive identical headlines that look like a broken bot. Escaping bug in `analysis_line` (`& < >`) → Telegram HTML parse error → **entire post rejected by the API**.
- **Suggested tests:** `pick_rotating` with temp state dir (rotation, no-repeat-after-reset, all-seen wrap-around, empty arts); `_article_links`/`_og_description`/`_category_url` with HTML fixtures; `analysis_line` with monkeypatched `channel_articles` (success, empty→fallback chain, escaping of `&<>`).

### `tgju_engine_ai.py` — AI pipeline (0%, 258 stmts)
- **What could break:** the exact scenario named in the task — `max_tokens` too small starves reasoning models (`reasoning_content` before `content` → **empty analysis text**, 3 retries, then error). `_parse_json_response` (tolerant of concatenated/streamed objects) breaking → every `_chat_completion` raises ValueError → `run_analysis` always fails → **AI analysis posts stop**; `test_provider` mis-reporting (auth probe only via chat/completions — a regression to a /models-only check would bless broken keys as healthy); `_channel_domain` misrouting (a channel named «نرخ ارز» containing a crypto slug must stay FX — a regression here produces gold-analysis on an FX channel).
- **Why the user notices:** analysis slot posts nothing (or an error line), the UI's "test provider" button lies, and analyses are about the wrong market.
- **Suggested tests:** mock `urllib.request` (or inject a fake HTTP layer): `_chat_completion` success/HTTP-error/malformed-JSON; `_parse_json_response` (strict, trailing garbage, concatenated objects, unbalanced braces); `run_analysis` guard rails (disabled → error, mock provider → error, missing model/base_url → error, empty-text retry → final error, success → activity recorded); `test_provider` (mock ok, 401/403, HTTP 5xx, network error); `_channel_domain` matrix (name vs slug precedence); `route_category`/`auto_build_routing`; `load_ai_config` merge semantics.

### `tgju_engine_orchestrator.py` — per-channel assembly (0%, 147 stmts)
- **What could break:** `get_channel_rows` precedence is intricate — slug overrides (manual_price > name > homepage > profile backfill > disk fallback) and the parallel backfill with 3-worker throttle (429 avoidance). A precedence regression = **manual admin prices silently ignored, or fallback (stale) prices posted as if fresh**. `_backfill_one` retry + caching + fallback ordering (profile cache → disk fallback → live fetch → retry → disk fallback again) is a 5-tier chain; any broken tier changes what subscribers see during an outage. `build_for_channel`/`post_channel` — a channel without telegram_id should raise before sending.
- **Why the user notices:** wrong prices posted that an admin explicitly overrode; stale numbers during a TGJU outage with no visible stale-notice banner; or an exception mid-post leaving the channel silent.
- **Suggested tests:** `get_channel_rows` with crafted `all_rows`/overrides fixtures (each precedence branch, incl. backfill-failure-with-manual-price); `slug_group_map`; `_backfill_one` cache-hit / fallback-hit / fetch-retry / total-failure with monkeypatched network; `build_for_channel` with `with_analysis` on/off + stale flag; `post_channel` ValueError on missing telegram_id, send called once.

### `tgju_engine_functions.py` — schedulable tasks (0%, 32 stmts)
- **What could break:** `load_functions` merge semantics (saved file over defaults, `channels` dict deep-merged) — a regression drops per-channel intervals or defaults; `function_channel_enabled` (master switch + per-channel opt-in) — **functions firing for channels that disabled them** (unwanted polls/analyses) or never firing; `function_channel_interval` int-clamping.
- **Why the user notices:** polls/analyses appear on channels that turned them off (spam), or the schedule silently stops.
- **Suggested tests:** pure unit — config merge with partial JSON, enabled-gating matrix (master off, per-channel off/on, missing entries), interval fallback + `max(1, ...)` clamp. Monkeypatch `FUNCTIONS_PATH` to a tmp file.

### `tgju_core_integration.py` — the core bridge (0%, 168 stmts)
- **What could break:** `orchestrated_post` is the scheduler/webapp pipeline: snapshot → run record → decision → message build → delivery with mode handling (auto/approval/manual) and idempotency guard. A regression in the duplicate-guard ordering = **duplicate posts to channels** (the exact thing `test_core_idempotency` protects at unit level, but the integration wiring here is untested); a regression in `legacy_to_core` mapping = wrong `post_types`/schedule translated to the v2 schema → content decisions wrong; `explain_run`/`command_center` feeding the UI — a crash here takes down the dashboard.
- **Why the user notices:** duplicate messages in channels (idempotency bypassed), posts sent despite manual/approval mode, or a broken Command Center.
- **Suggested tests:** integration with tmp state dirs (point the module's STATE paths at tmp_path): `build_snapshot`/`current_snapshot`, `legacy_to_core` schema mapping, `orchestrated_post` for each mode (auto with fake `send_fn` ok/fail, manual → prepared, approval → queued, duplicate → cancelled), `simulate_channel`, `explain_run` found/not-found. Label: **needs manual review** for the parts that require the running app (full FastAPI stack).

### `tgju_platform.py` — the webapp (0%, 1865 stmts, 83 routes)
- **What could break:** everything users touch: settings save/load, channel CRUD, slug overrides/rename, poll pool management, AI provider config, bot token handling, the scheduler loop, and `send_telegram`/`send_telegram_poll` retry logic (timeout + retry count). `get_bot_token` fallback chain (bot_profile → legacy .env) — a regression sends posts with an empty token → **every send fails with "TELEGRAM_BOT_TOKEN not found"**. `refresh_prices` cache/fallback logic — regression = stale or blank dashboard; `pick_poll` AI-vs-random fallback (25s budget) — regression = poll spam or duplicate questions. 83 routes with zero request-handler tests means any refactor (e.g. the recent multi-platform additions) can break the UI silently.
- **Why the user notices:** the dashboard at :8791 breaks, posts stop, settings won't save, polls repeat.
- **Suggested tests:** FastAPI `TestClient` smoke suite over the main routes (settings, channels, slugs, polls, ai, functions, command_center, simulate) with state paths pointed at tmp dirs and network monkeypatched; unit tests for `load_settings`/`save_settings` (unknown-key filtering), `get_bot_token` chain, `send_telegram` retry/mock HTTP, `pick_poll` fixed/random/AI-fallback, `refresh_prices` TTL + fallback-load branches. Full end-to-end scheduler: **needs manual review** (runs on a timer; verify via live run + platform.log).

### Secondary untested modules (delivery/ops layer)
| Module | Risk if untested |
|---|---|
| `tgju_engine_fallback.py` (99) | Outage path: fallback prices/news/analysis are the ONLY thing that keeps channels alive when tgju.org is down. A regression here = blank posts during an outage. Test: tmp-dir save/load roundtrips, corrupt-file tolerance. |
| `tgju_engine_whatsapp.py` (301) | 33 functions of WhatsApp delivery; a regression = WhatsApp channel silent or duplicated sends. Test: mock HTTP, message building. |
| `tgju_engine_bale.py` (135) | Bale delivery; same as WhatsApp. Test: mock HTTP. |
| `tgju_engine_bot.py` (71) | Token/profile selection; regression = auth failures on ALL channels. Test: tmp profile state. |
| `tgju_multi.py` (111) | Multi-platform CLI wiring; regression = launch scripts break. Test: CLI invocation with mocked platform. |
| `tgju_core_sources.py` (89) | Data-source layer; regression = bad source rows reach the orchestrator. Test: fixtures. |
| `apply_css.py` / `repair_css.py` (71) | One-off UI tooling scripts — **low risk**, label: skip / manual. |

## 3. Recommendation table

| Module | Current tests | Risk if untested | Suggested test type |
|---|---|---|---|
| `tgju_engine_scrape` | none (0%) | Parser breaks on tgju.org HTML change → **all prices post as "—"**; silent `{}` fallback hides failure | Golden-file HTML fixtures + regex unit tests; monkeypatch `fetch_html` (no network) |
| `tgju_engine_news` | none (0%) | Same headline repeated / news line vanishes / unescaped `&<>` → **whole post rejected by Telegram** | Unit: `pick_rotating` rotation+no-repeat with tmp state; HTML-fixture tests for `_article_links`/`_og_description`/`_category_url`; `analysis_line` fallback chain |
| `tgju_engine_ai` | none (0%) | Small `max_tokens` starves reasoning models → **empty analysis, all AI posts fail**; `test_provider` lies; wrong-market analyses | Mocked-HTTP unit tests: `_chat_completion`/`_parse_json_response` edge cases, `run_analysis` guard rails + retries, `test_provider` auth codes, `_channel_domain` precedence matrix |
| `tgju_engine_functions` | none (0%) | Per-channel switches/merges break → **unwanted polls/analyses fire on disabled channels** or schedule stops | Pure unit: config merge, enabled-gating matrix, interval clamp (tmp `FUNCTIONS_PATH`) |
| `tgju_engine_orchestrator` | none (0%) | Override precedence regression → **manual admin prices ignored, stale fallback posted as fresh**; backfill tier chain breaks during outages | Unit with crafted `all_rows`+overrides fixtures: every precedence branch, `_backfill_one` 5-tier chain (monkeypatched network), `post_channel` guard |
| `tgju_core_integration` | none (0%) | Idempotency wiring regression → **duplicate posts**; mode handling (auto/approval/manual) breaks → posts bypass approvals | Integration with tmp `STATE` dirs: `orchestrated_post` per mode + fake `send_fn`, `simulate_channel`, `explain_run`; running-app parts: **needs manual review** |
| `tgju_platform` | none (0%) | 83 routes + scheduler + send logic untested → **dashboard breaks, posts stop, settings/polls/ai UI breaks**; empty-token sends fail silently | FastAPI `TestClient` route smoke suite (tmp state, mocked network) + unit tests: `load/save_settings`, `get_bot_token` chain, `send_telegram` retries, `pick_poll`, `refresh_prices` branches; live scheduler: **needs manual review** |
| `tgju_engine_fallback` | none (0%) | Outage resilience breaks → **channels blank during tgju.org downtime** | Unit: tmp-dir save/load roundtrips, corrupt-file tolerance |
| `tgju_engine_bot` | none (0%) | Token selection regression → **auth failure on every channel** | Unit: tmp bot-profile state, legacy .env fallback |
| `tgju_engine_bale` | none (0%) | Bale delivery breaks → Bale channel silent | Unit: mocked HTTP send paths |
| `tgju_engine_whatsapp` | none (0%) | WhatsApp delivery breaks → WhatsApp channel silent/duplicated | Unit: mocked HTTP + message build |
| `tgju_multi` | none (0%) | Multi-platform CLI wiring breaks → launch scripts fail | CLI invocation tests with mocked platform |
| `tgju_core_sources` | none (0%) | Bad source rows reach orchestrator | Fixture-driven unit tests |
| `tgju_engine_format` | 47% (partial) | **Untested half**: `build_message` assembly, `star_block`, `chip_line` HTML link form, `slug_profile` overrides, `stale_notice` | Unit: complete the format suite — `build_message` with template/custom sections, `star_block` edge cases, `chip_line` link/plain, `slug_profile` alias+override |
| `tgju_engine_config` | 33% (partial) | Untested: `save_channels` YAML writer (special chars `@ : # *`), `rename_slug` (3-file consistency), `log_line` | Unit: roundtrip `save_channels`→`load_channels`, `rename_slug` across channels/overrides/cache, log append |
| `tgju_core/*` (orchestrator, runs, events, approval, health, channels, secrets, simulation) | 16–29% | Core decision/run/health layer mostly untested; decision regressions change what gets posted | Unit per submodule + shared integration through `tgju_core_integration` |
| `apply_css.py` / `repair_css.py` | 0% | One-off UI tooling — cosmetic only | **Low priority** — skip or manual |

## 4. Suggested priority order (what to write first)

1. **`tgju_engine_scrape`** — foundation: everything downstream depends on its parsing. Golden HTML fixtures make it deterministic.
2. **`tgju_engine_news`** — `pick_rotating` (stateful rotation) + escaping; cheap and high-value.
3. **`tgju_engine_ai`** — the `max_tokens`/empty-response behavior named in this task's context; all testable with mocked HTTP.
4. **`tgju_core_integration`** — the idempotency/duplicate-post wiring (complements `test_core_idempotency`).
5. **`tgju_engine_orchestrator`** + **`tgju_engine_format` completion** — the message that actually reaches subscribers.
6. **`tgju_platform` smoke suite** — TestClient over the 83 routes to lock the UI.
7. Remaining delivery modules (fallback, bot, bale, whatsapp, multi) — thin mocked-HTTP unit tests.

**Target:** lift total coverage from 11% to ≥60% with the first four items alone (they cover the highest-blast-radius logic); the `tgju_platform` smoke suite adds the largest single jump (1865 statements).
