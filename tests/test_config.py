# -*- coding: utf-8 -*-
"""Tests for tgju_engine_config — YAML channel loading and state helpers.

These tests run against the real channels.yaml and a temp state dir so they
never touch live runtime state.
"""
import json
import os
import sys
import tempfile

import pytest

import tgju.tgju_engine_config as cfg

YAML_PATH = os.path.join(os.path.dirname(__file__), "..", "platform", "channels.yaml")


class TestLoadChannels:
    def test_channels_load(self):
        channels = cfg.load_channels()
        assert isinstance(channels, list)
        assert len(channels) >= 1

    def test_every_channel_has_required_keys(self):
        required = {"id", "name", "enabled", "slugs", "slug_groups"}
        for ch in cfg.load_channels():
            assert required.issubset(set(ch.keys())), ch.get("id")

    def test_ids_unique_and_sequential(self):
        ids = [c["id"] for c in cfg.load_channels()]
        assert len(ids) == len(set(ids))
        assert ids == sorted(ids, key=lambda s: int(s.replace("ch", "")))

    def test_telegram_ids_are_placeholders(self):
        # Public repo safety: no real channel IDs may be committed.
        for ch in cfg.load_channels():
            assert ch.get("telegram_id", "") == ""


class TestYamlHelpers:
    def test_load_yaml_missing_raises(self):
        # load_yaml is a hard loader: a missing file must surface loudly,
        # not silently return {} (which would wipe channel configs).
        with pytest.raises(FileNotFoundError):
            cfg.load_yaml("/nonexistent/path.yaml")


class TestSlugOverrides:
    def test_save_and_load_roundtrip(self, tmp_path):
        orig_state, orig_path = cfg.STATE_DIR, cfg.OVERRIDES_PATH
        try:
            cfg.STATE_DIR = str(tmp_path)
            cfg.OVERRIDES_PATH = os.path.join(str(tmp_path), "slug_overrides.json")
            data = {"crypto-bitcoin": {"unit": "دلار", "manual_price": "96000"}}
            cfg.save_slug_overrides(data)
            assert cfg.load_slug_overrides() == data
        finally:
            cfg.STATE_DIR, cfg.OVERRIDES_PATH = orig_state, orig_path

    def test_load_missing_overrides_returns_empty(self, tmp_path):
        orig_state, orig_path = cfg.STATE_DIR, cfg.OVERRIDES_PATH
        try:
            cfg.STATE_DIR = str(tmp_path)
            cfg.OVERRIDES_PATH = os.path.join(str(tmp_path), "missing.json")
            assert cfg.load_slug_overrides() == {}
        finally:
            cfg.STATE_DIR, cfg.OVERRIDES_PATH = orig_state, orig_path


class TestChannelState:
    def test_state_roundtrip(self, tmp_path):
        orig_state = cfg.STATE_DIR
        try:
            cfg.STATE_DIR = str(tmp_path)
            state = {"day": 1, "used": ["a", "b"]}
            cfg.save_channel_state("ch1", state)
            assert cfg.load_channel_state("ch1") == state
        finally:
            cfg.STATE_DIR = orig_state
