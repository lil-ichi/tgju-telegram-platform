# -*- coding: utf-8 -*-
"""
tgju_core_sources.py — Data Layer: unified Source abstraction.
Wraps the existing scraper/news engines behind a common Source interface
so the decision layer (orchestrator) can consume normalized snapshots
regardless of which upstream the data comes from.

Sources:
  TgjuPricesSource   — wraps tgju_engine_scrape.get_all_prices()
  TgjuNewsSource     — wraps tgju_engine_news.channel_articles()
  TgjuProfileSource  — wraps tgju_engine_scrape.fetch_profile_price()
"""
from __future__ import annotations
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from tgju_core.types import Snapshot, generate_snapshot_id
from tgju_core.events import emit_event, EventType

from tgju_engine_scrape import get_all_prices, fetch_profile_price
from tgju_engine_news import channel_articles


class BaseSource:
    """Common Source contract: fetch() -> normalized dict."""

    name: str = "base"

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._cache: Optional[Dict[str, Any]] = None
        self._cached_at: Optional[datetime] = None

    def fetch(self, force: bool = False) -> Dict[str, Any]:
        """Fetch (or serve cache) normalized data. Override in subclass."""
        raise NotImplementedError

    def _get_cached(self) -> Optional[Dict[str, Any]]:
        if self._cache and self._cached_at:
            if datetime.now() - self._cached_at < timedelta(seconds=self.ttl):
                return self._cache
        return None

    def _store(self, data: Dict[str, Any]) -> Dict[str, Any]:
        self._cache = data
        self._cached_at = datetime.now()
        return data

    def to_snapshot(self) -> Snapshot:
        """Wrap latest data into a core Snapshot (immutable)."""
        data = self.fetch()
        return Snapshot(
            id=generate_snapshot_id(),
            created_at=datetime.now(),
            source=self.name,
            raw_data=data,
            normalized_data=data,
        )


class TgjuPricesSource(BaseSource):
    """Live TGJU market prices."""

    name = "tgju_prices"

    def fetch(self, force: bool = False) -> Dict[str, Any]:
        cached = self._get_cached() if not force else None
        if cached is not None:
            return cached
        t0 = time.time()
        try:
            rows = get_all_prices()
            dur_ms = int((time.time() - t0) * 1000)
            emit_event(EventType.SOURCE_FETCH_COMPLETED, run_id="system",
                       channel_id="*", status="success", duration_ms=dur_ms,
                       payload={"source": self.name, "rows": len(rows)})
            return self._store({"prices": rows})
        except Exception as e:
            emit_event(EventType.SOURCE_FETCH_FAILED, run_id="system",
                       channel_id="*", status="failed", error=str(e)[:300])
            # Serve stale cache if available
            if self._cache is not None:
                return self._cache
            raise


class TgjuNewsSource(BaseSource):
    """TGJU news articles by category."""

    name = "tgju_news"

    def __init__(self, categories: Optional[List[str]] = None, ttl_seconds: int = 600):
        super().__init__(ttl_seconds)
        self.categories = categories or []

    def fetch(self, force: bool = False) -> Dict[str, Any]:
        cached = self._get_cached() if not force else None
        if cached is not None:
            return cached
        t0 = time.time()
        try:
            articles = channel_articles(self.categories, [], limit=6)
            dur_ms = int((time.time() - t0) * 1000)
            emit_event(EventType.SOURCE_FETCH_COMPLETED, run_id="system",
                       channel_id="*", status="success", duration_ms=dur_ms,
                       payload={"source": self.name, "articles": len(articles)})
            return self._store({"articles": articles})
        except Exception as e:
            emit_event(EventType.SOURCE_FETCH_FAILED, run_id="system",
                       channel_id="*", status="failed", error=str(e)[:300])
            if self._cache is not None:
                return self._cache
            raise


class TgjuProfileSource(BaseSource):
    """TGJU single-instrument profile backfill."""

    name = "tgju_profile"

    def fetch(self, force: bool = False) -> Dict[str, Any]:
        """Profile backfill happens ad-hoc per slug; not cached globally."""
        raise NotImplementedError("Use fetch_profile() with a slug")

    def fetch_profile(self, slug: str, name: str = "") -> Dict[str, Any]:
        t0 = time.time()
        try:
            row = fetch_profile_price(slug, name) or {}
            emit_event(EventType.SOURCE_FETCH_COMPLETED, run_id="system",
                       channel_id="*", status="success",
                       duration_ms=int((time.time() - t0) * 1000),
                       payload={"source": self.name, "slug": slug, "found": bool(row)})
            return row
        except Exception as e:
            emit_event(EventType.SOURCE_FETCH_FAILED, run_id="system",
                       channel_id="*", status="failed", error=str(e)[:300])
            return {}


# Shared instances (process-wide, with sensible TTLs)
prices_source = TgjuPricesSource(ttl_seconds=240)
news_source = TgjuNewsSource(ttl_seconds=600)
profile_source = TgjuProfileSource()


def get_prices_snapshot(force: bool = False) -> Snapshot:
    """Get a fresh (or cached) prices snapshot through the data layer."""
    return prices_source.to_snapshot()


def get_news_articles(categories: List[str], limit: int = 6) -> List[Dict[str, Any]]:
    """Get news articles through the data layer."""
    src = TgjuNewsSource(categories=categories)
    data = src.fetch()
    return (data.get("articles") or [])[:limit]


def get_profile_row(slug: str, name: str = "") -> Dict[str, Any]:
    """Backfill a single instrument via the profile source."""
    return profile_source.fetch_profile(slug, name)