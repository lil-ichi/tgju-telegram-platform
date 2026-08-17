# -*- coding: utf-8 -*-
"""
Content Orchestrator - The decision engine.
Decides WHAT content each channel should publish based on system state.
"""
from __future__ import annotations
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from .types import (
    ContentType, TriggerType, ChannelDefinition, OrchestrationDecision,
    Snapshot, RunStatus, generate_content_id
)
from .channels import get_channel_manager
from .runs import get_run_manager
from .events import emit_event, EventType


class ContentOrchestrator:
    """
    The brain of the content automation system.
    
    Given:
    - Current system state (snapshots, channel configs, history)
    - Channel configuration (what content types enabled, schedule, limits)
    
    Decides:
    - What content types should be published NOW
    - Why each decision was made (explainability)
    """
    
    def __init__(self, channel_manager=None, run_manager=None):
        self.channel_manager = channel_manager or get_channel_manager()
        self.run_manager = run_manager or get_run_manager()
    
    def orchestrate(
        self,
        channel_id: str,
        trigger: TriggerType,
        snapshot: Snapshot,
        simulation: bool = False
    ) -> OrchestrationDecision:
        """
        Main orchestration logic.
        
        Returns an OrchestrationDecision with:
        - decisions: Dict[ContentType, bool] - what to publish
        - reason: Dict[ContentType, str] - human-readable why
        """
        channel = self.channel_manager.get_channel(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")
        
        if not channel.enabled:
            return self._empty_decision(channel_id, trigger, snapshot.id, "Channel disabled")
        
        now = datetime.now()
        decisions = {}
        reasons = {}
        
        # Check each content type
        for content_type in ContentType:
            should_publish, reason = self._evaluate_content_type(
                channel, content_type, trigger, snapshot, now
            )
            decisions[content_type] = should_publish
            reasons[content_type] = reason
            
            # Emit decision event
            emit_event(
                EventType.CONTENT_SELECTED if should_publish else EventType.CONTENT_REJECTED,
                run_id=f"orch_{channel_id}_{now.strftime('%Y%m%d%H%M%S')}",
                channel_id=channel_id,
                status="success",
                payload={
                    "content_type": content_type.value,
                    "reason": reason,
                    "trigger": trigger.value,
                    "simulation": simulation,
                }
            )
        
        decision = OrchestrationDecision(
            run_id=f"orch_{channel_id}_{now.strftime('%Y%m%d%H%M%S')}",
            channel_id=channel_id,
            trigger=trigger,
            decisions=decisions,
            reason=reasons,
            snapshot_id=snapshot.id,
            schedule_info=self._get_schedule_info(channel, now),
        )
        
        return decision
    
    def _evaluate_content_type(
        self,
        channel: ChannelDefinition,
        content_type: ContentType,
        trigger: TriggerType,
        snapshot: Snapshot,
        now: datetime
    ) -> tuple[bool, str]:
        """Evaluate whether a specific content type should be published."""
        
        # Map content type to channel config keys
        config_map = {
            ContentType.PRICE: "price_updates",
            ContentType.NEWS: "news",
            ContentType.POLL: "polls",
            ContentType.ANALYSIS: "analysis",
            ContentType.AI_ENRICHED: "ai_enrichment",
        }
        
        config_key = config_map.get(content_type)
        if not config_key:
            return False, f"Unknown content type: {content_type}"
        
        # 1. Check if content type is enabled in channel config
        if not channel.content.get(config_key, False):
            return False, f"{content_type.value} disabled in channel config"
        
        # 2. Check schedule (time since last post of this type)
        schedule_key = content_type.value if content_type != ContentType.AI_ENRICHED else "analysis"
        interval_minutes = channel.schedule.get(schedule_key, 10)
        if not self._is_schedule_due(channel_id=channel.id, content_type=content_type, interval_minutes=interval_minutes):
            return False, f"Schedule not due (interval: {interval_minutes}min)"
        
        # 3. Check daily limit
        if not self._check_daily_limit(channel.id, content_type, channel.max_posts_per_day.get(schedule_key, 999)):
            return False, "Daily limit reached"
        
        # 4. Check data availability for this content type
        if not self._has_data_for_type(snapshot, content_type):
            return False, f"No data available for {content_type.value}"
        
        # 5. Check trigger compatibility
        if not self._is_trigger_compatible(trigger, content_type):
            return False, f"Trigger {trigger.value} not compatible with {content_type.value}"
        
        return True, f"All conditions met for {content_type.value}"
    
    def _is_schedule_due(self, channel_id: str, content_type: ContentType, interval_minutes: int) -> bool:
        """Check if enough time has passed since last post of this type."""
        runs = self.run_manager.get_channel_history(channel_id, limit=100)
        
        # Find last successful run that published this content type
        for run in runs:
            if run.status != RunStatus.COMPLETED or not run.decisions:
                continue
            if run.decisions.decisions.get(content_type, False):
                if run.completed_at:
                    elapsed = datetime.now() - run.completed_at
                    return elapsed >= timedelta(minutes=interval_minutes)
        
        # No previous post of this type - it's due
        return True
    
    def _check_daily_limit(self, channel_id: str, content_type: ContentType, limit: int) -> bool:
        """Check if daily post limit for this content type is reached."""
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        runs = self.run_manager.list_runs(
            channel_id=channel_id,
            status=RunStatus.COMPLETED,
            since=today_start,
            limit=500
        )
        
        count = 0
        for run in runs:
            if run.decisions and run.decisions.decisions.get(content_type, False):
                count += 1
        
        return count < limit
    
    def _has_data_for_type(self, snapshot: Snapshot, content_type: ContentType) -> bool:
        """Check if snapshot has data for this content type."""
        data = snapshot.normalized_data or snapshot.raw_data
        
        if content_type == ContentType.PRICE:
            return bool(data.get("prices") or data.get("rows"))
        elif content_type == ContentType.NEWS:
            return bool(data.get("articles") or data.get("news"))
        elif content_type == ContentType.POLL:
            return True  # Polls always available (from pool)
        elif content_type == ContentType.ANALYSIS:
            return bool(data.get("analysis") or data.get("prices"))
        elif content_type == ContentType.AI_ENRICHED:
            return bool(data.get("prices"))  # Needs base data
        
        return True
    
    def _is_trigger_compatible(self, trigger: TriggerType, content_type: ContentType) -> bool:
        """Check if trigger type is compatible with content type."""
        # Scheduler can trigger anything
        if trigger == TriggerType.SCHEDULER:
            return True
        # Manual can trigger anything
        if trigger == TriggerType.MANUAL:
            return True
        # Webhook typically for news/alerts
        if trigger == TriggerType.WEBHOOK:
            return content_type in (ContentType.NEWS, ContentType.ANALYSIS, ContentType.AI_ENRICHED)
        # Approval follows original trigger
        if trigger == TriggerType.APPROVAL:
            return True
        # Simulation can do anything
        if trigger == TriggerType.SIMULATION:
            return True
        return True
    
    def _get_schedule_info(self, channel: ChannelDefinition, now: datetime) -> Dict[str, Any]:
        """Get schedule information for the decision."""
        return {
            "intervals": channel.schedule,
            "max_per_day": channel.max_posts_per_day,
            "next_due": self._calculate_next_due(channel, now),
        }
    
    def _calculate_next_due(self, channel: ChannelDefinition, now: datetime) -> Dict[str, str]:
        """Calculate next due time for each content type."""
        next_due = {}
        runs = self.run_manager.get_channel_history(channel.id, limit=50)
        
        for content_type in ContentType:
            schedule_key = content_type.value if content_type != ContentType.AI_ENRICHED else "analysis"
            interval = channel.schedule.get(schedule_key, 10)
            
            last_run = None
            for run in runs:
                if run.status == RunStatus.COMPLETED and run.decisions:
                    if run.decisions.decisions.get(content_type, False):
                        last_run = run.completed_at
                        break
            
            if last_run:
                next_time = last_run + timedelta(minutes=interval)
            else:
                next_time = now
            
            next_due[content_type.value] = next_time.isoformat()
        
        return next_due
    
    def _empty_decision(
        self,
        channel_id: str,
        trigger: TriggerType,
        snapshot_id: str,
        reason: str
    ) -> OrchestrationDecision:
        """Create an empty decision (nothing to publish)."""
        return OrchestrationDecision(
            run_id=f"orch_{channel_id}_empty",
            channel_id=channel_id,
            trigger=trigger,
            decisions={ct: False for ct in ContentType},
            reason={ct: reason for ct in ContentType},
            snapshot_id=snapshot_id,
        )
    
    def simulate(self, channel_id: str, snapshot: Snapshot) -> OrchestrationDecision:
        """Run orchestration in simulation mode (no side effects)."""
        return self.orchestrate(channel_id, TriggerType.SIMULATION, snapshot, simulation=True)
    
    def get_explanation(self, decision: OrchestrationDecision) -> Dict[str, Any]:
        """Generate human-readable explanation for a decision."""
        channel = self.channel_manager.get_channel(decision.channel_id)
        if not channel:
            return {"error": "Channel not found"}
        
        explanation = {
            "channel": {"id": channel.id, "name": channel.name},
            "trigger": decision.trigger.value,
            "snapshot": decision.snapshot_id,
            "timestamp": decision.created_at.isoformat(),
            "decisions": {},
        }
        
        for content_type, should_publish in decision.decisions.items():
            explanation["decisions"][content_type.value] = {
                "publish": should_publish,
                "reason": decision.reason.get(content_type, ""),
                "enabled_in_config": channel.content.get(
                    {ContentType.PRICE: "price_updates",
                     ContentType.NEWS: "news",
                     ContentType.POLL: "polls",
                     ContentType.ANALYSIS: "analysis",
                     ContentType.AI_ENRICHED: "ai_enrichment"}.get(content_type, ""),
                    False
                ),
                "schedule_interval_min": channel.schedule.get(
                    content_type.value if content_type != ContentType.AI_ENRICHED else "analysis",
                    10
                ),
            }
        
        return explanation


# Convenience function
def orchestrate_channel(
    channel_id: str,
    trigger: TriggerType,
    snapshot: Snapshot,
    simulation: bool = False
) -> OrchestrationDecision:
    """Orchestrate content for a channel."""
    orchestrator = ContentOrchestrator()
    return orchestrator.orchestrate(channel_id, trigger, snapshot, simulation)