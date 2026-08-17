# -*- coding: utf-8 -*-
"""Tests for platform.tgju_core.idempotency — duplicate-post protection.

Behavioral guarantee: the same (channel, content, scheduled-slot) triple may
only be posted once. `check_and_mark` returns `(is_duplicate, key)`:
  - first call:  (False, None)          — caller should proceed
  - after a successful send, `mark_used(key, message_id)` records it
  - any later call with identical content:  (True, key) — a duplicate
"""
import glob
import json
import os
import tempfile

import pytest

from platform.tgju_core.idempotency import IdempotencyManager

CONTENT = {"text": "قیمت دلار امروز", "type": "prices"}
CONTENT_SAME = {"text": "قیمت دلار امروز", "type": "prices"}
CONTENT_DIFF = {"text": "قیمت طلا امروز", "type": "prices"}


def _mark_latest_used(mgr, persist_dir, message_id="msg-1"):
    """Simulate the post pipeline: after a successful send, mark the newest key used."""
    files = glob.glob(os.path.join(persist_dir, "idempotency_*.json"))
    assert files, "expected at least one persisted idempotency key"
    latest = max(files, key=os.path.getmtime)
    with open(latest, encoding="utf-8") as f:
        key = json.load(f)["key"]
    assert mgr.mark_used(key, message_id) is True
    return key


class TestIdempotency:
    def test_first_call_allows_post(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = IdempotencyManager(persist_dir=d)
            dup, _ = mgr.check_and_mark("ch1", CONTENT, "slot-1")
            assert dup is False

    def test_identical_content_is_duplicate_after_send(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = IdempotencyManager(persist_dir=d)
            mgr.check_and_mark("ch1", CONTENT, "slot-1")
            _mark_latest_used(mgr, d)
            dup, key = mgr.check_and_mark("ch1", CONTENT_SAME, "slot-1")
            assert dup is True
            assert key is not None

    def test_different_content_not_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = IdempotencyManager(persist_dir=d)
            mgr.check_and_mark("ch1", CONTENT, "slot-1")
            _mark_latest_used(mgr, d)
            dup, _ = mgr.check_and_mark("ch1", CONTENT_DIFF, "slot-1")
            assert dup is False

    def test_different_channel_not_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = IdempotencyManager(persist_dir=d)
            mgr.check_and_mark("ch1", CONTENT, "slot-1")
            _mark_latest_used(mgr, d)
            dup, _ = mgr.check_and_mark("ch2", CONTENT, "slot-1")
            assert dup is False

    def test_different_slot_not_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = IdempotencyManager(persist_dir=d)
            mgr.check_and_mark("ch1", CONTENT, "slot-1")
            _mark_latest_used(mgr, d)
            dup, _ = mgr.check_and_mark("ch1", CONTENT, "slot-2")
            assert dup is False

    def test_check_idempotent_helper(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = IdempotencyManager(persist_dir=d)
            mgr.check_and_mark("ch1", CONTENT, "slot-1")
            assert mgr.check_idempotent("ch1", CONTENT, "slot-1") is False
            _mark_latest_used(mgr, d)
            assert mgr.check_idempotent("ch1", CONTENT, "slot-1") is True

    def test_persistence_across_instances(self):
        with tempfile.TemporaryDirectory() as d:
            mgr1 = IdempotencyManager(persist_dir=d)
            mgr1.check_and_mark("ch1", CONTENT, "slot-1")
            _mark_latest_used(mgr1, d)
            mgr2 = IdempotencyManager(persist_dir=d)
            dup, _ = mgr2.check_and_mark("ch1", CONTENT, "slot-1")
            assert dup is True

    def test_mark_used_unknown_key_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = IdempotencyManager(persist_dir=d)
            assert mgr.mark_used("no-such-key", "msg-1") is False
