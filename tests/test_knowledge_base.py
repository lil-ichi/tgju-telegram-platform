# -*- coding: utf-8 -*-
"""Tests for Knowledge Base (KB) Engine and AI integration."""
import os
import sys
import tempfile
import pytest

# Ensure repo root and tgju package are on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TGJU_DIR = os.path.join(BASE_DIR, "tgju")
if TGJU_DIR not in sys.path:
    sys.path.insert(0, TGJU_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import tgju_engine_kb as kb


class TestKnowledgeBaseEngine:
    def test_private_url_is_rejected(self):
        res = kb.fetch_url_content("http://127.0.0.1:8080/internal")
        assert res["ok"] is False

    def test_upload_size_is_limited(self):
        res = kb.save_uploaded_file("large.txt", b"x" * (kb._MAX_FILE_BYTES + 1))
        assert res["ok"] is False

    def test_load_save_config(self):
        cfg = kb.load_kb_config()
        assert "enabled" in cfg
        assert "sources" in cfg
        assert "jobs" in cfg
        assert cfg["jobs"]["poll_generate"]["enabled"] is True

    def test_add_snippet_source(self):
        title = "دستورالعمل لحن بازار طلا"
        text = "در تحلیل بازار طلا همواره به نوسانات انس جهانی و نرخ برابری دلار توجه کنید و لحن محترمانه داشته باشید."
        res = kb.add_snippet_source(title, text, tags=["طلا", "قوانین"])
        assert res["ok"] is True
        src_id = res["source"]["id"]

        # Verify source doc
        doc = kb.get_source_doc(src_id)
        assert doc is not None
        assert doc["title"] == title
        assert doc["word_count"] > 5
        assert len(doc["chunks"]) >= 1

        # Search test
        matches = kb.search_kb("طلا و انس جهانی", top_k=2)
        assert len(matches) >= 1
        assert matches[0]["title"] == title

        # Context generation test
        ctx = kb.get_kb_context("انس جهانی و طلا", job="analysis")
        assert title in ctx
        assert "انس جهانی" in ctx

        # Cleanup
        kb.delete_source(src_id)
        assert kb.get_source_doc(src_id) is None

    def test_add_folder_and_file_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create sample files
            f1 = os.path.join(tmpdir, "gold_report.md")
            with open(f1, "w", encoding="utf-8") as f:
                f.write("# گزارش هفتگی بازار طلا\nانس جهانی طلا در محدوده ۲۰۵۰ دلار معامله شد و انتظارات برای کاهش نرخ بهره ادامه دارد.")

            f2 = os.path.join(tmpdir, "fx_outlook.txt")
            with open(f2, "w", encoding="utf-8") as f:
                f.write("چشم‌انداز بازار ارز: ثبات نسبی در مرکز مبادله و کاهش حباب درهم.")

            # Test single file
            res_file = kb.add_file_source(f1, title="گزارش طلای هفتگی", tags=["طلا"])
            assert res_file["ok"] is True
            file_id = res_file["source"]["id"]

            # Test folder
            res_folder = kb.add_folder_source(tmpdir, title="پوشه گزارش‌ها")
            assert res_folder["ok"] is True
            folder_id = res_folder["source"]["id"]
            assert res_folder["source"]["files_count"] == 2

            # Search across both
            matches = kb.search_kb("کاهش نرخ بهره و انس", top_k=3)
            assert len(matches) >= 1
            found_titles = [m["title"] for m in matches]
            assert "گزارش طلای هفتگی" in found_titles or "پوشه گزارش‌ها" in found_titles

            # Clean up
            kb.delete_source(file_id)
            kb.delete_source(folder_id)

    def test_file_upload_helper(self):
        content = b"Bitcoin and crypto market overview: BTC holding above $65,000 support level."
        res = kb.save_uploaded_file("crypto_memo.txt", content, title="Crypto Memo")
        assert res["ok"] is True
        src_id = res["source"]["id"]

        matches = kb.search_kb("crypto bitcoin", top_k=1)
        assert len(matches) >= 1
        assert "Crypto Memo" in matches[0]["title"]

        kb.delete_source(src_id)

    def test_clean_html_text(self):
        raw_html = """
        <html>
          <head><title>Test Page</title><style>.test { color: red; }</style></head>
          <body>
            <nav>Menu Items</nav>
            <h1>سرخط خبر بازار</h1>
            <p>این یک <b>گزارش تحلیلی</b> معتبر برای بازار سکه است.<br>قیمت‌ها صعودی بودند.</p>
            <script>console.log('secret');</script>
          </body>
        </html>
        """
        cleaned = kb._clean_html_text(raw_html)
        assert "Menu Items" not in cleaned
        assert "console.log" not in cleaned
        assert "سرخط خبر بازار" in cleaned
        assert "گزارش تحلیلی" in cleaned


class TestKnowledgeBaseApi:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from tgju.tgju_platform import app
        from tgju_core.runtime import RUNTIME
        RUNTIME["auth_disabled"] = True
        c = TestClient(app)
        try:
            yield c
        finally:
            RUNTIME.pop("auth_disabled", None)

    def test_api_kb_lifecycle(self, client):
        # 1. GET /api/kb
        res = client.get("/api/kb")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "sources" in data

        # 2. POST /api/kb/sources/snippet
        res_snp = client.post("/api/kb/sources/snippet", json={
            "title": "قانون نظرسنجی کانال",
            "text": "هرگز گزینه‌های جهت‌دار مایل به خرید و فروش ثبت نشود.",
            "tags": ["نظرسنجی", "اخلاق"]
        })
        assert res_snp.status_code == 200
        snp_data = res_snp.json()
        assert snp_data["ok"] is True
        src_id = snp_data["source"]["id"]

        # 3. GET /api/kb/sources/{id}/content
        res_cnt = client.get(f"/api/kb/sources/{src_id}/content")
        assert res_cnt.status_code == 200
        cnt_data = res_cnt.json()
        assert cnt_data["ok"] is True
        assert "گزینه‌های جهت‌دار" in cnt_data["doc"]["raw_text"]

        # 4. POST /api/kb/search
        res_srch = client.post("/api/kb/search", json={"query": "گزینه‌های نظرسنجی"})
        assert res_srch.status_code == 200
        srch_data = res_srch.json()
        assert srch_data["ok"] is True
        assert len(srch_data["matches"]) >= 1

        # 5. POST /api/kb/sources/{id}/toggle
        res_tog = client.post(f"/api/kb/sources/{src_id}/toggle")
        assert res_tog.status_code == 200
        assert res_tog.json()["source"]["enabled"] is False

        # 6. DELETE /api/kb/sources/{id}
        res_del = client.delete(f"/api/kb/sources/{src_id}")
        assert res_del.status_code == 200
        assert res_del.json()["ok"] is True
