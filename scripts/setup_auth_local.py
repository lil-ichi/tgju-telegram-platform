# -*- coding: utf-8 -*-
"""Create the single local login credential for the TGJU dashboard.

The dashboard is NOT publicly accessible: it ships with NO credentials in
the repo.  The owner creates ONE username/password locally on first boot
and this is the only account — there is no signup UI, no change-password
option and no way to add users from the dashboard.

Usage (from the repo root, before starting the platform):
    python scripts/setup_auth_local.py
    python scripts/setup_auth_local.py --username alice        # prompt password
    python scripts/setup_auth_local.py --username alice --password 's3cret'

Writes state/auth.local.json  (git-ignored → never pushed to GitHub).

The value is re-read into auth.json (hashed) at next platform startup.
This file should be deleted after first boot if you prefer env vars:
    set TGJU_AUTH_USERNAME=alice
    set TGJU_AUTH_PASSWORD=s3cret
"""
import argparse
import getpass
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_AUTH_FILE = os.path.join(BASE, "tgju", "state", "auth.local.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--username", default=None, help="login username")
    ap.add_argument("--password", default=None, help="login password (default: prompt)")
    args = ap.parse_args()

    username = (args.username or input("username: ")).strip()
    if not username:
        print("ERROR: username is required")
        return 1
    if args.password:
        password = args.password
    else:
        password = getpass.getpass("password: ")
        if not password:
            print("ERROR: password is required")
            return 1

    os.makedirs(os.path.dirname(LOCAL_AUTH_FILE), exist_ok=True)
    tmp = LOCAL_AUTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"username": username, "password": password}, f,
                  ensure_ascii=False, indent=2)
    os.replace(tmp, LOCAL_AUTH_FILE)
    print("✔ local login credential saved to %s" % LOCAL_AUTH_FILE)
    print("  username : %s" % username)
    print("  (the dashboard seed will hash it into state/auth.json on next boot)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())