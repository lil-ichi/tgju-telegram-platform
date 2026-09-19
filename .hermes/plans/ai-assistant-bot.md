# TGJU AI Assistant Bot — Implementation Plan

## Goal

Add a second Telegram bot to the TGJU control center that answers user questions conversationally. It must remain completely independent from the existing price-publishing bot and ground answers in the existing Knowledge Base retrieval system.

## Architecture contract

### Separation from the publishing bot

- Publishing bot remains under `state/bot_profile.json` and existing `/api/bot*` routes.
- AI assistant bot uses `state/ai_assistant.json` and `/api/assistant*` routes.
- The assistant never calls `get_active_token()` or reads the publishing bot token.
- Changing or disabling the assistant cannot alter channel posting or scheduler behavior.

### Assistant pipeline

`Telegram user message → assistant policy checks → conversation context → KB search → grounded prompt → selected AI provider/model → Telegram reply`

The existing `tgju_engine_kb.get_kb_context()` is the retrieval source. The existing `tgju_engine_ai.try_providers()` is the model gateway and fallback layer.

### State shape

```json
{
  "enabled": false,
  "token": "",
  "bot_username": "",
  "provider": "",
  "model": "",
  "system_prompt": "...",
  "welcome_message": "...",
  "fallback_message": "...",
  "knowledge": {
    "enabled": true,
    "top_k": 4,
    "max_context_chars": 3000,
    "answer_only_from_kb": true,
    "show_sources": true
  },
  "conversation": {
    "history_messages": 6,
    "max_question_chars": 1200,
    "private_chats": true,
    "group_chats": false
  },
  "polling": {
    "enabled": false,
    "timeout_seconds": 25,
    "offset": 0
  },
  "stats": {
    "questions": 0,
    "answered": 0,
    "fallbacks": 0,
    "last_message_at": ""
  }
}
```

Secrets stay in ignored local state. API responses mask the token and never return it in full.

## Dashboard

Add a dedicated Telegram navigation item named `دستیار هوشمند`, clearly separate from `ربات انتشار`.

The page will use the existing restrained white/blue dashboard system and contain:

1. Connection and enablement
2. AI provider and model
3. Knowledge retrieval controls
4. Persona and response messages
5. Conversation and chat-scope limits
6. Simulation playground showing answer, provider, latency, and retrieved sources
7. Runtime status and counters

## Delivery increments

### Increment 1 — configuration + grounded simulation

- New assistant engine and state contract
- Authenticated GET/PUT/test/simulate API
- Independent token validation
- KB-grounded prompt and AI-provider call
- Separate dashboard tab
- Unit and API tests

### Increment 2 — live Telegram transport ✅ Implemented 2026-09-16

**Natural AI routing refinement (2026-09-16):** ordinary conversation uses the AI directly with history; TGJU/market questions selectively retrieve KB context; no-match TGJU questions still receive a natural AI answer. Strict KB-only mode is optional and off by default. The shared AI client accepts final JSON plus concatenated/SSE chunk responses.

- Dedicated long-polling task with independent update offset ✅
- `/start`, `/help`, `/reset`, text question handling ✅
- HTML-safe Telegram replies and message chunking ✅
- Per-chat short conversation history ✅
- Retry/backoff and graceful shutdown ✅

### Increment 3 — operations and safety

- Allow/block lists, admin-only mode, group mention behavior
- Source citations and answer confidence policy
- Activity log, per-chat metrics, failure diagnostics
- Rate limits and abuse controls
- Exportable conversation audit with retention settings

## Verification gates

- TDD: each behavior test fails before implementation and passes afterward.
- Full test suite remains green.
- New endpoints verified through FastAPI TestClient.
- UI JavaScript syntax checked with Node.
- Live DOM proves the separate tab renders and calls the assistant API.
- Publishing bot configuration remains byte-for-byte unaffected by assistant API operations.
- `APP.md` updated in the same turn as structural changes.

## Non-goals for Increment 1

- No live polling loop yet.
- No modification to channel posting, channel scheduler, or publisher bot profiles.
- No external vector database; use the existing local KB retrieval engine.
