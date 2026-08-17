# -*- coding: utf-8 -*-
"""
TGJU Core Architecture
======================
Multi-layer architecture for content automation:
- Data Layer: Sources, parsing, normalization, caching
- Decision Layer: Orchestrator, content selection, scheduling
- Content Layer: Message building, formatting, AI enrichment
- Format Layer: Template rendering, channel-specific formatting
- Delivery Layer: Telegram, Web, X, Email, etc.
- Observability Layer: Runs, events, health, audit trail
"""
from .types import *
from .events import *
from .runs import *
from .channels import *
from .orchestrator import *
from .health import *
from .secrets import *
from .idempotency import *
from .simulation import *
from .approval import *

__all__ = [
    # Types
    "ContentType", "TriggerType", "ChannelMode", "DeliveryTarget",
    "RunStatus", "EventType", "HealthComponent",
    "Snapshot", "ContentItem", "ChannelConfig", "ChannelDefinition", "RunRecord",
    "EventRecord", "HealthScore", "IdempotencyKey",
    # Events
    "EventBus", "emit_event", "subscribe", "get_event_bus",
    # Runs
    "RunManager", "create_run", "complete_run", "get_run", "get_run_manager",
    # Channels
    "ChannelManager", "load_channels", "save_channels", "get_channel",
    # Orchestrator
    "ContentOrchestrator", "OrchestrationDecision", "orchestrate_channel",
    # Health
    "HealthScorer", "calculate_health",
    # Secrets
    "SecretsManager", "get_secret", "set_secret", "get_secrets_manager",
    # Idempotency
    "IdempotencyManager", "check_idempotent", "mark_idempotent", "get_idempotency_manager",
    # Simulation
    "SimulationRunner", "run_simulation", "run_full_simulation",
    # Approval
    "ApprovalManager", "request_approval", "approve_content", "reject_content", "get_pending_approvals", "get_approval_manager",
]