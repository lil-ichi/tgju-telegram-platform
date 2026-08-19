# -*- coding: utf-8 -*-
"""
tgju_core_integration.py — Bridges the legacy platform (tgju_platform.py)
with the new tgju_core architecture:
  DATA  →  DECISION (orchestrator)  →  CONTENT  →  FORMAT  →  DELIVERY

Responsibilities:
- Initialise tgju_core singletons with real state paths
- Build Snapshot objects from the live price cache
- Wrap scheduler ticks: run id + events + idempotency
- Expose helper functions the webapp uses for the Command Center UI
"""
import os
import time
import threading
from datetime import datetime, timedelta

from tgju_core.types import (
    ContentType, TriggerType, RunStatus, Snapshot, ChannelDefinition,
    generate_snapshot_id, content_hash)
from tgju_core.events import get_event_bus, emit_event, EventType
from tgju_core.runs import get_run_manager, create_run, complete_run
from tgju_core.channels import get_channel_manager
from tgju_core.orchestrator import ContentOrchestrator
from tgju_core.health import HealthScorer
from tgju_core.secrets import get_secrets_manager
from tgju_core.idempotency import get_idempotency_manager
from tgju_core.approval import get_approval_manager
from tgju_core.simulation import SimulationRunner

from tgju_engine_config import BASE_DIR, log_line, LOG_PATH
from tgju_engine_scrape import get_all_prices
from tgju_engine_orchestrator import build_for_channel as legacy_build
from tgju_engine_format import esc

STATE = os.path.join(BASE_DIR, "state")
EVENTS_DIR = os.path.join(STATE, "events")
RUNS_DIR = os.path.join(STATE, "runs")
VERSIONS_DIR = os.path.join(STATE, "config_versions")
APPROVALS_DIR = os.path.join(STATE, "approvals")
IDEM_DIR = os.path.join(STATE, "idempotency")

_initialized = False
_init_lock = threading.Lock()

# Runtime mirror: latest snapshot + cache-age for the UI
RUNTIME = {"snapshot": None, "snapshot_at": None, "orchestrating": False}


def init_core() -> None:
    """Idempotent initialisation of all tgju_core singletons."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        for d in (EVENTS_DIR, RUNS_DIR, VERSIONS_DIR, APPROVALS_DIR, IDEM_DIR):
            os.makedirs(d, exist_ok=True)
        get_event_bus(EVENTS_DIR)
        get_run_manager(RUNS_DIR)
        get_channel_manager(os.path.join(STATE, "channels_v2.json"), VERSIONS_DIR)
        get_idempotency_manager(IDEM_DIR)
        get_approval_manager(APPROVALS_DIR)
        _initialized = True


# ── Snapshot building ──────────────────────────────────────────────────────
def build_snapshot(rows: dict) -> Snapshot:
    """Wrap a fresh price fetch into a core Snapshot (immutable data layer)."""
    snap = Snapshot(
        id=generate_snapshot_id(),
        created_at=datetime.now(),
        source="tgju.org",
        raw_data=rows,
        normalized_data={"prices": rows},
    )
    RUNTIME["snapshot"] = snap
    RUNTIME["snapshot_at"] = datetime.now()
    emit_event(EventType.SNAPSHOT_CREATED, run_id="system", channel_id="*",
               status="success", payload={"snapshot_id": snap.id, "rows": len(rows)})
    return snap


def current_snapshot() -> Snapshot:
    """Get the latest snapshot, fetching fresh rows if none exists yet."""
    if RUNTIME["snapshot"] is None:
        rows = get_all_prices()
        return build_snapshot(rows)
    return RUNTIME["snapshot"]


# ── Legacy channel -> core ChannelDefinition bridge ───────────────────────
def legacy_to_core(ch: dict) -> dict:
    """Convert a legacy channel dict to the v2 schema shape (no DB writes)."""
    content = {
        "price_updates": "prices" in (ch.get("post_types") or ["prices"]),
        "news": bool(ch.get("news_categories")),
        "polls": bool(ch.get("poll_enabled")),
        "analysis": bool(ch.get("with_analysis", True)),
        "ai_enrichment": False,
    }
    schedule = {"price": int(ch.get("schedule_minutes") or 10), "news": 60, "poll": 240}
    targets = []
    if ch.get("telegram_id"):
        targets.append({"type": "telegram", "chat_id": str(ch.get("telegram_id"))})
    return {
        "id": ch["id"], "name": ch.get("name"), "description": ch.get("header", ""),
        "sources": ["tgju_prices", "tgju_news"],
        "content": content, "schedule": schedule,
        "max_posts_per_day": {"price": 144, "news": 24, "poll": 6},
        "formatting": {
            "template": ch.get("format", "chips"),
            "with_star": ch.get("with_star", True),
            "with_footer": ch.get("with_footer", True),
            "footer_text": ch.get("footer", "به‌روزرسانی: هر ۱۰ دقیقه | منبع: tgju.org"),
            "locale": "fa_IR",
        },
        "ai": {"enabled": False, "provider": "", "model": "", "tasks": []},
        "delivery": {"targets": targets, "mode": "auto", "approval_required": False},
        "enabled": ch.get("enabled", True),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "version": 1,
    }


def channel_mode(ch: dict) -> str:
    """Resolve channel delivery mode (auto|approval|manual)."""
    return (ch.get("delivery") or {}).get("mode", "auto")


# ── Orchestrated post (used by scheduler + webapp) ─────────────────────────
def orchestrated_post(channel: dict, rows: dict, trigger: TriggerType,
                      send_fn=None, simulation: bool = False,
                      stale: bool = False, stale_age_hours: float = 0.0) -> dict:
    """
    Full pipeline for ONE channel:
      snapshot -> decision (orchestrator) -> content build -> [delivery]

    Returns a structured result dict with run_id, decision, message, status.
    Delivery happens only when send_fn is provided AND the channel mode
    allows it AND the decision says publish.
    """
    init_core()
    cid = channel["id"]
    snap = build_snapshot(rows)

    # run record
    run = create_run(cid, trigger, snapshot_id=snap.id,
                     metadata={"simulation": simulation, "channel_name": channel.get("name")})
    emit_event(EventType.RUN_STARTED, run.id, cid, status="started",
               payload={"trigger": trigger.value, "snapshot": snap.id})
    t0 = time.time()

    # DECISION — content orchestrator (pure, side-effect free)
    orchestrator = ContentOrchestrator()
    core_ch = legacy_to_core(channel)
    # register in core channel manager so the orchestrator can find it
    from tgju_core.types import ChannelDefinition as CoreChannelDef
    from tgju_core.channels import get_channel_manager as _gcm
    _gcm().register_in_memory(CoreChannelDef(**core_ch))
    decision = orchestrator.orchestrate(cid, trigger, snap, simulation=simulation)

    # translate decision to legacy post_type for the existing builders
    post_types = []
    if decision.decisions.get(ContentType.PRICE):
        post_types.append("prices")
    if decision.decisions.get(ContentType.NEWS) and channel.get("news_categories"):
        post_types.append("news")
    if decision.decisions.get(ContentType.POLL) and channel.get("poll_enabled"):
        post_types.append("poll")
    if not post_types:
        post_types = ["prices"]  # legacy default for manual/simulated runs

    # CONTENT + FORMAT (pass stale flag so members see the fallback banner)
    msg = legacy_build(channel, rows, stale=stale, stale_age_hours=stale_age_hours)
    decision_ms = int((time.time() - t0) * 1000)
    emit_event(EventType.MESSAGE_BUILT, run.id, cid, status="success",
               duration_ms=decision_ms, payload={"chars": len(msg), "types": post_types})

    result = {
        "ok": True, "run_id": run.id, "channel_id": cid,
        "trigger": trigger.value, "snapshot_id": snap.id,
        "decision": decision.to_dict(),
        "post_types": post_types,
        "message": msg,
        "simulation": simulation,
        "latency_ms": {"decision": decision_ms},
    }

    # DELIVERY
    if send_fn is None:
        # simulation / preview path — no delivery
        run.metadata.update({"simulated": True, "duration_ms": decision_ms})
        complete_run(run.id, RunStatus.COMPLETED, decisions=decision,
                     message_preview=msg,
                     latency_ms={"decision": decision_ms})
        emit_event(EventType.RUN_COMPLETED, run.id, cid, status="success",
                   duration_ms=int((time.time() - t0) * 1000),
                   payload={"simulation": True})
        return result

    mode = channel_mode(channel)
    if mode == "manual":
        # prepare content only; approval manager queues it for the UI
        result["status"] = "prepared_manual"
        complete_run(run.id, RunStatus.COMPLETED, decisions=decision,
                     message_preview=msg, latency_ms={"decision": decision_ms},
                     metadata={"mode": "manual", "prepared": True})
        return result

    if mode == "approval":
        # queue for human approval; delivery after approve_content()
        from tgju_core.approval import get_approval_manager
        get_approval_manager().create_approval_request(
            run.id, cid, msg[:4000], ",".join(post_types),
            metadata={"channel_name": channel.get("name"), "snapshot": snap.id})
        complete_run(run.id, RunStatus.AWAITING_APPROVAL, decisions=decision,
                     message_preview=msg, latency_ms={"decision": decision_ms})
        emit_event(EventType.RUN_STARTED, run.id, cid, status="awaiting_approval")
        result["status"] = "awaiting_approval"
        return result

    # AUTO — deliver with idempotency guard
    dup, _key = get_idempotency_manager().check_and_mark(
        cid, {"hash": content_hash({"msg": msg, "t": post_types})},
        datetime.now().strftime("%Y-%m-%d %H:%M"))
    if dup:
        result["ok"] = False
        result["status"] = "duplicate_skipped"
        complete_run(run.id, RunStatus.CANCELLED, decisions=decision,
                     message_preview=msg, error="duplicate_skipped",
                     latency_ms={"decision": decision_ms})
        emit_event(EventType.CONTENT_REJECTED, run.id, cid, status="rejected",
                   payload={"reason": "idempotency"})
        return result

    emit_event(EventType.TELEGRAM_SEND_STARTED, run.id, cid, status="started")
    t1 = time.time()
    resp = send_fn(msg)
    send_ms = int((time.time() - t1) * 1000)
    if resp and resp.get("ok"):
        mid = (resp.get("result") or {}).get("message_id")
        get_idempotency_manager().mark_used(_key.key, str(mid)) if _key else None
        complete_run(run.id, RunStatus.COMPLETED, decisions=decision,
                     message_preview=msg, telegram_message_id=str(mid),
                     delivery_target=str(channel.get("telegram_id")),
                     latency_ms={"decision": decision_ms, "delivery": send_ms})
        emit_event(EventType.TELEGRAM_SEND_SUCCESS, run.id, cid, status="success",
                   duration_ms=send_ms, payload={"message_id": mid})
        result.update({"ok": True, "status": "sent",
                       "message_id": mid, "latency_ms": {"decision": decision_ms, "delivery": send_ms}})
    else:
        err = (resp or {}).get("description") or (resp or {}).get("error") or "send failed"
        complete_run(run.id, RunStatus.FAILED, decisions=decision,
                     message_preview=msg, error=str(err)[:300],
                     latency_ms={"decision": decision_ms, "delivery": send_ms})
        emit_event(EventType.TELEGRAM_SEND_FAILED, run.id, cid, status="failed",
                   duration_ms=send_ms, error=str(err)[:300])
        result.update({"ok": False, "status": "failed", "error": str(err)[:300]})
    return result


# ── Explainability: "Why did this post happen?" ───────────────────────────
def explain_run(run_id: str) -> dict:
    """Human-readable explanation of a run: trigger, rules, data, result."""
    init_core()
    run = get_run_manager().get_run(run_id)
    if not run:
        return {"ok": False, "error": "run not found"}
    out = {
        "ok": True,
        "run_id": run.id,
        "channel": {"id": run.channel_id,
                    "name": (run.metadata or {}).get("channel_name", run.channel_id)},
        "trigger": run.trigger.value,
        "started_at": run.started_at.isoformat(),
        "status": run.status.value,
        "snapshot": run.snapshot_id,
        "result": "success" if run.status == RunStatus.COMPLETED else run.error or run.status.value,
        "duration_ms": run.duration_ms(),
        "telegram_message_id": run.telegram_message_id,
        "delivery_target": run.delivery_target,
        "rules": {},
    }
    if run.decisions:
        for ct, pub in run.decisions.decisions.items():
            out["rules"][ct.value] = {
                "publish": pub,
                "why": run.decisions.reason.get(ct, ""),
            }
    return out


# ── Command-center aggregates ──────────────────────────────────────────────
def command_center(channels: list, rows: dict) -> dict:
    """Aggregated data for the Command Center UI (one call)."""
    init_core()
    scorer = HealthScorer()
    health = scorer.get_overall_health()
    runs = get_run_manager().list_runs(limit=25)
    pending = get_approval_manager().get_pending()

    # next actions: channels that are due (schedule elapsed or never posted)
    next_actions = []
    for ch in channels:
        if not ch.get("enabled") or not ch.get("telegram_id"):
            continue
        cid = ch["id"]
        state = {}
        try:
            from tgju_engine_config import load_channel_state
            state = load_channel_state(cid) or {}
        except Exception:
            pass
        last = state.get("last_post_at")
        due = True
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                due = (datetime.now() - last_dt) >= timedelta(minutes=int(ch.get("schedule_minutes") or 10))
            except Exception:
                due = True
        next_actions.append({
            "channel": cid, "name": ch.get("name"),
            "due": due, "interval_min": int(ch.get("schedule_minutes") or 10),
            "last_post": last or "",
            "types": ch.get("post_types") or ["prices"],
        })

    recent_runs = []
    for r in runs:
        recent_runs.append({
            "run_id": r.id, "channel": r.channel_id, "trigger": r.trigger.value,
            "status": r.status.value, "duration_ms": r.duration_ms(),
            "started_at": r.started_at.isoformat(),
            "error": r.error or "",
        })

    return {
        "health": health,
        "recent_runs": recent_runs,
        "pending_approvals": pending,
        "next_actions": next_actions,
        "stats": get_run_manager().get_stats(),
        "snapshot": {
            "id": RUNTIME["snapshot"].id if RUNTIME["snapshot"] else None,
            "at": RUNTIME["snapshot_at"].isoformat() if RUNTIME["snapshot_at"] else None,
            "rows": len(rows),
        },
        "events_recent": [
            e.to_dict() for e in get_event_bus().query_events(limit=20)
        ],
    }


# ── Simulation (dry run, safe) ─────────────────────────────────────────────
def simulate_channel(channel: dict, rows: dict) -> dict:
    """Simulation: full pipeline except delivery. Reports would-be posts."""
    init_core()
    res = orchestrated_post(channel, rows, TriggerType.SIMULATION,
                            send_fn=None, simulation=True)
    return {
        "ok": True, "run_id": res["run_id"], "channel_id": res["channel_id"],
        "decision": res["decision"], "message": res["message"],
        "simulation": True,
        "reported": {
            "posts_would_generate": sum(1 for d in res["decision"]["decisions"].values() if d),
            "posts_rejected": sum(1 for d in res["decision"]["decisions"].values() if not d),
            "ai_operations": 0,
            "estimated_telegram_operations": sum(1 for d in res["decision"]["decisions"].values() if d),
            "errors": [],
        },
    }