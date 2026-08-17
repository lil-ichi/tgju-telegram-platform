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

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| main    | ✅ actively developed |

## Responsible Disclosure

Please include, if possible:

1. Steps to reproduce (minimal, deterministic)
2. Impact assessment (what an attacker could do)
3. Suggested fix (optional)
