#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TGJU Platform launcher — one command to run, watch, stop and debug the app.

Deliberately **stdlib only** (no fastapi/uvicorn/yaml imports at module level),
so a fresh checkout can be started with nothing but a Python 3.11+ interpreter:

    python tgju/tgju_start.py                 # start (foreground) + health check
    python tgju/tgju_start.py start --daemon  # start in background
    python tgju/tgju_start.py status          # running? pid, url, health, ports
    python tgju/tgju_start.py logs -f         # tail state/server.log
    python tgju/tgju_start.py stop            # graceful stop (SIGTERM → SIGKILL)
    python tgju/tgju_start.py restart
    python tgju/tgju_start.py doctor          # environment / config self-check
    python tgju/tgju_start.py setup           # create .venv + install deps only

It bootstraps the environment for you: missing ``.venv`` → created, missing
fastapi/uvicorn/PyYAML → ``pip install -r requirements.txt``. Then it launches
uvicorn against ``tgju_platform:app`` with the app directory on ``PYTHONPATH``.

Environment: ``TGJU_PORT`` (default 8791), ``TGJU_HOST`` (default 0.0.0.0 for
the server, 127.0.0.1 for probes), ``TGJU_PYTHON`` (interpreter override).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ── locations (module-level so tests can redirect them) ─────────────────────
APP_DIR = Path(__file__).resolve().parent            # .../tgju
ROOT = APP_DIR.parent                                # app root (owns .venv)
STATE_DIR = APP_DIR / "state"
PID_FILE = STATE_DIR / "platform.pid"
SERVER_LOG = STATE_DIR / "server.log"                # uvicorn stdout/stderr
APP_LOG = STATE_DIR / "platform.log"                 # app's own log_line output
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
CHANNELS_YAML = APP_DIR / "channels.yaml"
AUTH_FILE = STATE_DIR / "auth.json"
AUTH_LOCAL_FILE = STATE_DIR / "auth.local.json"
BOT_PROFILE_FILE = STATE_DIR / "bot_profile.json"

DEFAULT_PORT = int(os.environ.get("TGJU_PORT") or 8791)
DEFAULT_HOST = os.environ.get("TGJU_HOST") or "0.0.0.0"
PROBE_HOST = "127.0.0.1"
HEALTH_PATH = "/healthz"
IS_WIN = os.name == "nt"
MIN_PYTHON = (3, 11)

COMMANDS = ("start", "stop", "restart", "status", "logs", "doctor", "setup",
            "open", "version", "help")
ALIASES = {
    "up": "start", "run": "start", "serve": "start", "dev": "start",
    "down": "stop", "halt": "stop", "kill": "stop",
    "ps": "status", "info": "status", "st": "status",
    "log": "logs", "tail": "logs",
    "check": "doctor", "diag": "doctor", "verify": "doctor",
    "install": "setup", "bootstrap": "setup",
    "browse": "open", "url": "open",
    "--version": "version", "-v": "version", "-h": "help", "--help": "help",
}


# ── tiny output helpers (colors only on a real terminal) ────────────────────
# Make the console UTF-8 safe (Windows cmd defaults to a legacy code page) and
# fall back to ASCII glyphs when the terminal cannot encode the pretty ones.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _glyph(uni: str, ascii_fallback: str) -> str:
    try:
        uni.encode(sys.stdout.encoding or "utf-8")
        return uni
    except Exception:
        return ascii_fallback


OK_ICON = _glyph("✓", "+")
BAD_ICON = _glyph("✗", "x")
WARN_ICON = _glyph("!", "!")
ARROW = _glyph("▸", "->")
DOT = _glyph("●", "*")
RING = _glyph("○", "o")
STOP_ICON = _glyph("⏹", "--")


class C:
    _on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    BOLD = "\033[1m" if _on else ""
    DIM = "\033[2m" if _on else ""
    GREEN = "\033[32m" if _on else ""
    YELLOW = "\033[33m" if _on else ""
    RED = "\033[31m" if _on else ""
    CYAN = "\033[36m" if _on else ""
    OFF = "\033[0m" if _on else ""


def say(msg: str = ""):
    print(msg, flush=True)


def step(msg: str):
    say("%s%s%s %s" % (C.CYAN, ARROW, C.OFF, msg))


def ok(msg: str):
    say("  %s%s%s %s" % (C.GREEN, OK_ICON, C.OFF, msg))


def warn(msg: str):
    say("  %s%s%s %s" % (C.YELLOW, WARN_ICON, C.OFF, msg))


def fail(msg: str):
    say("  %s%s%s %s" % (C.RED, BAD_ICON, C.OFF, msg))


# ── interpreter / environment bootstrap ─────────────────────────────────────
def venv_python() -> Path | None:
    """The interpreter inside the app-local virtualenv, when it exists."""
    candidates = [VENV_DIR / "Scripts" / "python.exe", VENV_DIR / "bin" / "python",
                  VENV_DIR / "bin" / "python3"]
    for p in candidates:
        if p.exists():
            return p
    return None


def has_deps(python: str | os.PathLike) -> bool:
    """True when fastapi + uvicorn + yaml import in that interpreter."""
    try:
        r = subprocess.run([str(python), "-c",
                            "import fastapi, uvicorn, yaml"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def system_python() -> str | None:
    """A usable base interpreter (explicit override → venv → PATH)."""
    override = os.environ.get("TGJU_PYTHON")
    if override and Path(override).exists():
        return override
    vp = venv_python()
    if vp:
        return str(vp)
    for name in (sys.executable, "python3", "python", "py"):
        if not name:
            continue
        if name == "py":
            if shutil.which("py"):
                return "py -3"
            continue
        if Path(name).exists() or shutil.which(name):
            return name
    return None


def _run(cmd: list, **kw) -> int:
    try:
        return subprocess.run(cmd, **kw).returncode
    except FileNotFoundError:
        return 127


def bootstrap(install: bool = True, quiet: bool = False) -> Path:
    """Make sure a virtualenv with the requirements exists; return its python.

    Mirrors the launcher scripts (create ``.venv`` → install requirements) but
    also reuses an already-working interpreter when one is on PATH, so users
    with fastapi installed globally are not forced into a venv.
    """
    vp = venv_python()
    if vp and has_deps(vp):
        return vp

    base = os.environ.get("TGJU_PYTHON") or None
    if not base and not vp:
        # prefer a PATH interpreter that already has the deps — zero setup
        for name in ("python3", "python"):
            exe = shutil.which(name)
            if exe and has_deps(exe):
                if not quiet:
                    ok("using %s (dependencies already present)" % exe)
                return Path(exe)

    if not vp:
        base = base or shutil.which("python3") or shutil.which("python") or "py -3"
        if not quiet:
            step("Creating virtual environment (%s -m venv .venv)" % base)
        cmd = base.split() + ["-m", "venv", str(VENV_DIR)]
        if _run(cmd) != 0:
            raise SystemExit(
                "%s%s%s could not create .venv.\n"
                "    Install Python %d.%d+ (with the venv module) or set TGJU_PYTHON."
                % (C.RED, BAD_ICON, C.OFF, MIN_PYTHON[0], MIN_PYTHON[1]))
        vp = venv_python()
        if not vp:
            raise SystemExit("%s%s%s virtualenv created but no interpreter found"
                             % (C.RED, BAD_ICON, C.OFF))

    if has_deps(vp):
        return vp
    if not install:
        raise SystemExit("%s%s%s dependencies missing in %s (run: %s setup)"
                         % (C.RED, BAD_ICON, C.OFF, vp, launcher_rel()))
    if not quiet:
        step("Installing dependencies (pip install -r requirements.txt)")
    rc = _run([str(vp), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)])
    if rc != 0 or not has_deps(vp):
        raise SystemExit("%s%s%s dependency install failed — run manually:\n"
                         "    %s -m pip install -r %s"
                         % (C.RED, BAD_ICON, C.OFF, vp, REQUIREMENTS))
    return vp


# ── process / port helpers ──────────────────────────────────────────────────
def http_get(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tgju-launcher"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:          # reachable, just not 200
        return True, "HTTP %s" % e.code
    except Exception:
        return False, ""


def probe(port: int, timeout: float = 2.0, strict: bool = True) -> dict | None:
    """Return the /healthz payload when THIS platform answers on `port`.

    strict=True also requires `service == "tgju-platform"`, so a foreign
    service that happens to expose /healthz is never mistaken for the app.
    """
    got, body = http_get("http://%s:%d%s" % (PROBE_HOST, port, HEALTH_PATH), timeout)
    if not got or not body.strip().startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not data.get("ok"):
        return None
    if strict and data.get("service") != "tgju-platform":
        return None
    return data


def wait_healthy(port: int, timeout: float = 60.0) -> dict | None:
    """Poll /healthz until the server answers or `timeout` seconds elapse."""
    deadline = time.time() + max(1.0, timeout)
    while time.time() < deadline:
        hit = probe(port, timeout=1.5)
        if hit:
            return hit
        time.sleep(0.4)
    return None


def port_busy(port: int) -> bool:
    """True when something already listens on the loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((PROBE_HOST, port)) == 0


def pid_alive(pid: int) -> bool:
    """Cross-platform liveness check (never os.kill on Windows — it kills)."""
    if not pid or pid <= 0:
        return False
    if IS_WIN:
        try:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 capture_output=True, text=True, timeout=20).stdout
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, OverflowError, ValueError):
        return False


def read_pid() -> dict:
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_pid(pid: int, port: int, host: str, daemon: bool):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "port": port, "host": host, "daemon": daemon,
                       "started_at": datetime.now().isoformat(timespec="seconds"),
                       "python": sys.executable}, f, ensure_ascii=False)
    except Exception:
        pass


def clear_pid():
    try:
        PID_FILE.unlink()
    except Exception:
        pass


def listener_pid(port: int) -> int | None:
    """Best-effort: which pid listens on `port` (used when no pid file exists)."""
    try:
        if IS_WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, timeout=25).stdout
            for line in out.splitlines():
                if ":%d " % port in line and "LISTENING" in line.upper():
                    tail = line.split()
                    if tail and tail[-1].isdigit():
                        return int(tail[-1])
        else:
            for cmd in (["lsof", "-ti", "tcp:%d" % port, "-s", "TCP:LISTEN"],
                        ["fuser", "-n", "tcp", str(port)]):
                if not shutil.which(cmd[0]):
                    continue
                out = subprocess.run(cmd, capture_output=True, text=True,
                                     timeout=20).stdout
                nums = re.findall(r"\d+", out)
                if nums:
                    return int(nums[0])
    except Exception:
        pass
    return None


def stop_pid(pid: int, grace: float = 8.0) -> bool:
    """Terminate a process politely, then forcibly. Returns True when gone."""
    if not pid_alive(pid):
        return True
    if IS_WIN:
        _run(["taskkill", "/PID", str(pid), "/T", "/F"],
             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, 15)                      # SIGTERM
        except OSError:
            return True
        deadline = time.time() + grace
        while time.time() < deadline and pid_alive(pid):
            time.sleep(0.25)
        if pid_alive(pid):
            try:
                os.kill(pid, 9)                   # SIGKILL
            except OSError:
                pass
    deadline = time.time() + grace
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.25)
    return not pid_alive(pid)


# ── server command ──────────────────────────────────────────────────────────
def server_cmd(python, host: str, port: int, reload_: bool = False,
               log_level: str = "info") -> list:
    cmd = [str(python), "-m", "uvicorn", "tgju_platform:app",
           "--host", host, "--port", str(port), "--log-level", log_level]
    if reload_:
        cmd.append("--reload")
    return cmd


def server_env(host: str, port: int) -> dict:
    env = os.environ.copy()
    env["TGJU_PORT"] = str(port)
    env["TGJU_HOST"] = host
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    existing = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = str(APP_DIR) + (os.pathsep + existing if existing else "")
    return env


def url_for(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return "http://%s:%d" % (shown, port)


def banner(port: int, host: str, extra: str = ""):
    say()
    say("%s%s%s%s TGJU Platform%s — control center is up"
        % (C.BOLD, C.CYAN, _glyph("🛰", "*"), C.OFF, C.OFF))
    say("  %sDashboard%s  %s" % (C.BOLD, C.OFF, url_for(host, port)))
    say("  %sHealth%s     %s%s" % (C.BOLD, C.OFF, url_for(host, port), HEALTH_PATH))
    if extra:
        say("  %s%s%s" % (C.DIM, extra, C.OFF))
    say("  %s%s stop  ·  restart  ·  status  ·  logs -f  ·  doctor%s"
        % (C.DIM, launcher_rel(), C.OFF))


# ── commands ────────────────────────────────────────────────────────────────
def cmd_setup(args) -> int:
    step("Preparing the TGJU Platform environment")
    py = bootstrap(install=not args.no_install)
    ok("interpreter %s" % py)
    info = python_version(py)
    if info:
        ok("python %s" % info)
    ok("fastapi / uvicorn / PyYAML present")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ok("state dir %s" % STATE_DIR)
    say()
    say("Ready — start it with: %s%s%s" % (C.BOLD, command_hint(), C.OFF))
    return 0


def cmd_start(args) -> int:
    port = args.port or DEFAULT_PORT
    host = args.host or DEFAULT_HOST

    live = probe(port)
    if live:
        say("%s%s already running%s on %s (%s)"
            % (C.GREEN, DOT, C.OFF, url_for(host, port), json.dumps(live)))
        if args.open_browser:
            open_browser(host, port)
        return 0

    if port_busy(port):
        other = listener_pid(port)
        if not args.force:
            fail("port %d is already in use%s" % (port, " (pid %s)" % other if other else ""))
            say("    Free it, pick another port (--port 8792) or use --force to stop it.")
            return 1
        warn("port %d busy — stopping pid %s (--force)" % (port, other))
        if other:
            stop_pid(other)
        if port_busy(port):
            fail("port %d still busy after stop" % port)
            return 1

    stale = read_pid()
    if stale and not pid_alive(int(stale.get("pid") or 0)):
        warn("removing stale pid file (pid %s not running)" % stale.get("pid"))
        clear_pid()

    py = bootstrap(install=not args.no_install)
    cmd = server_cmd(py, host, port, reload_=args.reload, log_level=args.log_level)
    env = server_env(host, port)
    daemon = bool(args.daemon)

    say()
    step("Starting TGJU Platform (%s)" % ("background" if daemon else "foreground"))
    if daemon:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log = open(SERVER_LOG, "ab")
        kwargs = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL}
        if IS_WIN:
            kwargs["creationflags"] = 0x00000008 | 0x00000200   # DETACHED | NEW_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, cwd=str(APP_DIR), env=env, **kwargs)
    else:
        try:
            proc = subprocess.Popen(cmd, cwd=str(APP_DIR), env=env)
        except KeyboardInterrupt:
            say("\nCancelled.")
            return 130

    write_pid(proc.pid, port, host, daemon)
    hit = wait_healthy(port, timeout=args.timeout)
    if not hit:
        failed = proc.poll() is not None
        fail("server did not answer %s within %.0fs%s"
             % (HEALTH_PATH, args.timeout,
                " (the process already exited)" if failed else ""))
        if daemon:
            say("    log: %s" % SERVER_LOG)
            say("    or run it in the foreground to see the error: %s"
                % foreground_hint(port))
        else:
            say("    see the output above for the reason")
        clear_pid()
        if proc.poll() is None:
            stop_pid(proc.pid)
        return 1

    banner(port, host, "pid %d · log %s" % (proc.pid, SERVER_LOG.name if daemon else APP_LOG.name))
    auth_hint()
    if args.open_browser:
        open_browser(host, port)

    if daemon:
        ok("running in background (pid %d)" % proc.pid)
        say("  %slogs:%s %s logs -f        %sstop:%s %s stop"
            % (C.BOLD, C.OFF, launcher_rel(), C.BOLD, C.OFF, launcher_rel()))
        return 0

    say("  %sCtrl+C stops the server%s" % (C.DIM, C.OFF))
    say()
    try:
        return proc.wait()
    except KeyboardInterrupt:
        say("\n%s%s  stopping…%s" % (C.YELLOW, STOP_ICON, C.OFF))
        stop_pid(proc.pid)
        clear_pid()
        return 0


def cmd_stop(args) -> int:
    port = args.port or DEFAULT_PORT
    info = read_pid()
    pid = int(info.get("pid") or 0)
    port = int(info.get("port") or port)
    if not pid or not pid_alive(pid):
        if info:
            warn("pid file was stale — removing it")
            clear_pid()
        other = listener_pid(port)
        if other and probe(port):
            warn("no pid file, but pid %s answers on port %d — stopping it" % (other, port))
            stop_pid(other)
            ok("stopped pid %s" % other)
            return 0
        if port_busy(port):
            say("Platform is not running.")
            warn("port %d is used by pid %s, which is not the TGJU platform — left alone"
                 % (port, other or "?"))
            say("    start on another port (--port 8792) or stop that process yourself.")
            return 1
        say("Platform is not running (port %d free)." % port)
        return 0
    step("Stopping pid %d (port %d)" % (pid, port))
    if stop_pid(pid):
        clear_pid()
        ok("stopped")
        return 0
    fail("could not stop pid %d — kill it manually" % pid)
    return 1


def cmd_restart(args) -> int:
    cmd_stop(args)
    time.sleep(0.6)
    return cmd_start(args)


def cmd_status(args) -> int:
    info = read_pid()
    port = int(info.get("port") or (args.port or DEFAULT_PORT))
    pid = int(info.get("pid") or 0)
    alive = pid_alive(pid)
    hit = probe(port)
    host = info.get("host") or DEFAULT_HOST

    say()
    say("%sTGJU Platform status%s" % (C.BOLD, C.OFF))
    if hit and alive:
        say("  state        %s%s running%s (pid %d)" % (C.GREEN, DOT, C.OFF, pid))
    elif hit:
        say("  state        %s%s running%s (no pid file — started outside the launcher)"
            % (C.YELLOW, DOT, C.OFF))
    elif alive:
        say("  state        %s%s process alive but %s not answering%s"
            % (C.YELLOW, WARN_ICON, HEALTH_PATH, C.OFF))
    else:
        say("  state        %s%s stopped%s" % (C.DIM, RING, C.OFF))
    say("  dashboard    %s" % url_for(host, port))
    say("  port         %d %s" % (port, "(busy)" if port_busy(port) else "(free)"))
    if info.get("started_at"):
        say("  started      %s" % info["started_at"])
    if info.get("python"):
        say("  server py    %s" % info["python"])
    if hit:
        say("  /healthz     %s" % json.dumps(hit, ensure_ascii=False))
    vp = venv_python()
    say("  interpreter  %s" % (vp or "(none — run: %s setup)" % launcher_rel()))
    for label, path in (("app log", APP_LOG), ("server log", SERVER_LOG),
                        ("state dir", STATE_DIR), ("pid file", PID_FILE)):
        if path.exists():
            if path.is_dir():
                size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                say("  %-12s %s (%d files, %.1f KB)"
                    % (label, path, len(list(path.iterdir())), size / 1024.0))
            else:
                say("  %-12s %s (%.1f KB)" % (label, path, path.stat().st_size / 1024.0))
        else:
            say("  %-12s %s %s(missing)%s" % (label, path, C.DIM, C.OFF))
    say()
    return 0


def cmd_logs(args) -> int:
    path = Path(args.file) if args.file else (SERVER_LOG if args.server else APP_LOG)
    if not path.exists():
        alt = APP_LOG if path == SERVER_LOG else SERVER_LOG
        if alt.exists():
            warn("%s missing — showing %s" % (path.name, alt))
            path = alt
        else:
            fail("no log yet (%s / %s)" % (SERVER_LOG.name, APP_LOG.name))
            return 1
    lines = tail_lines(path, args.lines)
    say("%s── %s (last %d lines) ──%s" % (C.DIM, path, len(lines), C.OFF))
    for ln in lines:
        say(ln.rstrip("\n"))
    if not args.follow:
        return 0
    say("%s── following (Ctrl+C to stop) ──%s" % (C.DIM, C.OFF))
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            while True:
                chunk = f.readline()
                if chunk:
                    say(chunk.rstrip("\n"))
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        say("")
        return 0
    except Exception as e:
        fail("tail failed: %s" % e)
        return 1


def cmd_open(args) -> int:
    port = args.port or int((read_pid().get("port") or DEFAULT_PORT))
    host = read_pid().get("host") or DEFAULT_HOST
    if not probe(port):
        warn("nothing answers on %s yet — starting is up to you" % url_for(host, port))
    open_browser(host, port)
    ok("opened %s" % url_for(host, port))
    return 0


def cmd_doctor(args) -> int:
    port = args.port or int((read_pid().get("port") or DEFAULT_PORT))
    say()
    say("%sEnvironment self-check%s" % (C.BOLD, C.OFF))
    results = doctor_checks(port=port, offline=args.offline)
    bad = 0
    for level, label, hint in results:
        if level == "ok":
            ok(label)
        elif level == "warn":
            warn(label)
        else:
            bad += 1
            fail(label)
        if hint:
            say("      %s%s%s" % (C.DIM, hint, C.OFF))
    say()
    if bad:
        fail("%d problem(s) found" % bad)
        return 1
    ok("everything the platform needs is in place")
    return 0


def cmd_version(args) -> int:
    say("TGJU Platform launcher")
    say("  root         %s" % ROOT)
    say("  app version  %s" % app_version())
    say("  python       %s" % sys.version.split()[0])
    vp = venv_python()
    if vp:
        say("  venv python  %s (%s)" % (vp, python_version(vp) or "?"))
    return 0


def cmd_help(args) -> int:
    say("%sTGJU Platform launcher%s — one command for the whole lifecycle." % (C.BOLD, C.OFF))
    say("Runs on any Python %d.%d+: it creates .venv, installs the requirements,"
        % MIN_PYTHON)
    say("waits for /healthz and tells you the dashboard URL.")
    show_commands()
    return 0


# ── shared helpers used by the commands ─────────────────────────────────────
def python_version(python) -> str | None:
    try:
        out = subprocess.run([str(python), "-c",
                              "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return out or None
    except Exception:
        return None


def app_version() -> str:
    try:
        txt = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "unknown"


def launcher_rel() -> str:
    """How the user should type the launcher from the app root."""
    rel = APP_DIR.name + "/" + Path(__file__).name
    return "%s %s" % (python_word(), rel)


def python_word() -> str:
    return "py" if IS_WIN and shutil.which("py") else "python"


def command_hint() -> str:
    return launcher_rel()


def foreground_hint(port: int) -> str:
    return "%s %s" % (launcher_rel(), "--port %d" % port if port != DEFAULT_PORT else "start")


def tail_lines(path: Path, count: int) -> list:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()[-max(1, count):]
    except Exception:
        return []


def open_browser(host: str, port: int):
    try:
        import webbrowser
        webbrowser.open(url_for(host, port))
    except Exception:
        pass


def auth_hint():
    """Tell the user what to do when no login account is provisioned yet."""
    try:
        have_users = AUTH_FILE.exists() and bool(
            (json.loads(AUTH_FILE.read_text(encoding="utf-8")) or {}).get("users"))
    except Exception:
        have_users = False
    if have_users:
        return
    say()
    say("  %s!%s no login account yet — create %s"
        % (C.YELLOW, C.OFF, AUTH_LOCAL_FILE))
    say('    %s{"username": "admin", "password": "choose-a-strong-one"}%s'
        % (C.DIM, C.OFF))
    say("    or set TGJU_AUTH_USERNAME / TGJU_AUTH_PASSWORD before starting.")


def doctor_checks(port: int = DEFAULT_PORT, offline: bool = False) -> list:
    """Return [(level, label, hint)] — level ∈ ok | warn | fail."""
    out = []
    vi = sys.version_info
    if vi >= MIN_PYTHON:
        out.append(("ok", "python %d.%d.%d (needs ≥ %d.%d)"
                    % (vi[0], vi[1], vi[2], MIN_PYTHON[0], MIN_PYTHON[1]), ""))
    else:
        out.append(("fail", "python %d.%d is too old (needs ≥ %d.%d)"
                    % (vi[0], vi[1], MIN_PYTHON[0], MIN_PYTHON[1]),
                    "install a newer Python or set TGJU_PYTHON"))

    vp = venv_python()
    if vp and has_deps(vp):
        out.append(("ok", "virtualenv ready (%s)" % python_version(vp), ""))
    elif vp:
        out.append(("fail", "virtualenv exists but fastapi/uvicorn/PyYAML are missing",
                    "run: %s setup" % launcher_rel()))
    else:
        py = shutil.which("python3") or shutil.which("python")
        if py and has_deps(py):
            out.append(("warn", "no .venv — using system python %s" % py,
                        "run: %s setup  (recommended, keeps deps local)" % launcher_rel()))
        else:
            out.append(("fail", "no usable interpreter with the requirements",
                        "run: %s setup" % launcher_rel()))

    if CHANNELS_YAML.exists():
        try:
            import yaml
            data = yaml.safe_load(CHANNELS_YAML.read_text(encoding="utf-8")) or {}
            chans = data.get("channels") or []
            enabled = [c for c in chans if c.get("enabled")]
            out.append(("ok", "channels.yaml parses — %d channel(s), %d enabled"
                        % (len(chans), len(enabled)), ""))
            missing_tgid = [c.get("id") for c in enabled if not c.get("telegram_id")]
            if missing_tgid:
                out.append(("warn", "enabled channel(s) without telegram_id: %s"
                            % ", ".join(str(x) for x in missing_tgid),
                            "set the channel id in the dashboard or channels.yaml"))
        except Exception as e:
            out.append(("fail", "channels.yaml is not valid YAML: %s" % e,
                        "restore it from git — a bad save breaks every post"))
    else:
        out.append(("fail", "channels.yaml is missing", "expected %s" % CHANNELS_YAML))

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        probe_file = STATE_DIR / ".write-test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        out.append(("ok", "state dir writable (%s)" % STATE_DIR, ""))
    except Exception as e:
        out.append(("fail", "state dir is not writable: %s" % e,
                    "fix permissions on %s" % STATE_DIR))

    if port_busy(port):
        if probe(port):
            out.append(("warn", "port %d already serves this platform (running)" % port,
                        "use the dashboard, or 'stop' then start again"))
        else:
            out.append(("fail", "port %d is taken by another process" % port,
                        "start with --port 8792 or free the port"))
    else:
        out.append(("ok", "port %d is free" % port, ""))

    if offline:
        out.append(("warn", "network checks skipped (--offline)", ""))
    else:
        got, _ = http_get("https://www.tgju.org/", timeout=8)
        if got:
            out.append(("ok", "tgju.org reachable", ""))
        else:
            out.append(("warn", "tgju.org unreachable from here",
                        "posts will use the on-disk fallback cache until it returns"))
        got_tg, _ = http_get("https://api.telegram.org/", timeout=8)
        out.append(("ok" if got_tg else "warn",
                    "api.telegram.org %s" % ("reachable" if got_tg else "unreachable"),
                    "" if got_tg else "Iran-side timeouts are environmental (WinError 10060)"))

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    try:
        prof = json.loads(BOT_PROFILE_FILE.read_text(encoding="utf-8")) or {}
        token = token or any((p.get("token") for p in (prof.get("profiles") or [])))
    except Exception:
        pass
    out.append(("ok" if token else "warn",
                "Telegram bot token %s" % ("configured" if token else "not configured"),
                "" if token else "add it in the dashboard (رباتها) or via .env"))

    try:
        have_users = bool((json.loads(AUTH_FILE.read_text(encoding="utf-8")) or {})
                          .get("users"))
    except Exception:
        have_users = False
    if have_users:
        out.append(("ok", "dashboard login account provisioned", ""))
    else:
        has_local = AUTH_LOCAL_FILE.exists() or bool(os.environ.get("TGJU_AUTH_USERNAME"))
        out.append(("warn" if has_local else "fail",
                    "no dashboard login account yet"
                    + (" (local source present — it seeds on next start)" if has_local else ""),
                    "create %s or set TGJU_AUTH_USERNAME/TGJU_AUTH_PASSWORD" % AUTH_LOCAL_FILE))
    return out


# ── argument parsing (command may appear before or after the flags) ─────────
def parse_args(argv: list) -> tuple[str, argparse.Namespace]:
    argv = list(argv)
    command = None
    rest = []
    for tok in argv:
        low = tok.lower()
        if command is None and not tok.startswith("-"):
            if low in COMMANDS or low in ALIASES:
                command = ALIASES.get(low, low)
                continue
            if tok.isdigit():                     # bare port shorthand: `start 8792`
                rest.append(tok)
                continue
            # unknown bare word: keep it so argparse reports a clear error
            rest.append(tok)
            continue
        if command is None and low in ALIASES:
            command = ALIASES[low]
            continue
        rest.append(tok)
    command = command or "start"

    ap = argparse.ArgumentParser(
        prog="tgju_start",
        description="TGJU Platform launcher (run it with no arguments to start).",
        add_help=True)
    ap.add_argument("port_positional", nargs="?", type=int, default=None,
                    help=argparse.SUPPRESS)
    ap.add_argument("-p", "--port", type=int, default=None,
                    help="server port (default %d / $TGJU_PORT)" % DEFAULT_PORT)
    ap.add_argument("--host", default=None,
                    help="bind address (default %s / $TGJU_HOST)" % DEFAULT_HOST)
    ap.add_argument("-d", "--daemon", "--background", dest="daemon", action="store_true",
                    help="detach and run in the background (log: %s)" % SERVER_LOG.name)
    ap.add_argument("-f", "--follow", action="store_true", help="logs: keep streaming")
    ap.add_argument("-n", "--lines", type=int, default=40, help="logs: how many lines")
    ap.add_argument("--file", default=None, help="logs: read this file instead")
    ap.add_argument("--server", action="store_true",
                    help="logs: show the server stream instead of platform.log")
    ap.add_argument("--reload", action="store_true", help="dev auto-reload")
    ap.add_argument("--log-level", default="info",
                    choices=["critical", "error", "warning", "info", "debug", "trace"])
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="seconds to wait for /healthz (default 60)")
    ap.add_argument("--no-install", action="store_true",
                    help="never touch pip — fail instead of installing")
    ap.add_argument("--force", action="store_true",
                    help="stop whatever occupies the port, then start")
    ap.add_argument("--open", dest="open_browser", action="store_true", default=None,
                    help="open the dashboard in a browser")
    ap.add_argument("--no-open", dest="open_browser", action="store_false",
                    help="never open a browser")
    ap.add_argument("--offline", action="store_true", help="doctor: skip network checks")
    args = ap.parse_args(rest)
    if args.port is None:
        args.port = args.port_positional
    if args.open_browser is None:
        args.open_browser = not args.daemon       # daemon stays quiet by default
    if not args.host:
        args.host = DEFAULT_HOST
    return command, args


def show_commands():
    say()
    say("%sCommands%s" % (C.BOLD, C.OFF))
    rows = [
        ("start", "run the platform (foreground, health-checked) — default"),
        ("start -d", "run detached in the background"),
        ("stop", "graceful stop (pid file → SIGTERM → SIGKILL)"),
        ("restart", "stop then start"),
        ("status", "pid, port, /healthz, logs and paths"),
        ("logs -f", "tail state/platform.log (--server for the uvicorn stream)"),
        ("doctor", "self-check: python, deps, yaml, state, port, network, secrets"),
        ("setup", "create .venv + install requirements only"),
        ("open", "open the dashboard in a browser"),
        ("version", "launcher + app + interpreter versions"),
    ]
    for cmd, desc in rows:
        say("  %s%-12s%s %s" % (C.BOLD, cmd, C.OFF, desc))
    say()
    say("%sFlags%s  -p/--port N · --host H · -d/--daemon · --reload · --force · "
        "--timeout S · --no-install · --open/--no-open" % (C.BOLD, C.OFF))
    say("%sTry%s    %s doctor        (then)   %s status"
        % (C.BOLD, C.OFF, command_hint(), command_hint()))


def main(argv: list | None = None) -> int:
    command, args = parse_args(sys.argv[1:] if argv is None else argv)
    handlers = {
        "start": cmd_start, "stop": cmd_stop, "restart": cmd_restart,
        "status": cmd_status, "logs": cmd_logs, "doctor": cmd_doctor,
        "setup": cmd_setup, "open": cmd_open, "version": cmd_version,
        "help": cmd_help,
    }
    try:
        return handlers[command](args)
    except KeyboardInterrupt:
        say("\nCancelled.")
        return 130
    except SystemExit as e:
        return int(e.code or 0)
    except Exception as e:
        fail("launcher error: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
