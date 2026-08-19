# -*- coding: utf-8 -*-
"""Tests for tgju_core.auth — hashing, sessions, lockout, dependency guard.

Hermetic: redirects AUTH_PATH to a temp file so real state/ is never touched.
"""
import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tgju"))
import tgju_core.auth as auth


@pytest.fixture(autouse=True)
def _isolated_auth(tmp_path):
    """Point auth at a temp file and reset caches between tests."""
    auth.AUTH_PATH = str(tmp_path / "auth.json")
    auth.LOCAL_AUTH_FILE = str(tmp_path / "auth.local.json")
    auth._cache["data"] = None
    auth._cache["mtime"] = 0.0
    auth._failed.clear()
    yield


class TestHashing:
    def test_hash_uses_pbkdf2_with_iterations(self):
        rec = auth.hash_password("pw")
        assert rec["iterations"] == auth.PBKDF2_ITERATIONS
        assert rec["salt"]
        assert rec["password_hash"]

    def test_verify_roundtrip(self):
        rec = auth.hash_password("correct horse battery staple")
        assert auth.verify_password("correct horse battery staple",
                                    rec["salt"], rec["password_hash"],
                                    rec["iterations"])
        assert not auth.verify_password("wrong", rec["salt"],
                                        rec["password_hash"], rec["iterations"])

    def test_salts_are_unique(self):
        a = auth.hash_password("same")
        b = auth.hash_password("same")
        assert a["salt"] != b["salt"]
        assert a["password_hash"] != b["password_hash"]

    def test_legacy_sha256_record_still_verifies(self):
        import hashlib
        salt = "deadbeef"
        old_hash = hashlib.sha256((salt + "old-pass").encode()).hexdigest()
        # no "iterations" key => legacy path (iterations falsy)
        assert auth.verify_password("old-pass", salt, old_hash, 0)
        assert not auth.verify_password("nope", salt, old_hash, 0)


class TestSetupAndCredentials:
    def test_create_user_marks_setup_complete(self):
        auth.create_user("admin", "pw1234")
        assert auth.setup_complete()
        data = json.load(open(auth.AUTH_PATH, encoding="utf-8"))
        assert "admin" in data["users"]
        assert data["users"]["admin"]["salt"]

    def test_verify_credentials(self):
        auth.create_user("admin", "pw1234")
        assert auth.verify_credentials("admin", "pw1234")
        assert not auth.verify_credentials("admin", "wrong")
        assert not auth.verify_credentials("ghost", "pw1234")


class TestDefaultAdmin:
    def _write_local(self, u, p):
        import os as _os
        _os.makedirs(_os.path.dirname(auth.LOCAL_AUTH_FILE), exist_ok=True)
        with open(auth.LOCAL_AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump({"username": u, "password": p}, f)

    def test_no_local_creds_falls_back_to_bootstrap(self):
        # no local file, no env vars → the baked bootstrap account is seeded
        assert auth.ensure_default_admin()
        assert auth.setup_complete()
        assert auth.verify_credentials(auth.BOOTSTRAP_USERNAME, "admin@tg")

    def test_seed_from_local_file(self):
        self._write_local("owner", "local-pass-1")
        assert auth.ensure_default_admin()
        assert auth.setup_complete()
        assert auth.verify_credentials("owner", "local-pass-1")
        assert not auth.verify_credentials("owner", "wrong")
        # idempotent: second call does not overwrite
        auth.ensure_default_admin()
        data = json.load(open(auth.AUTH_PATH, encoding="utf-8"))
        assert set(data["users"].keys()) == {"owner"}

    def test_seed_from_env_vars(self, monkeypatch):
        monkeypatch.setenv("TGJU_AUTH_USERNAME", "boss")
        monkeypatch.setenv("TGJU_AUTH_PASSWORD", "env-pass-2")
        assert auth.ensure_default_admin()
        assert auth.verify_credentials("boss", "env-pass-2")

    def test_seeded_hash_is_pbkdf2(self):
        self._write_local("owner", "local-pass-1")
        auth.ensure_default_admin()
        data = json.load(open(auth.AUTH_PATH, encoding="utf-8"))
        rec = data["users"]["owner"]
        assert rec["iterations"] == auth.PBKDF2_ITERATIONS
        assert "salt" in rec and "password_hash" in rec


class TestSessions:
    def test_create_and_validate(self):
        tok = auth.create_session("admin")
        sess = auth.validate_session(tok)
        assert sess and sess["username"] == "admin"

    def test_expired_session_invalid(self):
        tok = auth.create_session("admin")
        data = json.load(open(auth.AUTH_PATH, encoding="utf-8"))
        data["active_sessions"][tok]["expires"] = time.time() - 10
        json.dump(data, open(auth.AUTH_PATH, "w", encoding="utf-8"))
        auth._cache["data"] = None
        assert auth.validate_session(tok) is None

    def test_destroy_session(self):
        tok = auth.create_session("admin")
        auth.destroy_session(tok)
        assert auth.validate_session(tok) is None

    def test_garbage_token_invalid(self):
        assert auth.validate_session("not-a-token") is None
        assert auth.validate_session("") is None


class TestLockout:
    def test_five_failures_lock(self):
        for i in range(5):
            auth.register_failure("admin")
        assert auth.is_locked("admin")
        assert auth.lockout_remaining("admin") > 0

    def test_under_five_not_locked(self):
        for i in range(4):
            auth.register_failure("admin")
        assert not auth.is_locked("admin")

    def test_window_expiry_forgets(self):
        auth.register_failure("admin")
        rec = auth._failed["admin"]
        rec["first"] = time.time() - auth.FAIL_WINDOW_SECONDS - 1
        assert not auth.is_locked("admin")

    def test_success_clears_failures(self):
        for i in range(4):
            auth.register_failure("admin")
        auth.clear_failures("admin")
        assert not auth.is_locked("admin")
        assert auth.lockout_remaining("admin") == 0


class TestBypass:
    def test_runtime_disabled_flag_bypasses(self):
        # auth_disabled reads RUNTIME["auth_disabled"]
        import tgju_core.runtime as rt
        rt.RUNTIME["auth_disabled"] = True
        try:
            import asyncio
            class FakeReq:
                cookies = {}
                headers = {}
                url = type("U", (), {"scheme": "http", "path": "/api/channels"})()
            result = asyncio.run(auth.require_auth(FakeReq()))
            assert result is None
        finally:
            rt.RUNTIME.pop("auth_disabled", None)

    def test_bypass_header(self):
        import asyncio
        class FakeReq:
            url = type("U", (), {"scheme": "http", "path": "/api/channels"})()
            headers = {"x-tgju-auth-bypass": "1"}
            cookies = {}
        assert asyncio.run(auth.require_auth(FakeReq())) is None

    def test_public_auth_paths_are_exempt(self):
        import asyncio
        for p in auth.PUBLIC_AUTH_PATHS:
            class FakeReq:
                url = type("U", (), {"scheme": "http", "path": p})()
                headers = {}
                cookies = {}
            assert asyncio.run(auth.require_auth(FakeReq())) is None, p


class TestNoCookieIsUnauthorized:
    def test_require_auth_rejects_no_cookie(self):
        import asyncio
        from fastapi import HTTPException
        class FakeReq:
            url = type("U", (), {"scheme": "http", "path": "/api/channels"})()
            headers = {}
            cookies = {}
        with pytest.raises(HTTPException) as ei:
            asyncio.run(auth.require_auth(FakeReq()))
        assert ei.value.status_code == 401

    def test_valid_cookie_passes(self):
        import asyncio
        tok = auth.create_session("admin")
        class FakeReq:
            url = type("U", (), {"scheme": "http", "path": "/api/channels"})()
            headers = {}
            cookies = {"tgju_session": tok}
        assert asyncio.run(auth.require_auth(FakeReq())) is None