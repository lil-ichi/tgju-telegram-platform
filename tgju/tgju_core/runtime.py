# -*- coding: utf-8 -*-
"""tgju_core/runtime.py — Shared runtime state for the platform webapp.

The legacy monolith (tgju_platform.py) kept one process-global RUNTIME dict
plus per-request caches.  These live here now so the extracted modules
(routes, scheduler, status, sender, settings) all read/write the SAME
objects — exactly as they did when they shared one file.

Externally-visible names kept for backward compatibility:
  RUNTIME  — process-global state dict (channels, last_rows, last_fetch,
             refreshing, last_preview, scheduler, degraded, ...)
  CONN_PROBE_CACHE — 60s Bot API probe cache used by /api/connections
  UI_PAGE  — cached HTML of the dashboard (loaded lazily by the root route)
"""
import threading

# persistent runtime state
RUNTIME = {"channels": None, "last_rows": {}, "last_fetch": None,
           "last_fetch_duration": None, "refreshing": False,
           "last_preview": {}, "scheduler": None, "scheduler_running": False,
           "refresh_lock": threading.Lock()}

# 60s cache for the live Bot API connection probes
CONN_PROBE_CACHE = {"entry": 0.0, "data": None}

# Lazy-cached dashboard HTML (loaded on first GET /)
UI_PAGE = None
