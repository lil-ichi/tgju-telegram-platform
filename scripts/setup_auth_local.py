# -*- coding: utf-8 -*-
"""Create or update the single local login credential for the TGJU dashboard.

The dashboard is NOT publicly accessible: it ships with a baked PBKDF2 hash
of the owner's password in state/auth.json.  This script is the ONLY way
to change it — and it REQUIRES the current master password as proof of
authorization.

Usage (from the repo root, before starting the platform):
    python scripts/setup_auth_local.py                          # prompt
    python scripts/setup_auth_local.py --username alice         # prompt password
    python scripts/setup_auth_local.py --username alice --password 's3cret'

Writes state/auth.local.json  (git-ignored → never pushed to GitHub).

The value is re-read into auth.json (hashed) at next platform startup.
"""
import argparse
import getpass
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_AUTH_FILE = os.path.join(BASE, "tgju", "state", "auth.local.json")
AUTH_JSON_FILE = os.path.join(BASE, "tgju", "state", "auth.json")


def _load_auth_json():
    """Load the current auth.json (contains the baked hash)."""
    if not os.path.exists(AUTH_JSON_FILE):
        return None
    with open(AUTH_JSON_FILE, encoding="utf-8") as f:
        return json.load(f)


def _hash_bits(password: str, salt_hex: str, iterations: int) -> str:
    """PBKDF2 hash matching the server's own verifier."""
    import hashlib
    password_bytes = password.encode("utf-8")
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations)
    return dk.hex()


def _verify_current(current_password: str) -> bool:
    """Check current_password against the baked hash in auth.json."""
    data = _load_auth_json()
    if not data or not data.get("setup_complete"):
        # No auth.json yet — first boot, no verification needed
        return True
    users = data.get("users", {})
    for uname, rec in users.items():
        if not isinstance(rec, dict):
            continue
        salt = rec.get("salt", "")
        expected = rec.get("password_hash", "")
        iterations = rec.get("iterations", 310000)
        if not salt or not expected:
            continue
        candidate = _hash_bits(current_password, salt, iterations)
        if candidate == expected:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--username", default=None, help="login username")
    ap.add_argument("--password", default=None, help="new login password (default: prompt)")
    args = ap.parse_args()

    # ── Step 1: verify the current master password ──
    data = _load_auth_json()
    if data and data.get("setup_complete") and data.get("users"):
        print("🔒  This dashboard has an existing password.")
        print("    You must provide the CURRENT master password to change it.")
        print()
        current = getpass.getpass("Current master password: ")
        if not current:
            print("ERROR: current password is required")
            return 1
        if not _verify_current(current):
            print("ERROR: incorrect master password — access denied")
            return 1
        print("✔  Verified.")
        print()
    else:
        print("ℹ  No existing password found — this is first boot.")

    # ── Step 2: collect new credential ──
    username = (args.username or input("New username: ")).strip()
    if not username:
        print("ERROR: username is required")
        return 1
    if args.password:
        password = args.password
    else:
        password = getpass.getpass("New password: ")
        if not password:
            print("ERROR: password is required")
            return 1
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("ERROR: passwords do not match")
            return 1

    # ── Step 3: write local credential ──
    os.makedirs(os.path.dirname(LOCAL_AUTH_FILE), exist_ok=True)
    tmp = LOCAL_AUTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"username": username, "password": password}, f,
                  ensure_ascii=False, indent=2)
    os.replace(tmp, LOCAL_AUTH_FILE)
    print()
    print("✔  Local login credential saved to %s" % LOCAL_AUTH_FILE)
    print("   username : %s" % username)
    print("   (the dashboard seed will hash it into state/auth.json on next boot)")
    print()
    print("⚠  IMPORTANT: Keep this password secret. There is no way to recover")
    print("   it without running this script again with the current password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
