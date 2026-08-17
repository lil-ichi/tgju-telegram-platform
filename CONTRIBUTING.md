# Contributing to TGJU Telegram Platform

Thanks for your interest! This project runs on a few simple conventions that
keep it maintainable.

## Development setup

```bash
git clone https://github.com/lil-ichi/tgju-telegram-platform.git
cd tgju-telegram-platform
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS / git-bash
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
python -m pytest -v
```

All tests are hermetic: they use `tmp_path` fixtures and never touch live
runtime state, the network, or Telegram.

## Code style

- Python 3.11+, type hints on public functions
- Persian strings for user-facing content; English for code/docs
- Keep `tgju_engine_*.py` single-purpose; shared services live in
  `platform/tgju_core/`

## Before you open a PR

1. Run `python -m pytest` — all green.
2. Run `python -m compileall -q platform tests` — no syntax errors.
3. If you changed the app structure, update `APP.md` (this is a hard rule —
   `APP.md` is the canonical agent-facing doc).

## Security

See [SECURITY.md](SECURITY.md). Never commit tokens, channel IDs, or
`state/` files.

## Commit messages

Concise, imperative, one logical change per commit. Example:

```
Add unit tests for Rial→Toman conversion

Covers floor division, comma stripping, and unparseable input.
```
