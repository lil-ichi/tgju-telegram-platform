# -*- coding: utf-8 -*-
"""Tests for tgju_start — the stdlib-only platform launcher.

The launcher must work before the dependencies exist, so these tests import it
directly (no fastapi/uvicorn needed) and cover the pure logic: argument
parsing, URL/interpreter resolution, the pid file, health probing, listener
lookup wiring and the doctor check list.
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

TGJU_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tgju")
if TGJU_DIR not in sys.path:
    sys.path.insert(0, TGJU_DIR)

import tgju_start as L  # noqa: E402

REAL_CHANNELS = os.path.join(TGJU_DIR, "channels.yaml")


# ── tiny local HTTP server used to test /healthz probing ────────────────────
class _Health(BaseHTTPRequestHandler):
    payload = {"ok": True, "service": "tgju-platform"}

    def do_GET(self):
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def health_server():
    """Start a localhost /healthz server; yields (port, payload_dict)."""
    holder, ready = {}, threading.Event()

    def run():
        srv = HTTPServer(("127.0.0.1", 0), _Health)
        holder["srv"], holder["port"] = srv, srv.server_address[1]
        ready.set()
        srv.serve_forever()

    srv_thread = threading.Thread(target=run, daemon=True)
    srv_thread.start()
    ready.wait(5)
    yield holder["port"], _Health.payload
    holder["srv"].shutdown()
    holder["srv"].server_close()
    srv_thread.join(timeout=5)


class TestArgParsing:
    def test_no_args_defaults_to_start(self):
        cmd, args = L.parse_args([])
        assert cmd == "start"
        assert args.port is None

    def test_command_with_flags(self):
        cmd, args = L.parse_args(["start", "--port", "9000"])
        assert (cmd, args.port) == ("start", 9000)

    def test_flags_before_command(self):
        cmd, args = L.parse_args(["--port", "9001", "start"])
        assert (cmd, args.port) == ("start", 9001)

    @pytest.mark.parametrize("alias,expected", [
        ("up", "start"), ("serve", "start"), ("down", "stop"), ("ps", "status"),
        ("tail", "logs"), ("check", "doctor"), ("install", "setup"),
    ])
    def test_aliases(self, alias, expected):
        cmd, _ = L.parse_args([alias])
        assert cmd == expected

    def test_bare_port_shorthand(self):
        cmd, args = L.parse_args(["start", "8792"])
        assert (cmd, args.port) == ("start", 8792)

    def test_version_flags(self):
        assert L.parse_args(["--version"])[0] == "version"
        assert L.parse_args(["-v"])[0] == "version"

    def test_daemon_defaults_to_no_browser(self):
        _, args = L.parse_args(["start", "--daemon"])
        assert args.daemon is True and args.open_browser is False

    def test_foreground_defaults_to_opening_the_browser(self):
        _, args = L.parse_args(["start"])
        assert args.open_browser is True

    def test_logs_flags(self):
        cmd, args = L.parse_args(["logs", "-n", "5", "-f", "--server"])
        assert cmd == "logs" and args.lines == 5 and args.follow and args.server

    def test_doctor_offline(self):
        cmd, args = L.parse_args(["doctor", "--offline"])
        assert cmd == "doctor" and args.offline is True


class TestResolvedValues:
    def test_url_for_wildcard_host_is_loopback(self):
        assert L.url_for("0.0.0.0", 8791) == "http://127.0.0.1:8791"
        assert L.url_for("127.0.0.1", 9000) == "http://127.0.0.1:9000"

    def test_server_cmd_targets_the_app(self):
        cmd = L.server_cmd("/usr/bin/python", "127.0.0.1", 8792, reload_=True)
        assert cmd[1:4] == ["-m", "uvicorn", "tgju_platform:app"]
        assert "--port" in cmd and "8792" in cmd and "--reload" in cmd

    def test_server_env_points_at_the_app_dir(self):
        env = L.server_env("0.0.0.0", 8793)
        assert env["TGJU_PORT"] == "8793"
        assert str(L.APP_DIR) in env["PYTHONPATH"]
        assert env["PYTHONUNBUFFERED"] == "1"

    def test_app_version_from_pyproject(self):
        assert L.app_version() not in ("", "unknown")

    def test_venv_python_is_found_or_absent(self):
        vp = L.venv_python()
        assert vp is None or vp.exists()


class TestPidFile:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "STATE_DIR", tmp_path)
        monkeypatch.setattr(L, "PID_FILE", tmp_path / "platform.pid")
        assert L.read_pid() == {}
        L.write_pid(4242, 8791, "0.0.0.0", daemon=True)
        info = L.read_pid()
        assert info["pid"] == 4242 and info["port"] == 8791 and info["daemon"] is True
        assert info["started_at"]
        L.clear_pid()
        assert L.read_pid() == {}

    def test_corrupt_pid_file_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "PID_FILE", tmp_path / "platform.pid")
        (tmp_path / "platform.pid").write_text("{not json", encoding="utf-8")
        assert L.read_pid() == {}

    def test_pid_alive(self):
        assert L.pid_alive(os.getpid()) is True
        assert L.pid_alive(0) is False
        assert L.pid_alive(4294967294) is False


class TestPorts:
    def test_free_port_is_not_busy(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        assert L.port_busy(free) is False

    def test_listening_socket_is_busy(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            assert L.port_busy(s.getsockname()[1]) is True


class TestHealthProbe:
    def test_probe_reads_healthz(self, health_server):
        port, payload = health_server
        hit = L.probe(port)
        assert hit and hit["ok"] and hit["service"] == "tgju-platform"

    def test_strict_probe_rejects_foreign_service(self, health_server, monkeypatch):
        port, _ = health_server
        monkeypatch.setattr(_Health, "payload", {"ok": True, "service": "something-else"})
        assert L.probe(port) is None
        assert L.probe(port, strict=False)["ok"] is True

    def test_probe_none_when_nothing_listens(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        assert L.probe(free, timeout=0.5) is None

    def test_wait_healthy_times_out_cleanly(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        assert L.wait_healthy(free, timeout=0.5) is None


class TestLogs:
    def test_tail_lines(self, tmp_path):
        path = tmp_path / "platform.log"
        path.write_text("\n".join("line %d" % i for i in range(10)),
                        encoding="utf-8")
        tail = [ln.rstrip("\n") for ln in L.tail_lines(path, 3)]
        assert tail == ["line 7", "line 8", "line 9"]

    def test_tail_missing_file(self, tmp_path):
        assert L.tail_lines(tmp_path / "nope.log", 5) == []


class TestDoctor:
    def test_offline_checks_return_levels(self, tmp_path, monkeypatch):
        monkeypatch.setattr(L, "STATE_DIR", tmp_path)
        monkeypatch.setattr(L, "CHANNELS_YAML", __import__("pathlib").Path(REAL_CHANNELS))
        monkeypatch.setattr(L, "AUTH_FILE", tmp_path / "auth.json")
        monkeypatch.setattr(L, "AUTH_LOCAL_FILE", tmp_path / "auth.local.json")
        monkeypatch.setattr(L, "BOT_PROFILE_FILE", tmp_path / "bot_profile.json")
        results = L.doctor_checks(port=0, offline=True)
        assert results
        for level, label, hint in results:
            assert level in ("ok", "warn", "fail")
            assert label and isinstance(hint, str)
        assert any("channels.yaml parses" in label for _, label, _ in results)
        assert any("state dir writable" in label for _, label, _ in results)

    def test_broken_yaml_is_reported_as_failure(self, tmp_path, monkeypatch):
        bad = tmp_path / "channels.yaml"
        bad.write_text("channels:\n  - id: 'ch1'   [broken", encoding="utf-8")
        monkeypatch.setattr(L, "STATE_DIR", tmp_path)
        monkeypatch.setattr(L, "CHANNELS_YAML", bad)
        results = L.doctor_checks(port=0, offline=True)
        assert any(level == "fail" and "channels.yaml" in label
                   for level, label, _ in results)


class TestBootstrap:
    @staticmethod
    def _fake_venv(tmp_path):
        """Create the interpreter file venv_python() looks for, no real venv."""
        sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
        exe = tmp_path / "venv" / sub[0] / sub[1]
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("", encoding="utf-8")
        return exe

    def test_venv_with_deps_is_reused(self, tmp_path, monkeypatch):
        exe = self._fake_venv(tmp_path)
        monkeypatch.setattr(L, "VENV_DIR", tmp_path / "venv")
        monkeypatch.setattr(L, "has_deps", lambda py: True)
        assert L.bootstrap(quiet=True) == exe

    def test_missing_deps_without_install_raises(self, tmp_path, monkeypatch):
        self._fake_venv(tmp_path)
        monkeypatch.setattr(L, "VENV_DIR", tmp_path / "venv")
        monkeypatch.setattr(L, "REQUIREMENTS", tmp_path / "requirements.txt")
        monkeypatch.setattr(L, "has_deps", lambda py: False)
        with pytest.raises(SystemExit):
            L.bootstrap(install=False, quiet=True)
