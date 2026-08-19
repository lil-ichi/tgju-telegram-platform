# -*- coding: utf-8 -*-
"""tgju_core/auth.py — Credentials, sessions, lockout and the require_auth dependency.

Pure-stdlib authentication for the TGJU platform dashboard:

- Credentials + sessions live in ``tgju/state/auth.json`` (never in git):
  ``{"users": {name: {"password_hash", "salt"}}, "active_sessions": {token: {username, expires}},
    "setup_complete": false}``
- Password hashing: SHA-256 over ``salt_hex + password`` (salt = 16 random
  bytes hex).  Deliberately simple on purpose — local admin dashboard, no
  external deps allowed, requirements.txt untouched.
- Sessions: UUID4 tokens, 24h expiry, stored in auth.json.  The HTTP cookie
  ``tgju_session`` is HttpOnly + SameSite=Lax; ``Secure`` is set ONLY when the
  request arrived over HTTPS (``request.url.scheme == "https"`` or
  ``X-Forwarded-Proto: https``) — never unconditionally, because the app is
  routinely used on plain http://localhost / http://LAN-IP:8791 where an
  unconditional Secure flag silently kills the session.
- Lockout: 5 failed logins for a username within 5 minutes ⇒ 429 for the next
  5 minutes.
- Test bypass: the ``require_auth`` dependency is skipped whenever
  ``RUNTIME.get("auth_disabled")`` is truthy (existing tests import functions
  directly and never go through HTTP, but this keeps any future HTTP-level
  test suite painless), or when the request carries
  ``X-TGJU-AUTH-BYPASS: 1`` (intended for local non-browser automation only).

NOTE: sessions are pruned lazily (on load/validate) so auth.json never grows
unbounded; the file is rewritten only when something actually changed.
"""
import hashlib
import json
import os
import secrets
import threading
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# ── paths ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tgju/
AUTH_PATH = os.path.join(BASE_DIR, "state", "auth.json")

SESSION_TTL_SECONDS = 24 * 60 * 60   # 24h session lifetime
FAIL_WINDOW_SECONDS = 5 * 60         # 5 failed attempts counted within 5 min
FAIL_MAX = 5                         # ...then locked out
LOCKOUT_SECONDS = 5 * 60             # ...for 5 minutes

# in-process caches (auth.json is the source of truth; these avoid disk I/O
# on every request and make require_auth fast)
_cache = {"data": None, "mtime": 0.0}
_failed = {}        # username -> {"count": int, "first": float, "locked_until": float}
_failed_lock = threading.Lock()
_write_lock = threading.Lock()

AUTH_DISABLED_KEY = "auth_disabled"  # RUNTIME flag for tests / local automation


# ── file IO ────────────────────────────────────────────────────────────────
def _default_data() -> dict:
    return {"users": {}, "active_sessions": {}, "setup_complete": False}


def load_auth(force: bool = False) -> dict:
    """Read auth.json (cached by mtime). Returns the mutable dict."""
    try:
        mtime = os.path.getmtime(AUTH_PATH)
    except OSError:
        mtime = 0.0
    if not force and _cache["data"] is not None and _cache["mtime"] == mtime:
        return _cache["data"]
    try:
        with open(AUTH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = _default_data()
    data.setdefault("users", {})
    data.setdefault("active_sessions", {})
    data.setdefault("setup_complete", False)
    # lazy prune of expired sessions
    changed = _prune_expired(data)
    if changed:
        _save(data)
    _cache["data"] = data
    _cache["mtime"] = mtime
    return data


def _save(data: dict):
    os.makedirs(os.path.dirname(AUTH_PATH), exist_ok=True)
    with _write_lock:
        tmp = AUTH_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, AUTH_PATH)
    try:
        _cache["mtime"] = os.path.getmtime(AUTH_PATH)
    except OSError:
        pass
    _cache["data"] = data


def _prune_expired(data: dict) -> bool:
    now = time.time()
    dead = [t for t, s in (data.get("active_sessions") or {}).items()
            if not isinstance(s, dict) or float(s.get("expires", 0)) <= now]
    if dead:
        for t in dead:
            data["active_sessions"].pop(t, None)
        return True
    return False


def setup_complete() -> bool:
    return bool(load_auth().get("setup_complete"))


# ── baked bootstrap credential (NEVER plaintext — only a PBKDF2 hash) ──────
# This is the SOLE login account for the dashboard (per owner mandate:
# single account, no signup, no password change UI).  The username and a
# salted hash are the only things in source; the original password text is
# not recoverable from the hash.  Anyone who clones the repo gets the login
# screen but cannot log in — the owner rotates the hash via
# `python scripts/setup_auth_local.py` which OVERWRITES this user on next
# boot (so the owner's chosen credential wins; this is just a default).
BOOTSTRAP_USERNAME = "tgadmin"
# PBKDF2-HMAC-SHA256, 310,000 iterations (OWASP 2023)
_BOOTSTRAP_SALT = "cc04c83e2530e84c0fcb9b17a45341e1"
_BOOTSTRAP_HASH = ("52acad9db130d3f412d60c182b6778b8f8adcea8"
                   "02365744d4374caa4ec0d2e1")

# optional local override source (state/auth.local.json — git-ignored) that
# the owner can use to replace the baked bootstrap account
LOCAL_AUTH_FILE = os.path.join(BASE_DIR, "state", "auth.local.json")


def ensure_default_admin():
    """Seed the single login account (idempotent).

    Priority: local credential source (state/auth.local.json or env vars)
    wins if present; otherwise the baked bootstrap hash (tgadmin) is used
    so the dashboard is always loggable for the owner.  Never overwrites an
    existing user.  Returns True when a user exists after seeding.
    """
    data = load_auth()
    users = data.get("users") or {}
    if users:
        return True
    local = _read_local_credentials()
    if local:
        username, password = local
        data["users"][username] = hash_password(password)
        data["setup_complete"] = True
        _save(data)
        log_line("auth: seeded local account '%s' (from local credential source)"
                 % username)
        return True
    # fallback: baked bootstrap (never plaintext in repo)
    data["users"][BOOTSTRAP_USERNAME] = {
        "password_hash": _BOOTSTRAP_HASH, "salt": _BOOTSTRAP_SALT,
        "iterations": PBKDF2_ITERATIONS}
    data["setup_complete"] = True
    _save(data)
    log_line("auth: seeded bootstrap account '%s' (default hash)" % BOOTSTRAP_USERNAME)
    return True


def _read_local_credentials():
    """Return (username, password) from local sources, or None."""
    try:
        with open(LOCAL_AUTH_FILE, "r", encoding="utf-8") as f:
            lc = json.load(f)
        u = (lc.get("username") or "").strip()
        p = lc.get("password") or ""
        if u and p:
            return u, p
    except (OSError, ValueError):
        pass
    u = (os.environ.get("TGJU_AUTH_USERNAME") or "").strip()
    p = os.environ.get("TGJU_AUTH_PASSWORD") or ""
    if u and p:
        return u, p
    return None


def log_line(msg: str):
    """Append a timestamped line to state/platform.log (same file as the
    rest of the platform — best-effort, never raises)."""
    try:
        from tgju_engine_config import log_line as _log
        _log(msg)
    except Exception:
        pass


# ── hashing (PBKDF2-HMAC-SHA256, stdlib — no external deps) ──────────────
# Deliberately stronger than a bare sha256(salt+password): PBKDF2 stretches
# the key with 310,000 iterations (OWASP 2023 recommendation), which makes
# offline brute-force of a leaked auth.json impractical.  Pure stdlib
# (hashlib.pbkdf2_hmac), requirements.txt untouched.
PBKDF2_ITERATIONS = 310_000
PBKDF2_DKLEN = 32  # 256-bit derived key

def _hash_bits(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt.encode("utf-8"),
                               PBKDF2_ITERATIONS, dklen=PBKDF2_DKLEN).hex()

def hash_password(password: str, salt: str = None) -> dict:
    """Return {"password_hash": hex, "salt": hex, "iterations": n}.

    Salt = 16 random bytes hex.  Old sha256(salt+password) records (no
    'iterations' key) verify via the legacy path for backward compat.
    """
    salt = salt or secrets.token_hex(16)
    return {"password_hash": _hash_bits(password, salt), "salt": salt,
            "iterations": PBKDF2_ITERATIONS}


def verify_password(password: str, salt: str, expected_hash: str,
                    iterations: int = None) -> bool:
    """Verify against a stored hash.  Supports both PBKDF2 (has
    'iterations') and legacy sha256 records (iterations falsy)."""
    if iterations:
        return _hash_bits(password, salt) == expected_hash
    # legacy: sha256(salt_hex + password)
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest() == expected_hash


# ── setup / users ──────────────────────────────────────────────────────────
def create_user(username: str, password: str) -> dict:
    """Create (or overwrite) a user; marks setup_complete=True.

    Used by the initial-setup flow (first user) and by future admin user
    management.  Returns the user record (without the password hash).
    """
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("username and password are required")
    data = load_auth()
    data["users"][username] = hash_password(password)
    data["setup_complete"] = True
    _save(data)
    return {"username": username, "created": True}


def verify_credentials(username: str, password: str) -> bool:
    data = load_auth()
    rec = (data.get("users") or {}).get(username)
    if not isinstance(rec, dict):
        return False
    return verify_password(password, rec.get("salt", ""),
                           rec.get("password_hash", ""),
                           rec.get("iterations") or 0)


# ── sessions ───────────────────────────────────────────────────────────────
def create_session(username: str) -> str:
    """Create a 24h session token, persist it, return the token."""
    data = load_auth()
    token = uuid.uuid4().hex
    data["active_sessions"][token] = {
        "username": username,
        "expires": time.time() + SESSION_TTL_SECONDS,
        "created": time.time(),
    }
    _save(data)
    return token


def destroy_session(token: str):
    data = load_auth()
    if data["active_sessions"].pop(token, None) is not None:
        _save(data)


def validate_session(token: str):
    """Return the session dict (with 'username') if valid, else None."""
    if not token:
        return None
    data = load_auth()
    sess = data["active_sessions"].get(token)
    if not isinstance(sess, dict):
        return None
    try:
        expires = float(sess.get("expires", 0))
    except (TypeError, ValueError):
        expires = 0.0
    if expires <= time.time():
        data["active_sessions"].pop(token, None)
        _save(data)
        return None
    return sess


def current_username(request: Request):
    """Username for the request's session cookie, or None."""
    token = request.cookies.get("tgju_session")
    sess = validate_session(token)
    return sess.get("username") if sess else None


# ── lockout ────────────────────────────────────────────────────────────────
def _now() -> float:
    return time.time()


def is_locked(username: str) -> bool:
    with _failed_lock:
        rec = _failed.get(username)
        if not rec:
            return False
        now = _now()
        if rec.get("locked_until", 0) > now:
            return True
        if now - rec.get("first", now) > FAIL_WINDOW_SECONDS:
            _failed.pop(username, None)   # window expired, forget history
        return False


def register_failure(username: str) -> bool:
    """Record a failed attempt. Returns True if the user is now locked out."""
    now = _now()
    with _failed_lock:
        rec = _failed.get(username)
        if not rec or now - rec.get("first", now) > FAIL_WINDOW_SECONDS:
            rec = {"count": 0, "first": now}
        rec["count"] += 1
        locked = False
        if rec["count"] >= FAIL_MAX:
            rec["locked_until"] = now + LOCKOUT_SECONDS
            rec["count"] = 0
            locked = True
        _failed[username] = rec
        return locked


def clear_failures(username: str):
    with _failed_lock:
        _failed.pop(username, None)


def lockout_remaining(username: str) -> int:
    """Seconds until the lockout lifts (0 = not locked)."""
    with _failed_lock:
        rec = _failed.get(username)
        if not rec:
            return 0
        rem = int(rec.get("locked_until", 0) - _now())
        return rem if rem > 0 else 0


# ── FastAPI dependency ─────────────────────────────────────────────────────
def _request_is_https(request: Request) -> bool:
    if (request.url.scheme or "").lower() == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "")
    return "https" in proto.lower().split(",")


def auth_disabled(request: Request) -> bool:
    """Test/local bypass hook — RUNTIME flag or explicit bypass header."""
    try:
        from tgju_core.runtime import RUNTIME
        if RUNTIME.get(AUTH_DISABLED_KEY):
            return True
    except Exception:
        pass
    return request.headers.get("x-tgju-auth-bypass", "") == "1"


# Endpoints the UI must reach before any login exists.  The router-wide
# guard applies to every route (including "/" — FastAPI MERGES router-level
# and route-level dependencies, it cannot opt out), so require_auth skips
# these paths itself.  "/" is the public HTML shell that renders the login
# gate; the /api/auth/* set is the pre-login API.  Add any new public route
# here.
PUBLIC_AUTH_PATHS = frozenset({
    "/",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
})


async def require_auth(request: Request):
    """FastAPI dependency: 401 JSON unless a valid tgju_session cookie.

    Public auth endpoints (PUBLIC_AUTH_PATHS) are exempt.  Skipped entirely
    when RUNTIME["auth_disabled"] is set (tests / local automation) or when
    the request carries ``X-TGJU-AUTH-BYPASS: 1``.

    Raises HTTPException with a dict detail so the error body stays JSON
    (returning a Response from a dependency does NOT short-circuit FastAPI —
    only the endpoint's own return value does, so a raise is required).
    """
    if request.url.path in PUBLIC_AUTH_PATHS or auth_disabled(request):
        return None
    token = request.cookies.get("tgju_session")
    sess = validate_session(token)
    if not sess:
        raise HTTPException(status_code=401,
                            detail={"error": "unauthorized",
                                    "message": "login required"})
    return None
