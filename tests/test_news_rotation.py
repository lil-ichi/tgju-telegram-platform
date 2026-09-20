# -*- coding: utf-8 -*-
"""Tests for tgju_engine_news — random, never-repeating footer news.

The footer hyperlink on price posts must be drawn RANDOMLY from the
per-channel pool and must never repeat a previously posted headline
(or the immediately preceding one after a full cycle).
"""
import os
import sys

import pytest

TGJU_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tgju")
if TGJU_DIR not in sys.path:
    sys.path.insert(0, TGJU_DIR)

import tgju_engine_config as cfg  # noqa: E402
import tgju_engine_news as news  # noqa: E402


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    """Redirect the state dir so tests never touch live runtime state."""
    monkeypatch.setattr(cfg, "STATE_DIR", str(tmp_path))
    return tmp_path


def _arts(n: int, prefix: str = "id") -> list:
    return [{"id": "%s%d" % (prefix, i),
             "url": "https://www.tgju.org/news/%s%d/x" % (prefix, i),
             "text": "headline %s %d" % (prefix, i)} for i in range(n)]


class TestPickRotating:
    def test_empty_articles_returns_empty(self, state_dir):
        assert news.pick_rotating("ch1", []) == {}

    def test_never_repeats_until_pool_exhausted(self, state_dir):
        arts = _arts(10)
        picked = [news.pick_rotating("ch1", arts)["id"] for _ in range(10)]
        assert len(set(picked)) == 10, picked

    def test_never_repeats_previous_headline_on_long_run(self, state_dir):
        arts = _arts(6)
        picked = [news.pick_rotating("ch1", arts)["id"] for _ in range(60)]
        for prev, nxt in zip(picked, picked[1:]):
            assert prev != nxt, picked

    def test_full_cycle_keeps_recent_guard_window(self, state_dir):
        arts = _arts(20)
        picked = [news.pick_rotating("ch1", arts)["id"] for _ in range(20)]
        assert set(picked) == {a["id"] for a in arts}   # every headline used once
        after = news.pick_rotating("ch1", arts)["id"]   # pool recycled
        assert after not in picked[-news.NEWS_RECENT_GUARD:]

    def test_identical_texts_are_treated_as_the_same_news(self, state_dir):
        arts = _arts(2)
        dup = {"id": "other", "url": arts[0]["url"], "text": arts[0]["text"]}
        first = news.pick_rotating("ch1", arts + [dup])
        # whatever was picked, its twin text must not be picked next
        nxt = news.pick_rotating("ch1", arts + [dup])
        assert nxt["text"] != first["text"]

    def test_history_is_persisted_and_fifo(self, state_dir):
        arts = _arts(4)
        ids = [news.pick_rotating("ch1", arts)["id"] for _ in range(4)]
        state = cfg.load_channel_state("ch1")
        assert state["news_used"] == ids
        assert state["last_news_id"] == ids[-1]
        assert state["last_news_at"]

    def test_legacy_used_key_is_migrated_and_dropped(self, state_dir):
        cfg.save_channel_state("ch1", {"used": ["id0", "id1"], "last_news_id": "id1"})
        arts = _arts(6)
        picked = news.pick_rotating("ch1", arts)
        assert picked["id"] not in ("id0", "id1")
        assert "used" not in cfg.load_channel_state("ch1")

    def test_scheduler_timestamps_survive_the_pick(self, state_dir):
        cfg.save_channel_state("ch1", {"last_poll_at": "2026-09-20T12:00:00",
                                       "last_analysis_at": "2026-09-20T09:00:00"})
        news.pick_rotating("ch1", _arts(3))
        state = cfg.load_channel_state("ch1")
        assert state["last_poll_at"] == "2026-09-20T12:00:00"
        assert state["last_analysis_at"] == "2026-09-20T09:00:00"


class TestPool:
    def test_pool_add_dedupes_by_id(self, state_dir):
        news.pool_add("ch1", _arts(5))
        assert news.pool_add("ch1", _arts(5)) == 0    # nothing new
        assert len(news.pool_articles("ch1")) == 5

    def test_pool_grows_across_calls(self, state_dir):
        news.pool_add("ch1", _arts(3))
        news.pool_add("ch1", _arts(3, prefix="new"))
        ids = {a["id"] for a in news.pool_articles("ch1")}
        assert ids == {"id0", "id1", "id2", "new0", "new1", "new2"}

    def test_pool_is_capped(self, state_dir, monkeypatch):
        monkeypatch.setattr(news, "NEWS_POOL_CAP", 4)
        news.pool_add("ch1", _arts(10))
        assert len(news.pool_articles("ch1")) == 4

    def test_pool_skips_articles_without_text(self, state_dir):
        news.pool_add("ch1", [{"id": "x", "url": "u", "text": "  "}])
        assert news.pool_articles("ch1") == []


class TestAnalysisLine:
    def test_uses_pool_when_the_network_fails(self, state_dir, monkeypatch):
        monkeypatch.setattr(news, "channel_articles", lambda *a, **k: [])
        news.pool_add("ch1", _arts(2))
        line = news.analysis_line("ch1", ["اخبار ارزی"], [])
        assert line.startswith("<a href=\"https://www.tgju.org/news/")
        assert "headline" in line

    def test_picks_randomly_from_the_growing_pool(self, state_dir, monkeypatch):
        monkeypatch.setattr(news, "channel_articles",
                            lambda *a, **k: _arts(4, prefix="call%d" % len(
                                news.pool_articles("ch1"))))
        seen = set()
        for _ in range(4):
            line = news.analysis_line("ch1", [], [])
            assert line
            seen.add(line)
        assert len(seen) == 4

    def test_escapes_html_in_the_headline(self, state_dir, monkeypatch):
        monkeypatch.setattr(news, "channel_articles",
                            lambda *a, **k: [{"id": "1", "url": "https://x/1",
                                              "text": "طلا & <b>نقره</b>"}])
        line = news.analysis_line("ch1", [], [])
        assert "&amp;" in line and "&lt;b&gt;" in line and "<b>" not in line
