# Security Policy

## Reporting a Vulnerability

If you discover a security issue in this project, **do not open a public
issue**. Please report it privately by opening a
[private security advisory](https://github.com/lil-ichi/tgju-telegram-platform/security/advisories/new)
on this repository, or contact the maintainer directly through GitHub.

We aim to acknowledge reports within **72 hours** and ship a fix as soon as a
reproducible patch is available.

## Secret Handling

- Bot tokens, API keys, and channel credentials are **never committed**.
  They live in `state/` (git-ignored) or in the environment.
- A bot token looks like `123456789:AA...`. If one ever leaks, **revoke it
  immediately** in [@BotFather](https://t.me/BotFather) and regenerate.
- `state/` files are created automatically on first boot — nothing needs to
  be checked in for the app to run.

## Dashboard Access (private — no credentials in the repo)

The dashboard requires login. The platform ships **without** any credentials —
the repo contains no username or password, so a public clone cannot log in.
The **owner** creates the single account locally:

```bash
# from the repo root, BEFORE first start
python scripts/setup_auth_local.py --username yourname --password 'yourpass'
# or environment variables (also read at first boot):
#   TGJU_AUTH_USERNAME=yourname
#   TGJU_AUTH_PASSWORD=yourpass
```

- Credentials are stored **only** in git-ignored `state/auth.local.json` /
  `state/auth.json` (salted PBKDF2-HMAC-SHA256, 310,000 iterations — never
  plaintext). Nothing credential-like is ever pushed to GitHub.
- **No signup, no change-password option, one account only.** Only people you
  share the local credentials with can log in.
- Failed logins are rate-limited: **5 failures within 5 minutes ⇒ 5-minute
  lockout**.
- Sessions are 24-hour opaque tokens in `state/auth.json` (HttpOnly cookie,
  SameSite=Lax; `Secure` only over HTTPS).

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| main    | ✅ actively developed |

## Responsible Disclosure

Please include, if possible:

1. Steps to reproduce (minimal, deterministic)
2. Impact assessment (what an attacker could do)
3. Suggested fix (optional)
