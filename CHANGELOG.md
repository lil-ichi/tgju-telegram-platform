# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Test suite (`tests/`) — 47 tests covering the format engine (Rial→Toman
  conversion, unit conventions, Persian digits), the YAML channel config
  loader, and the core idempotency service (duplicate-post protection).
- CI pipeline (`.github/workflows/ci.yml`) — runs tests + compile checks on
  Python 3.11/3.12, Ubuntu and Windows.
- `pyproject.toml` — proper packaging (`pip install -e .`), console entry
  point `tgju-platform`, pytest configuration.
- `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` — project governance docs.

### Changed
- Launcher scripts (`start-platform.bat` / `start-platform.sh`) are now
  portable: they resolve `TGJU_PYTHON` → repo-local `.venv` → `python` on
  PATH, with no hardcoded machine paths.
- Removed hardcoded `Agent 10` username from `tgju_platform.py`,
  `tgju_multi.py`, `tgju_engine_bot.py` — token lookup now uses
  `expanduser()` (identical result, any machine).
- `platform/channels.yaml` — real Telegram channel IDs replaced with empty
  placeholders (public repo safety). Re-enter via dashboard or YAML.
- `tgju_platform.py` — `main()` entry point extracted.

## [2.0.0] — 2026-08-16

### Added
- New modular core: `platform/tgju_core/` (channels, health, runs, events,
  idempotency, secrets, approval, simulation, types).
- WhatsApp (Meta Cloud API) + Bale delivery from the same dashboard.
- AI orchestrator: 4 customizable jobs (analysis, poll select, poll generate,
  news summary) with per-channel provider/model.
- Functions registry (`state/functions.json`) — analysis/news/poll scheduling
  with priority (analysis → news → poll → rotation).
- Bot profiles (`state/bot_profile.json`) — multi-bot, switch without restart.
- Units system — rial→toman conversion with slug domains (domestic / USD /
  world-index points).

### Fixed
- Dashboard UI: warm-white redesign, blue accent, animated sidebar
  (replaced the dark indigo/gold theme).
- Font-face duplication breaking the whole `<style>` block.
- Div-balance bug that rendered tabs below the sidebar.

## [1.1.0] — 2026-08-10

### Added
- Non-blocking `/api/status` (cached, never hits the network).
- Background price refresher with configurable TTL.
- Live connection monitoring (`getMe`, `getChat`, `getChatMember`).
- 24-question safe poll pool with 4-hour rotation.

## [1.0.0] — 2026-08-09

### Added
- Initial release: 9 Telegram channels, chip-format price tables, TGJU news
  rotation, native Telegram polls, optional AI analysis, Persian RTL
  dashboard, FastAPI backend on :8791.
