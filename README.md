<div align="center">

# 🛰️ TGJU Telegram Platform

**Multi-channel market-data broadcasting & control center for [TGJU](https://www.tgju.org) (Tehran Gold & Currency Exchange)**

Automated Telegram channels, WhatsApp, Bale, Rubika & Eitaa delivery, a full Persian RTL dashboard with a built-in handbook, and an AI engine layer — all from one FastAPI app.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.133-009688?style=flat-square&logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Telegram%20•%20WhatsApp%20•%20Bale%20•%20Rubika%20•%20Eitaa-2CA5E0?style=flat-square)
![CI](https://github.com/lil-ichi/tgju-telegram-platform/actions/workflows/ci.yml/badge.svg)
![Tests](https://img.shields.io/badge/Tests-75%20passing-brightgreen?style=flat-square)

</div>

---

## ✨ Overview

TGJU Telegram Platform is a production-grade control center that turns live
[TGJU](https://www.tgju.org) market data (currency, gold, coins, metals, global
markets, oil, crypto) into **scheduled, formatted posts** delivered to your own
channels on **five platforms** — Telegram, WhatsApp (Meta Cloud API), Bale,
Rubika and Eitaa — with a fully configurable AI analysis engine and a
self-healing data layer.

Everything is managed from a **single-page Persian (RTL) dashboard**: channels,
preview & live posting, per-platform polls, the Data & Links editor, an AI
control center with live engine health, scheduled functions, connections,
settings, activity reports — plus a complete in-app Persian handbook
(راهنمای سامانه).

> **Built for the Iranian market, engineered to be portable.** The app is
> fully self-contained — one directory, one venv, zero external services.

---

## 🚀 Features

| Area | Highlights |
|---|---|
| 📡 **Market data** | 230+ TGJU slugs (currency, gold, coins, metals, global indices, oil, crypto) with profile-page fallback + a self-healing alias resolver that auto-corrects broken/renamed slugs |
| 📣 **Channels** | 9 Telegram channels out of the box — prices, news, polls, AI analysis; full CRUD from the dashboard; one-click copy of the whole channel set to Bale/Rubika/Eitaa |
| 🤖 **Post engine** | Chip-format price tables, star ratings, TGJU analysis links, custom footers, per-channel templates |
| 🗞️ **News** | Rotating TGJU news summaries with full hyperlinks (text-only, safe) |
| 📊 **Polls** | Native Telegram polls from a safe interaction-focused pool, 4-hour rotation, anonymous (required for channels); full poll management tabs for Bale, Rubika and Eitaa as well |
| 🧠 **AI engine** | Per-channel/per-job analysis via any OpenAI-compatible endpoint — live connection status card (real probe per provider), model picker fed from the provider's `/models`, model switching without restart; **off by default** (company rule: TGJU's own analysis is always the source) |
| 🕐 **Scheduler** | Priority scheduler (analysis → news → poll → rotation) with per-channel intervals; prices are never stolen by other jobs |
| 🖥️ **Dashboard** | Persian RTL single-page UI: channels, preview & post, polls per platform, Data & Links editor, AI control center, functions, connections, settings, reports, built-in handbook |
| 🔗 **Data & Links** | Per-slug inline editor (display name, profile URL, manual price, unit); rename propagates everywhere at once — channels, overrides, cache, alias map — and re-warms the price under the new key immediately |
| 🔌 **Connections** | Live bot monitoring — `getMe`, `getChat`, `getChatMember` admin checks with 60s cache; per-platform bot setup (Telegram, WhatsApp, Bale, Rubika, Eitaa) |
| 🌍 **Multi-platform** | Telegram + WhatsApp (Meta Cloud API) + Bale + Rubika + Eitaa — managed from the same dashboard |
| 🩺 **Slug Doctor** | Rule-based alias resolver: finds بدون قیمت slugs, proposes the correct TGJU page, verifies with a live fetch, and heals automatically (zero AI tokens); one-click engine test; manual ➕ add / 🗑 delete with a permanent blocklist so deleted codes never resurrect |
| 📖 **Handbook** | Built-in Persian guide (راهنمای سامانه): every tab explained in plain language + a FAQ covering outages, price fallbacks, AI errors (429/404), SSL timeouts and recovery steps |
| 🥷 **Stealth UI** | Light theme (blue accent) and dark navy theme, switched by one minimal button in the sidebar; IRANSansDN Persian typography |
| 🏥 **Core services** | Health checks, idempotency, run history, events, approval flow (`tgju/tgju_core/`) |

---

## 🖼️ Screenshots

| | |
|---|---|
| Dashboard — channel management (Persian RTL) | ![](docs/screenshots/dashboard.png) |

---

## 📦 Quick Start

### Requirements

- **Python 3.11+** (3.10 may work; 3.11 is the tested baseline)
- A Telegram **bot token** from [@BotFather](https://t.me/BotFather)
- Your bot must be an **admin** of the target channel(s)

### 1. Get the code

```bash
git clone https://github.com/lil-ichi/tgju-telegram-platform.git
cd tgju-telegram-platform
```

### 2. Install

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS / git-bash
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure your bot token

The token is read from (highest priority first):

1. The active profile in `tgju/state/bot_profile.json` (created by the
   dashboard's **Bot** tab — recommended)
2. `TELEGRAM_BOT_TOKEN` in the legacy Hermes `.env` location
   (`%LOCALAPPDATA%\hermes\.env`), or in `~/.hermes/.env` on Unix

The `state/` directory is **auto-created on first boot** and is git-ignored —
you never need to commit anything to run the app.

### 4. Run

```bash
# Windows
start-platform.bat

# Linux / macOS / git-bash
./start-platform.sh
```

Then open **http://localhost:8791** — you'll see the login screen. The
platform ships with a default login account. Log in with the username below
and the password you were given privately (stored only as a salted hash in
the code; not reversible). Share it only with trusted members:

| Username |
|----------|
| `tgadmin` |

> 🔐 The dashboard is login-protected — anyone who downloads the code must
> log in. The password is **not** in this repo (only its PBKDF2 hash is).
> There is **no signup, no password-change UI** — only one account.
>
> **To change the password** (trusted members only), you must know the
> current master password:
>
> ```bash
> python scripts/setup_auth_local.py --username tgadmin --password 'new-secret'
> ```
>
> The script will prompt for the **current master password** before accepting
> any change. Without it, access is denied. This ensures the password can
> only be modified by someone who already holds it.

> **Tip:** `TGJU_PYTHON` environment variable overrides the Python
> interpreter used by the launcher scripts.

---

## 🗂️ Project Structure

```
tgju-telegram-platform/
├── tgju/
│   ├── tgju_platform.py          # FastAPI app — dashboard + API + scheduler (:8791)
│   ├── tgju_platform_ui.html     # Persian RTL single-page dashboard
│   ├── channels.yaml             # Channel definitions (or edit via UI)
│   ├── tgju_engine_*.py          # Engine: scraping, formatting, news, polls, AI, WhatsApp, Bale
│   ├── tgju_multi.py             # CLI: --list / --preview <ch> / --post <ch> --real
│   ├── tgju_core/                # Core services: channels, health, runs, events, idempotency, secrets
│   └── state/                    # Runtime state (git-ignored, auto-created)
├── start-platform.bat            # Windows launcher (portable)
├── start-platform.sh             # Unix / git-bash launcher (portable)
├── requirements.txt
├── APP.md                        # Canonical agent-facing architecture doc
└── SECURITY.md
```

---

## 🔌 API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/status` | All channels + data age (instant, cached — never blocks on network) |
| GET | `/api/channels` | Full channel configuration |
| GET/POST/PUT/DELETE | `/api/channels[/{id}]` | Channel CRUD |
| GET | `/api/preview/{id}?type=prices\|news\|poll\|analysis\|all` | Post preview (never sends) |
| POST | `/api/post/{id}` | Live post with `post_type` |
| POST | `/api/polls/send/{platform}` | Send a poll on any platform (telegram/bale/rubika/eitaa) |
| POST | `/api/refresh` | Schedule a fresh fetch (background, instant return) |
| GET/PUT | `/api/settings` | System settings |
| GET/POST/PUT/DELETE | `/api/slugs*`, `/api/slugs/rename` | Data-tab slug CRUD; rename propagates to channels/overrides/cache/alias-map and re-warms the price instantly |
| GET | `/api/connections`, `/api/connections/probe` | Live bot connection monitoring |
| GET | `/api/providers`, `/api/ai/*` | AI provider management, tests, runs |
| GET | `/api/ai/engine_status` | Live probe of every AI provider + active model (powers the engine card) |
| POST | `/api/ai/set_model` | Switch a provider's active model (no restart needed) |
| GET | `/api/functions` | Scheduled-function registry (analysis/news/poll/slug-repair) |
| GET/POST | `/api/alias-resolver[/run|/config]` | Slug Doctor: status, manual pass, interval config |
| POST/DELETE | `/api/slugs/add`, `/api/slugs/remove/{slug}` | Add a slug (auto price-warm) / delete from all platforms + blocklist |
| GET/POST | `/api/bot*` | Bot profile management & activation |
| GET | `/api/logs` | System logs |

Full details live in [APP.md](APP.md).

---

## ⚙️ Post Types

| Type | Description |
|---|---|
| `prices` | Full chip-format price table + stars + TGJU analysis + footer |
| `news` | TGJU news summary (text + full hyperlink) |
| `poll` | Native Telegram poll from the safe pool (anonymous); Bale/Rubika/Eitaa get message-formatted polls |
| `analysis` | Optional AI-generated analysis — per-channel opt-in only |
| `all` | Prices + poll |

---

## 🤖 AI Engine Layer (optional)

The platform can generate channel analysis through any OpenAI-compatible
endpoint (local LLMs included):

- Per-channel and per-job provider/model configuration with test & preview endpoints
- **Engine status card**: every provider is probed with a tiny real request;
  green = answering right now, red = down — with the exact error (429 rate
  limit, 404 model removed, auth failure) shown in plain Persian
- Model picker populated live from the provider's `/models` endpoint; switch
  models any time, effective immediately, no restart
- Orchestrator jobs: analysis, poll selection, poll generation, news summary —
  plus the rule-based Slug Doctor (uses no tokens; its provider/model choice
  is for reporting only)
- Resilient by design: when a model disappears or rate-limits, jobs log the
  failure and retry next cycle — nothing is lost
- **Company rule:** the analysis in posted messages is always TGJU's official
  text (full `og:description` + link). AI output is purely optional and off
  by default.

---

## 🩹 Price Reliability

Market sources occasionally fail — TGJU goes down, slugs get renamed upstream,
free AI models disappear. The platform is built around those facts:

- **Fallback serving**: if TGJU is unreachable, the last known-good price for
  every source is used and flagged (`serving fallback prices`); rows with no
  verified price ever are dropped rather than posted empty
- **Alias resolution**: wrong homepage slugs resolve through rule maps and a
  persistent, human-editable alias map (`state/slug_alias_map.json`) verified
  by live fetches
- **Consistent renaming**: renaming a slug in the Data & Links tab updates
  channels, overrides, cache and the alias map together, then immediately
  fetches the fresh price under the new key
- **Graceful AI degradation**: provider/model failures surface on the engine
  card with actionable guidance instead of silent empty posts

---

## 🧭 Roadmap

- [x] Test suite (`tests/`) — 75 tests, hermetic (no network/Telegram)
- [x] CI pipeline (`.github/workflows/ci.yml`) — Python 3.11/3.12 × Ubuntu/Windows
- [x] Proper packaging (`pyproject.toml`, `pip install -e .`)
- [x] Dockerfile (one-command deploy)
- [x] Five-platform delivery (Telegram, WhatsApp, Bale, Rubika, Eitaa) with per-platform channel tabs
- [x] Slug Doctor (Tier 1): self-healing alias resolver with live verification
- [x] AI engine status card with live probes + instant model switching
- [ ] Slug Doctor Tier 2: free third-party sources (CoinGecko / open.er-api / Yahoo) for slugs with no TGJU page
- [ ] GitHub Actions release workflow (tag → PyPI/ghcr)
- [ ] Webhook-based Telegram delivery (vs. polling)
- [ ] Multi-language post templates (EN/Farsi)

---

## ⚠️ Data & Disclaimer

- Market data is sourced from **TGJU** ([tgju.org](https://www.tgju.org)).
  This project is developed by a TGJU team member as an **internal automation
  tool** published for the community. It is an unofficial project — please
  refer to tgju.org for official data and services.
- Data is provided for **informational purposes only** — not financial advice.
  Verify before making any decision.
- Polls are interaction-focused and deliberately avoid market signals or
  anything that could put the channel owner at risk.

---

## 📄 License

[MIT](LICENSE) © 2026 Ichigho (lil-ichi)

---

## 🇮🇷 فارسی

### معرفی

پلتفرم کامل مدیریت کانال‌های بازار (قیمت‌ها، اخبار، نظرسنجی و تحلیل) بر پایه داده‌های
[TGJU](https://www.tgju.org) — با داشبورد فارسی RTL و راهنمای درون‌برنامه‌ای،
زمان‌بندی خودکار، ارسال به تلگرام / واتساپ / بله / روبیکا / ایتا، لایه هوش مصنوعی
اختیاری و لایه دادهٔ خودترمیم.

ویژگی «دکتر اسلاگ»: اسلاگ‌های بدون قیمت به‌صورت خودکار پیدا و ترمیم می‌شوند
(نقشهٔ نام‌های مستعار + تأیید زنده از tgju.org، بدون مصرف توکن). افزودن و حذف
اسلاگ از تب داده‌ها با لیست سیاه دائمی برای کدهای حذف‌شده؛ تغییر نام هر اسلاگ
همه‌جا اعمال می‌شود و قیمت زیر نام جدید بلافاصله گرفته می‌شود.

«موتور هوش مصنوعی» داشبورد: وضعیت زندهٔ اتصال هر پرووایدر (آزمون واقعی)، فهرست
مدل‌ها و تغییر مدل بدون ری‌استارت؛ خطاهای رایج (۴۲۹ محدودیت نرخ، ۴۰۴ حذف مدل)
با راهنمای ساده نمایش داده می‌شوند.

### راه‌اندازی سریع

```bash
git clone https://github.com/lil-ichi/tgju-telegram-platform.git
cd tgju-telegram-platform
python -m venv .venv
# ویندوز:
.venv\Scripts\activate
# لینوکس / مک / git-bash:
source .venv/bin/activate
pip install -r requirements.txt

# اجرا:
start-platform.bat        # ویندوز
./start-platform.sh       # لینوکس / مک / git-bash
```

سپس مرورگر: **http://localhost:8791**

- توکن بات را از [@BotFather](https://t.me/BotFather) بگیرید و در تب **بات** داشبورد ثبت کنید (بات باید ادمین کانال باشد).
- همه تنظیمات، کانال‌ها، پیش‌نمایش و ارسال از داشبورد انجام می‌شود؛ فایل `channels.yaml` نیز قابل ویرایش مستقیم است.
- پاسخ پرسش‌های رایج (قطعی سایت، جایگزین قیمت، خطاهای هوش مصنوعی و…) داخل خود برنامه است: تب «📖 راهنمای سامانه» ← «❓ پرسش‌های رایج».
- دایرکتوری `state/` به‌صورت خودکار ساخته می‌شود و در گیت نادیده گرفته می‌شود (هیچ رازی کامیت نمی‌شود).
