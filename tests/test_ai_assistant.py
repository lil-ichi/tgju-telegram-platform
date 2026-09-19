# -*- coding: utf-8 -*-
"""Tests for the independent Telegram AI assistant bot."""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TGJU_DIR = os.path.join(BASE_DIR, "tgju")
if TGJU_DIR not in sys.path:
    sys.path.insert(0, TGJU_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import tgju_engine_ai_assistant as assistant
import tgju_engine_ai as ai_engine


def test_assistant_config_is_independent_and_token_is_masked(tmp_path, monkeypatch):
    config_path = tmp_path / "ai_assistant.json"
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(config_path))

    saved = assistant.save_assistant_config({
        "enabled": True,
        "token": "123456:assistant-secret-token",
        "provider": "gateway",
        "model": "answer-model",
    })

    assert config_path.exists()
    on_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert on_disk["token"] == "123456:assistant-secret-token"
    assert saved["provider"] == "gateway"

    public = assistant.public_assistant_config(saved)
    assert "token" not in public
    assert public["token_set"] is True
    assert public["token_preview"] == "123456…oken"
    assert public["knowledge"]["answer_only_from_kb"] is False
    assert public["conversation"]["private_chats"] is True


def test_custom_ai_api_secret_is_masked_and_blank_update_preserves_it(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(tmp_path / "assistant.json"))
    saved = assistant.save_assistant_config({
        "api": {
            "base_url": "https://provider.example/v1",
            "api_key": "sk-assistant-private-key",
            "model": "custom-model",
            "timeout_seconds": 45,
            "max_tokens": 1800,
        }
    })
    public = assistant.public_assistant_config(saved)

    assert "api_key" not in public["api"]
    assert public["api"]["api_key_set"] is True
    assert public["api"]["api_key_preview"] == "sk-a…-key"
    assert public["api"]["base_url"] == "https://provider.example/v1"
    assert public["api"]["model"] == "custom-model"

    updated = assistant.save_assistant_config({"api": {"api_key": "", "model": "next-model"}})
    assert updated["api"]["api_key"] == "sk-assistant-private-key"
    assert updated["api"]["model"] == "next-model"


def test_answer_prefers_custom_assistant_api_over_shared_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(tmp_path / "assistant.json"))
    cfg = assistant.save_assistant_config({
        "provider_mode": "custom",
        "api": {
            "base_url": "https://provider.example/v1",
            "api_key": "private-key",
            "model": "custom-model",
            "timeout_seconds": 35,
            "max_tokens": 1700,
        },
    })
    captured = {}

    def runner(ai_cfg, prompt, **kwargs):
        captured["provider"] = ai_cfg["providers"]["assistant_custom"]
        captured["kwargs"] = kwargs
        return {"ok": True, "text": "پاسخ طبیعی", "provider": "assistant_custom", "model": "custom-model", "latency_ms": 3}

    result = assistant.answer_question(
        "سلام",
        config=cfg,
        provider_runner=runner,
        ai_config={"providers": {"shared": {"base_url": "https://shared.invalid/v1", "model": "shared"}}},
    )

    assert result["ok"] is True
    assert captured["provider"]["base_url"] == "https://provider.example/v1"
    assert captured["provider"]["api_key"] == "private-key"
    assert captured["provider"]["model"] == "custom-model"
    assert captured["kwargs"]["preferred_provider"] == "assistant_custom"
    assert captured["kwargs"]["timeout_s"] == 35
    assert captured["kwargs"]["max_tokens"] == 1700


def test_blank_token_update_preserves_existing_secret(tmp_path, monkeypatch):
    config_path = tmp_path / "ai_assistant.json"
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(config_path))
    assistant.save_assistant_config({"token": "111:keep-me", "enabled": False})

    updated = assistant.save_assistant_config({
        "token": "",
        "enabled": True,
        "knowledge": {"max_context_chars": 2400},
    })

    assert updated["token"] == "111:keep-me"
    assert updated["enabled"] is True
    assert updated["knowledge"]["max_context_chars"] == 2400
    assert updated["knowledge"]["answer_only_from_kb"] is False


def test_answer_question_uses_kb_context_and_selected_provider(tmp_path, monkeypatch):
    config_path = tmp_path / "ai_assistant.json"
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(config_path))
    assistant.save_assistant_config({
        "enabled": True,
        "provider": "gateway",
        "model": "answer-model",
        "knowledge": {"enabled": True, "show_sources": True, "max_context_chars": 3000, "answer_only_from_kb": True},
    })
    captured = {}

    def fake_search(query, top_k=4, tags=None):
        assert query == "قیمت طلای آبشده چیست؟"
        return [{
            "source_id": "gold-guide",
            "title": "راهنمای بازار طلا",
            "score": 8.5,
            "text": "طلای آبشده بر پایه مظنه و عیار معامله می‌شود.",
        }]

    def fake_provider_runner(cfg, prompt, max_tokens, timeout_s, job_id="", preferred_provider=None):
        captured["prompt"] = prompt
        captured["preferred_provider"] = preferred_provider
        assert cfg["providers"]["gateway"]["model"] == "answer-model"
        return {
            "ok": True,
            "text": "طلای آبشده بر پایه مظنه و عیار ارزش‌گذاری می‌شود.",
            "provider": "gateway",
            "model": "answer-model",
            "latency_ms": 12,
        }

    ai_cfg = {"providers": {"gateway": {
        "kind": "openai_compat", "enabled": True, "base_url": "http://example.test/v1",
        "api_key": "", "model": "provider-default",
    }}}
    result = assistant.answer_question(
        "قیمت طلای آبشده چیست؟",
        kb_search=fake_search,
        provider_runner=fake_provider_runner,
        ai_config=ai_cfg,
    )

    assert result["ok"] is True
    assert result["grounded"] is True
    assert result["sources"] == [{"id": "gold-guide", "title": "راهنمای بازار طلا", "score": 8.5}]
    assert captured["preferred_provider"] == "gateway"
    assert "طلای آبشده بر پایه مظنه" in captured["prompt"]
    assert "فقط بر اساس دانش ارائه شده" in captured["prompt"]


def test_normal_conversation_uses_ai_without_querying_kb(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(tmp_path / "assistant.json"))
    cfg = assistant.save_assistant_config({
        "enabled": True,
        "provider": "gateway",
        "knowledge": {"enabled": True, "answer_only_from_kb": False},
    })
    captured = {}

    def kb_must_not_run(*args, **kwargs):
        raise AssertionError("normal conversation must not query the TGJU KB")

    def runner(ai_cfg, prompt, **kwargs):
        captured["prompt"] = prompt
        return {"ok": True, "text": "سلام! خوبم، چطور می‌توانم کمکت کنم؟", "provider": "gateway", "model": "m", "latency_ms": 4}

    result = assistant.answer_question(
        "سلام، حالت چطوره؟",
        config=cfg,
        kb_search=kb_must_not_run,
        provider_runner=runner,
        ai_config={"providers": {"gateway": {"kind": "openai_compat", "enabled": True, "base_url": "x", "model": "m"}}},
    )

    assert result["ok"] is True
    assert result["route"] == "conversation"
    assert result["knowledge_used"] is False
    assert "دانش بازیابی شده" not in captured["prompt"]
    assert "طبیعی و انسانی" in captured["prompt"]


def test_tgju_question_without_kb_match_still_gets_ai_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(tmp_path / "assistant.json"))
    cfg = assistant.save_assistant_config({
        "enabled": True,
        "provider": "gateway",
        "knowledge": {"enabled": True, "answer_only_from_kb": False},
    })
    called = {"provider": 0}

    def runner(ai_cfg, prompt, **kwargs):
        called["provider"] += 1
        assert "منبع مرتبطی در پایگاه دانش پیدا نشد" in prompt
        return {"ok": True, "text": "برای نرخ لحظه‌ای دلار بهتر است داده زنده TGJU بررسی شود.", "provider": "gateway", "model": "m", "latency_ms": 5}

    result = assistant.answer_question(
        "نرخ دلار در TGJU چطور محاسبه می‌شود؟",
        config=cfg,
        kb_search=lambda *args, **kwargs: [],
        provider_runner=runner,
        ai_config={"providers": {"gateway": {"kind": "openai_compat", "enabled": True, "base_url": "x", "model": "m"}}},
    )

    assert called["provider"] == 1
    assert result["ok"] is True
    assert result["route"] == "tgju"
    assert result["grounded"] is False
    assert result["fallback"] is False


def test_ai_engine_joins_gateway_chunk_objects():
    raw = "\n".join([
        '{"object":"chat.completion.chunk","choices":[{"delta":{"content":"سلام! "}}]}',
        '{"object":"chat.completion.chunk","choices":[{"delta":{"content":"خوشحالم می‌بینمت."}}]}',
        '{"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}',
    ])
    assert ai_engine._extract_chat_content(raw) == "سلام! خوشحالم می‌بینمت."


def test_answer_prompt_respects_context_character_limit(tmp_path, monkeypatch):
    config_path = tmp_path / "ai_assistant.json"
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(config_path))
    cfg = assistant.save_assistant_config({
        "enabled": True,
        "provider": "gateway",
        "knowledge": {"max_context_chars": 500, "answer_only_from_kb": True},
    })
    captured = {}

    def runner(ai_cfg, prompt, **kwargs):
        captured["prompt"] = prompt
        return {"ok": True, "text": "پاسخ", "provider": "gateway", "model": "m", "latency_ms": 1}

    assistant.answer_question(
        "پرسش درباره طلا",
        config=cfg,
        kb_search=lambda *args, **kwargs: [{
            "source_id": "long", "title": "منبع بلند", "score": 2,
            "text": "x" * 3000,
        }],
        provider_runner=runner,
        ai_config={"providers": {"gateway": {"enabled": True, "kind": "openai_compat", "base_url": "x", "model": "m"}}},
    )

    assert "x" * 501 not in captured["prompt"]
    assert "x" * 450 in captured["prompt"]


def test_answer_only_from_kb_returns_fallback_without_calling_provider(tmp_path, monkeypatch):
    config_path = tmp_path / "ai_assistant.json"
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(config_path))
    cfg = assistant.save_assistant_config({
        "enabled": True,
        "fallback_message": "پاسخ معتبر در پایگاه دانش پیدا نشد.",
        "knowledge": {"enabled": True, "answer_only_from_kb": True},
    })

    def should_not_run(*args, **kwargs):
        raise AssertionError("provider must not run without KB evidence")

    result = assistant.answer_question(
        "پرسش بدون منبع درباره طلا",
        config=cfg,
        kb_search=lambda *args, **kwargs: [],
        provider_runner=should_not_run,
        ai_config={"providers": {}},
    )

    assert result == {
        "ok": True,
        "answer": "پاسخ معتبر در پایگاه دانش پیدا نشد.",
        "grounded": False,
        "fallback": True,
        "sources": [],
        "provider": "",
        "model": "",
        "latency_ms": 0,
    }


def _api_client():
    from fastapi.testclient import TestClient
    from tgju.tgju_platform import app
    from tgju_core.runtime import RUNTIME

    RUNTIME["auth_disabled"] = True
    return TestClient(app), RUNTIME


def test_assistant_api_saves_settings_without_returning_token(tmp_path, monkeypatch):
    config_path = tmp_path / "ai_assistant.json"
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(config_path))
    client, runtime = _api_client()
    try:
        response = client.put("/api/assistant", json={
            "enabled": True,
            "token": "987654:api-secret-token",
            "provider": "gateway",
            "model": "assistant-model",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert "token" not in body["config"]
        assert body["config"]["token_set"] is True

        loaded = client.get("/api/assistant")
        assert loaded.status_code == 200
        assert loaded.json()["token_preview"] == "987654…oken"
        assert "runtime" in loaded.json()
        assert "status" in loaded.json()["runtime"]
    finally:
        runtime.pop("auth_disabled", None)


def test_assistant_models_api_uses_custom_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(tmp_path / "assistant.json"))
    assistant.save_assistant_config({
        "provider_mode": "custom",
        "api": {"base_url": "https://provider.example/v1", "api_key": "saved-key"},
    })
    captured = {}

    def fake_models(overrides=None):
        captured.update(overrides or {})
        return {"ok": True, "models": ["model-a", "model-b"]}

    monkeypatch.setattr(assistant, "list_assistant_models", fake_models, raising=False)
    client, runtime = _api_client()
    try:
        response = client.post("/api/assistant/models", json={
            "base_url": "https://another.example/v1",
            "api_key": "temporary-key",
        })
        assert response.status_code == 200
        assert response.json()["models"] == ["model-a", "model-b"]
        assert captured["base_url"] == "https://another.example/v1"
        assert captured["api_key"] == "temporary-key"
    finally:
        runtime.pop("auth_disabled", None)


def test_assistant_simulation_api_returns_grounded_answer(tmp_path, monkeypatch):
    config_path = tmp_path / "ai_assistant.json"
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(config_path))
    assistant.save_assistant_config({"enabled": True})

    monkeypatch.setattr(assistant, "answer_question", lambda question: {
        "ok": True,
        "answer": "پاسخ آزمایشی مستند",
        "grounded": True,
        "fallback": False,
        "sources": [{"id": "guide", "title": "راهنما", "score": 4.0}],
        "provider": "gateway",
        "model": "assistant-model",
        "latency_ms": 9,
    })
    client, runtime = _api_client()
    try:
        response = client.post("/api/assistant/simulate", json={"question": "طلا چیست؟"})
        assert response.status_code == 200
        assert response.json()["answer"] == "پاسخ آزمایشی مستند"
        assert response.json()["sources"][0]["title"] == "راهنما"
    finally:
        runtime.pop("auth_disabled", None)


def test_dashboard_has_separate_ai_assistant_tab_and_api_wiring():
    ui_path = os.path.join(TGJU_DIR, "tgju_platform_ui.html")
    html = open(ui_path, encoding="utf-8").read()

    assert 'data-panel="assistant"' in html
    assert 'id="panel_assistant"' in html
    assert 'id="as_polling"' in html
    assert "function loadAssistant" in html
    assert "function saveAssistant" in html
    assert "function simulateAssistant" in html
    assert "function loadAssistantModels" in html
    assert "function testAssistantProvider" in html
    assert 'id="as_api_base_url"' in html
    assert 'id="as_api_key"' in html
    assert '<select id="as_model"' in html
    assert "'/api/assistant'" in html
    assert "'/api/assistant/simulate'" in html


def test_poll_once_consumes_private_message_and_sends_answer():
    sent = []
    config = assistant.normalize_assistant_config({
        "enabled": True,
        "token": "assistant-token",
        "polling": {"enabled": True, "offset": 0},
        "conversation": {"private_chats": True, "group_chats": False},
    })

    result = assistant.poll_assistant_once(
        config=config,
        get_updates=lambda token, offset, timeout: [{
            "update_id": 42,
            "message": {
                "text": "قیمت طلا چگونه تعیین می‌شود؟",
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 7, "first_name": "کاربر"},
            },
        }],
        send_message=lambda token, chat_id, text: sent.append((token, chat_id, text)) or {"ok": True},
        answer_fn=lambda question, **kwargs: {
            "ok": True, "answer": "بر پایه منابع بازار و داده‌های معتبر.",
            "sources": [{"title": "راهنمای طلا"}], "grounded": True,
        },
    )

    assert result["processed"] == 1
    assert result["next_offset"] == 43
    assert sent == [("assistant-token", 123, "بر پایه منابع بازار و داده‌های معتبر.\n\nمنابع: راهنمای طلا")]


def test_start_command_uses_welcome_without_calling_ai():
    sent = []
    config = assistant.normalize_assistant_config({
        "enabled": True,
        "token": "assistant-token",
        "welcome_message": "به دستیار TGJU خوش آمدید.",
        "polling": {"enabled": True},
    })

    result = assistant.poll_assistant_once(
        config=config,
        get_updates=lambda *args: [{
            "update_id": 9,
            "message": {"text": "/start", "chat": {"id": 55, "type": "private"}},
        }],
        send_message=lambda token, chat_id, text: sent.append(text) or {"ok": True},
        answer_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI must not run")),
    )

    assert result["processed"] == 1
    assert sent == ["به دستیار TGJU خوش آمدید."]


def test_group_message_is_ignored_when_groups_are_disabled():
    sent = []
    config = assistant.normalize_assistant_config({
        "enabled": True,
        "token": "assistant-token",
        "polling": {"enabled": True},
        "conversation": {"private_chats": True, "group_chats": False},
    })
    result = assistant.poll_assistant_once(
        config=config,
        get_updates=lambda *args: [{
            "update_id": 11,
            "message": {"text": "سلام", "chat": {"id": -100, "type": "group"}},
        }],
        send_message=lambda *args: sent.append(args),
        answer_fn=lambda *args, **kwargs: {"ok": True, "answer": "نباید ارسال شود"},
    )

    assert result["processed"] == 0
    assert result["ignored"] == 1
    assert result["next_offset"] == 12
    assert sent == []


def test_platform_lifecycle_starts_ai_assistant_poller():
    platform_path = os.path.join(TGJU_DIR, "tgju_platform.py")
    source = open(platform_path, encoding="utf-8").read()
    assert "assistant_polling_loop" in source
    assert 'name="tgju-ai-assistant"' in source


def test_conversation_history_is_passed_to_next_answer_and_reset(tmp_path, monkeypatch):
    conversations_path = tmp_path / "assistant_conversations.json"
    monkeypatch.setattr(assistant, "ASSISTANT_CONVERSATIONS_PATH", str(conversations_path))
    config = assistant.normalize_assistant_config({
        "enabled": True,
        "token": "assistant-token",
        "polling": {"enabled": True},
        "conversation": {"history_messages": 2},
    })
    histories = []
    updates = iter([
        [{"update_id": 1, "message": {"text": "پرسش اول", "chat": {"id": 77, "type": "private"}}}],
        [{"update_id": 2, "message": {"text": "پرسش دوم", "chat": {"id": 77, "type": "private"}}}],
        [{"update_id": 3, "message": {"text": "/reset", "chat": {"id": 77, "type": "private"}}}],
    ])

    def answer(question, history=None, **kwargs):
        histories.append(list(history or []))
        return {"ok": True, "answer": "پاسخ " + question, "sources": [], "grounded": True}

    for _ in range(3):
        assistant.poll_assistant_once(
            config=config,
            get_updates=lambda *args: next(updates),
            send_message=lambda *args: {"ok": True},
            answer_fn=answer,
        )

    assert histories[0] == []
    assert histories[1] == [
        {"role": "user", "content": "پرسش اول"},
        {"role": "assistant", "content": "پاسخ پرسش اول"},
    ]
    assert assistant.load_conversations().get("77") in (None, [])


def test_telegram_send_chat_action_called_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(tmp_path / "assistant.json"))
    config = assistant.normalize_assistant_config({
        "enabled": True,
        "token": "bot-test-token",
        "polling": {"enabled": True},
    })
    chat_actions = []
    messages = []
    update = {
        "update_id": 101,
        "message": {
            "text": "قیمت سکه امامی چنده؟",
            "chat": {"id": 888, "type": "private"},
        },
    }

    def fake_action(token, chat_id, action="typing"):
        chat_actions.append((token, chat_id, action))
        return {"ok": True}

    def fake_send(token, chat_id, text):
        messages.append((token, chat_id, text))
        return {"ok": True}

    assistant.poll_assistant_once(
        config=config,
        get_updates=lambda *args: [update],
        send_message=fake_send,
        send_chat_action=fake_action,
        answer_fn=lambda text, **kwargs: {"ok": True, "answer": "سکه ۲۳۰ میلیونه.", "sources": []},
    )

    assert len(chat_actions) == 1
    assert chat_actions[0] == ("bot-test-token", 888, "typing")
    assert len(messages) == 1


def test_live_market_data_grounding_injection(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant, "ASSISTANT_CONFIG_PATH", str(tmp_path / "assistant.json"))
    cfg = assistant.save_assistant_config({
        "enabled": True,
        "provider": "gateway",
    })

    fake_rows = {
        "sekee": {"name": "سکه امامی", "price": 2339850000, "change_pct": 1.08},
        "geram18": {"name": "طلای 18 عیار", "price": 235588000, "change_pct": 1.29},
        "price_dollar_rl": {"name": "دلار", "price": 2302000, "change_pct": 0.5},
    }
    from tgju_core import state as core_state
    monkeypatch.setattr(core_state, "cached_rows", lambda: fake_rows)

    context = assistant.get_live_market_context("قیمت دلار و سکه چنده؟")
    assert "سکه امامی" in context
    assert "دلار" in context
    assert "تومان" in context

    captured = {}
    def fake_runner(ai_cfg, prompt, **kwargs):
        captured["prompt"] = prompt
        return {"ok": True, "text": "دلار و سکه افزایش داشتند.", "latency_ms": 10, "ttft_ms": 8}

    res = assistant.answer_question(
        "قیمت دلار و سکه چنده؟",
        config=cfg,
        provider_runner=fake_runner,
        ai_config={"providers": {"gateway": {"kind": "openai_compat", "enabled": True, "base_url": "x"}}},
    )

    assert res["ok"] is True
    assert res["live_prices_used"] is True
    assert res["ttft_ms"] == 8
    assert "سکه امامی" in captured["prompt"]
    assert "نرخ‌های لحظه‌ای بازار" in captured["prompt"]


def test_kb_foundational_knowledge_fallback():
    from tgju_engine_kb import search_kb, FOUNDATIONAL_TGJU_KNOWLEDGE
    assert len(FOUNDATIONAL_TGJU_KNOWLEDGE) >= 3

    matches = search_kb("طلای آبشده و مظنه مثقال", top_k=2)
    assert len(matches) >= 1
    assert any("مظنه" in m["text"] or "آبشده" in m["text"] for m in matches)


def test_assistant_streaming_route(monkeypatch):
    def fake_stream(question, **kwargs):
        yield {"type": "meta", "ttft_ms": 45, "provider": "test_p", "model": "test_m"}
        yield {"type": "token", "content": "سلام ", "ttft_ms": 45}
        yield {"type": "token", "content": "کاربر!", "ttft_ms": 45}
        yield {"type": "done", "answer": "سلام کاربر!", "ttft_ms": 45, "latency_ms": 120}

    monkeypatch.setattr(assistant, "answer_question_stream", fake_stream)

    client, runtime = _api_client()
    try:
        response = client.post("/api/assistant/stream", json={"question": "سلام"})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        text = response.text
        assert "data: " in text
        assert "سلام " in text
        assert "کاربر!" in text
        assert '"ttft_ms": 45' in text
    finally:
        runtime.pop("auth_disabled", None)

