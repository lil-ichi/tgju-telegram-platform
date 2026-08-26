# -*- coding: utf-8 -*-
"""Tests for the unified Bale/Rubika/Eitaa polling engine (transport-only)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tgju"))

import tgju_engine_polling as p  # noqa: E402


def test_extract_updates_bale_telegram_envelope():
    ok = {"ok": True, "result": [{"update_id": 1, "message": {}}]}
    assert p._extract_updates("bale", True, ok) == [{"update_id": 1, "message": {}}]
    assert p._extract_updates("bale", False, {"ok": False}) == []


def test_extract_updates_rubika_v3_envelope():
    resp = {"status": "OK", "data": {"updates": [{"update_id": 7}]}}
    assert p._extract_updates("rubika", True, resp) == [{"update_id": 7}]
    # error envelope → no updates
    assert p._extract_updates("rubika", True, {"status": "ERROR"}) == []
    # data as plain list also accepted
    assert p._extract_updates("rubika", True, {"status": "OK", "data": [{"update_id": 9}]}) \
        == [{"update_id": 9}]


def test_extract_offset_variants():
    assert p._extract_offset({"update_id": 42}) == 42
    assert p._extract_offset({"offset": 5}) == 5
    assert p._extract_offset({}) is None


def test_api_urls():
    assert "tapi.bale.ai/botTKT/getUpdates" in p._api_url("bale", "TKT", "getUpdates")
    assert "botapi.rubika.ir/v3/TKT/getUpdates" in p._api_url("rubika", "TKT", "getUpdates")
    assert "eitaayar.ir/api/TKT/getUpdates" in p._api_url("eitaa", "TKT", "getUpdates")


def test_start_stop_all_lifecycle():
    started = p.start_all(lambda plat: (lambda u: None))
    assert set(started.keys()) == {"bale", "rubika", "eitaa"}
    try:
        st = {s["platform"]: s for s in p.status_all()}
        for plat in ("bale", "rubika", "eitaa"):
            assert st[plat]["running"], "%s poller should be running" % plat
            assert st[plat]["interval"] == p.DEFAULTS[plat]["interval"]
            assert st[plat]["long_poll"] == p.DEFAULTS[plat]["long_poll"]
        # unconfigured tokens → threads alive but idle (no crash loop)
        assert all(not st[plat]["configured"]
                   for plat in ("bale", "rubika", "eitaa"))
    finally:
        p.stop_all()
