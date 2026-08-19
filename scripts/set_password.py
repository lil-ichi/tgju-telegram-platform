# -*- coding: utf-8 -*-
"""Change the dashboard password for a platform user.

Usage (from the repo root):
    python scripts/set_password.py <username> <new-password>

Examples:
    python scripts/set_password.py tgadmin 'My-New-Pass-2026'
    python scripts/set_password.py tgadmin            # prompts (hidden input)

The platform ships with a premade account (tgadmin / admin@tg — see
SECURITY.md).  Because that password is public (documented in the repo),
it MUST be rotated after first login.  This script rewrites the user's
PBKDF2 hash inside state/auth.json.

Only works while the platform is NOT running (auth.py caches the file; a
running server would overwrite this change on its next save).
"""
import getpass
import hashlib
import json
import os
import secrets
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_PATH = os.path.join(BASE, "tgju", "state", "auth.json")
PBKDF2_ITERATIONS = 310_000
PBKDF2_DKLEN = 32


def set_password(username, password):
    if not username or not password:
        return False, "username and password are required"
    if len(password) < 8:
        return False, "password too short (min 8 chars)"
    data = {}
    if os.path.exists(AUTH_PATH):
        with open(AUTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
    data.setdefault("users", {})
    data.setdefault("active_sessions", {})
    data.setdefault("setup_complete", True)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"),
                                 PBKDF2_ITERATIONS, dklen=PBKDF2_DKLEN).hex()
    data["users"][username] = {"password_hash": digest, "salt": salt,
                               "iterations": PBKDF2_ITERATIONS}
    # invalidate all existing sessions on password change
    data["active_sessions"] = {}
    os.makedirs(os.path.dirname(AUTH_PATH), exist_ok=True)
    tmp = AUTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AUTH_PATH)
    return True, "password updated for '%s' (all sessions invalidated)" % username


def main():
    if len(sys.argv) >= 3:
        username, password = sys.argv[1], sys.argv[2]
    else:
        username = sys.argv[1] if len(sys.argv) >= 2 else input("username: ").strip()
        password = getpass.getpass("new password: ")

    ok, msg = set_password(username, password)
    print(("OK: " if ok else "ERROR: ") + msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()