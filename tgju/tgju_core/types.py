# -*- coding: utf-8 -*-
"""
Core type definitions for TGJU Content Automation Platform.
"""
from __future__ import annotations
import enum
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional, Dict, List, Literal
import json


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════

class ContentType(str, enum.Enum):
    """Types of content that can be published."""
    PRICE = "price"
    NEWS = "news"
    POLL = "poll"
    ANALYSIS = "analysis"
    AI_ENRICHED = "ai_enriched"


class TriggerType(str, enum.Enum):
    """What triggered a run."""
    SCHEDULER = "scheduler"
    MANUAL = "manual"
    WEBHOOK = "webhook"
    APPROVAL = "approval"
    SIMULATION = "simulation"


class ChannelMode(str, enum.Enum):
    """Channel publishing mode."""
    AUTO = "auto"           # Fully automatic
    APPROVAL = "approval"   # Requires human approval
    MANUAL = "manual"       # Only prepares content


class DeliveryTarget(str, enum.Enum):
    """Delivery destinations."""
    TELEGRAM = "telegram"
    WEB = "web"
    X = "x"
    INSTAGRAM = "instagram"
    EMAIL = "email"


class RunStatus(str, enum.Enum):
    """Execution run status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_APPROVAL = "awaiting_approval"


class EventType(str, enum.Enum):
    """Structured event types for observability."""
    # Source events
    SOURCE_FETCH_STARTED = "source_fetch_started"
    SOURCE_FETCH_COMPLETED = "source_fetch_completed"
    SOURCE_FETCH_FAILED = "source_fetch_failed"
    
    # Snapshot events
    SNAPSHOT_CREATED = "snapshot_created"
    SNAPSHOT_EXPIRED = "snapshot_expired"
    
    # Backfill events
    BACKFILL_STARTED = "backfill_started"
    BACKFILL_COMPLETED = "backfill_completed"
    BACKFILL_FAILED = "backfill_failed"
    
    # Decision events
    CONTENT_SELECTED = "content_selected"
    CONTENT_REJECTED = "content_rejected"
    
    # Format events
    MESSAGE_BUILT = "message_built"
    PREVIEW_GENERATED = "preview_generated"
    
    # Delivery events
    TELEGRAM_SEND_STARTED = "telegram_send_started"
    TELEGRAM_SEND_SUCCESS = "telegram_send_success"
    TELEGRAM_SEND_FAILED = "telegram_send_failed"
    
    # AI events
    AI_STARTED = "ai_started"
    AI_COMPLETED = "ai_completed"
    AI_FAILED = "ai_failed"
    
    # Run lifecycle
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class HealthComponent(str, enum.Enum):
    """System health components."""
    DATA = "data"
    CACHE = "cache"
    CHANNELS = "channels"
    TELEGRAM = "telegram"
    SCHEDULER = "scheduler"
    AI = "ai"
    SECRETS = "secrets"


# ═══════════════════════════════════════════════════════════════════════════
# CORE DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Snapshot:
    """Immutable data snapshot at a point in time."""
    id: str
    created_at: datetime
    source: str                    # e.g., "tgju.org"
    raw_data: Dict[str, Any]       # Raw parsed rows
    normalized_data: Dict[str, Any] # Normalized/validated data
    metadata: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        if self.expires_at:
            d["expires_at"] = self.expires_at.isoformat()
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Snapshot":
        d = d.copy()
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        if d.get("expires_at"):
            d["expires_at"] = datetime.fromisoformat(d["expires_at"])
        return cls(**d)


@dataclass
class ContentItem:
    """A piece of content ready for formatting."""
    id: str
    type: ContentType
    channel_id: str
    snapshot_id: str
    data: Dict[str, Any]           # The actual content data
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["created_at"] = self.created_at.isoformat()
        return d


@dataclass
class OrchestrationDecision:
    """Decision from the orchestrator about what to publish."""
    run_id: str
    channel_id: str
    trigger: TriggerType
    decisions: Dict[ContentType, bool]  # content_type -> should_publish
    reason: Dict[ContentType, str]      # content_type -> human-readable reason
    snapshot_id: str
    schedule_info: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["trigger"] = self.trigger.value
        d["decisions"] = {k.value: v for k, v in self.decisions.items()}
        d["reason"] = {k.value: v for k, v in self.reason.items()}
        d["created_at"] = self.created_at.isoformat()
        return d


@dataclass
class ChannelDefinition:
    """Complete channel configuration (v2 schema)."""
    id: str
    name: str
    description: str = ""
    
    # Sources this channel draws from
    sources: List[str] = field(default_factory=list)  # e.g., ["tgju_prices", "tgju_news"]
    
    # Content configuration
    content: Dict[str, Any] = field(default_factory=lambda: {
        "price_updates": True,
        "news": True,
        "polls": False,
        "analysis": False,
        "ai_enrichment": False,
    })
    
    # Schedule per content type (minutes)
    schedule: Dict[str, int] = field(default_factory=lambda: {
        "price": 10,
        "news": 60,
        "poll": 240,
    })
    
    # Max posts per day per type
    max_posts_per_day: Dict[str, int] = field(default_factory=lambda: {
        "price": 144,  # 24h * 6 = 144 at 10min intervals
        "news": 24,
        "poll": 6,
    })
    
    # Formatting
    formatting: Dict[str, Any] = field(default_factory=lambda: {
        "template": "currency_chips",
        "with_star": True,
        "with_footer": True,
        "footer_text": "به‌روزرسانی: هر ۱۰ دقیقه | منبع: tgju.org",
        "locale": "fa_IR",
    })
    
    # AI configuration
    ai: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "provider": "",
        "model": "",
        "tasks": [],  # ["summarize", "headline", "categorize"]
    })
    
    # Delivery
    delivery: Dict[str, Any] = field(default_factory=lambda: {
        "targets": [{"type": "telegram", "chat_id": ""}],
        "mode": "auto",
        "approval_required": False,
    })
    
    # Metadata
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    version: int = 1
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChannelDefinition":
        d = d.copy()
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        d["updated_at"] = datetime.fromisoformat(d["updated_at"])
        return cls(**d)
    
    def get_delivery_targets(self) -> List[Dict[str, Any]]:
        """Get active delivery targets."""
        return [t for t in self.delivery.get("targets", []) if t.get("chat_id")]
    
    def get_mode(self) -> ChannelMode:
        mode_str = self.delivery.get("mode", "auto")
        return ChannelMode(mode_str)


@dataclass
class RunRecord:
    """Execution run record with full traceability."""
    id: str
    channel_id: str
    trigger: TriggerType
    status: RunStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    
    # Data references
    snapshot_id: str = ""
    decisions: Optional[OrchestrationDecision] = None
    content_items: List[ContentItem] = field(default_factory=list)
    
    # Execution details
    message_preview: str = ""
    telegram_message_id: Optional[str] = None
    delivery_target: str = ""
    
    # Metrics
    latency_ms: Dict[str, int] = field(default_factory=dict)  # stage -> ms
    error: Optional[str] = None
    
    # Traceability
    parent_run_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["trigger"] = self.trigger.value
        d["status"] = self.status.value
        d["started_at"] = self.started_at.isoformat()
        if self.completed_at:
            d["completed_at"] = self.completed_at.isoformat()
        if self.decisions:
            d["decisions"] = self.decisions.to_dict()
        d["content_items"] = [c.to_dict() for c in self.content_items]
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunRecord":
        d = d.copy()
        d["trigger"] = TriggerType(d["trigger"])
        d["status"] = RunStatus(d["status"])
        d["started_at"] = datetime.fromisoformat(d["started_at"])
        if d.get("completed_at"):
            d["completed_at"] = datetime.fromisoformat(d["completed_at"])
        if d.get("decisions"):
            dec = d["decisions"]
            dec["trigger"] = TriggerType(dec["trigger"])
            dec["decisions"] = {ContentType(k): v for k, v in dec["decisions"].items()}
            dec["reason"] = {ContentType(k): v for k, v in dec["reason"].items()}
            dec["created_at"] = datetime.fromisoformat(dec["created_at"])
            d["decisions"] = OrchestrationDecision(**dec)
        d["content_items"] = [ContentItem(**c) for c in d.get("content_items", [])]
        return cls(**d)
    
    def duration_ms(self) -> Optional[int]:
        if self.completed_at:
            return int((self.completed_at - self.started_at).total_seconds() * 1000)
        return None


@dataclass
class EventRecord:
    """Structured event for observability."""
    id: str
    event_type: EventType
    run_id: str
    channel_id: str
    timestamp: datetime
    status: str  # "started", "success", "failed"
    duration_ms: Optional[int] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        d["timestamp"] = self.timestamp.isoformat()
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EventRecord":
        d = d.copy()
        d["event_type"] = EventType(d["event_type"])
        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        return cls(**d)


@dataclass
class HealthScore:
    """Health score for a system component."""
    component: HealthComponent
    score: float           # 0-100
    status: str            # "healthy", "degraded", "critical"
    details: Dict[str, Any] = field(default_factory=dict)
    last_check: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["component"] = self.component.value
        d["last_check"] = self.last_check.isoformat()
        return d


@dataclass
class IdempotencyKey:
    """Idempotency key for preventing duplicate delivery."""
    key: str
    channel_id: str
    content_hash: str
    scheduled_slot: str      # ISO timestamp of the scheduled slot
    created_at: datetime
    used: bool = False
    telegram_message_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        return d


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def generate_run_id() -> str:
    """Generate a unique run ID: YYYY-MM-DD-XXXX"""
    today = datetime.now().strftime("%Y-%m-%d")
    suffix = uuid.uuid4().hex[:4].upper()
    return f"{today}-{suffix}"


def generate_event_id() -> str:
    """Generate a unique event ID."""
    return f"evt_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def generate_snapshot_id() -> str:
    """Generate a unique snapshot ID."""
    return f"snap_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def generate_content_id() -> str:
    """Generate a unique content ID."""
    return f"cnt_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def generate_idempotency_key(channel_id: str, content_hash: str, scheduled_slot: str) -> str:
    """Generate idempotency key from components."""
    import hashlib
    raw = f"{channel_id}:{content_hash}:{scheduled_slot}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def content_hash(data: Dict[str, Any]) -> str:
    """Compute deterministic hash of content data."""
    import hashlib
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]