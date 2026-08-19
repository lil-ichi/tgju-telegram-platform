# -*- coding: utf-8 -*-
"""
Simulation / Dry Run Mode - Safe testing without publishing.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional

from .types import (
    ContentType, TriggerType, ChannelDefinition, Snapshot,
    OrchestrationDecision, ContentItem, RunStatus
)
from .channels import get_channel_manager
from .orchestrator import ContentOrchestrator, orchestrate_channel
from .runs import create_run, complete_run, RunStatus as RunStatusType
from .events import emit_event, EventType


class SimulationRunner:
    """Runs the full pipeline in simulation mode (no Telegram delivery)."""
    
    def __init__(self):
        self.channel_manager = get_channel_manager()
        self.orchestrator = ContentOrchestrator()
    
    def simulate_channel(
        self,
        channel_id: str,
        snapshot: Snapshot,
        trigger: TriggerType = TriggerType.SIMULATION
    ) -> Dict[str, Any]:
        """
        Run full simulation for a channel.
        
        Returns detailed simulation result.
        """
        channel = self.channel_manager.get_channel(channel_id)
        if not channel:
            return {"error": f"Channel {channel_id} not found", "success": False}
        
        # Create simulation run
        run = create_run(
            channel_id=channel_id,
            trigger=trigger,
            snapshot_id=snapshot.id,
            metadata={"simulation": True}
        )
        
        start_time = datetime.now()
        latency = {}
        
        try:
            # Stage 1: Orchestration
            stage_start = datetime.now()
            decision = self.orchestrator.orchestrate(channel_id, trigger, snapshot, simulation=True)
            latency["orchestration_ms"] = int((datetime.now() - stage_start).total_seconds() * 1000)
            
            # Stage 2: Content Selection
            stage_start = datetime.now()
            content_items = self._build_content_items(channel, decision, snapshot)
            latency["content_selection_ms"] = int((datetime.now() - stage_start).total_seconds() * 1000)
            
            # Stage 3: Message Building (formatting)
            stage_start = datetime.now()
            previews = self._build_previews(channel, content_items, snapshot)
            latency["formatting_ms"] = int((datetime.now() - stage_start).total_seconds() * 1000)
            
            # Stage 4: AI (if enabled)
            stage_start = datetime.now()
            ai_operations = 0
            if channel.ai.get("enabled", False):
                ai_operations = self._simulate_ai(channel, content_items, snapshot)
            latency["ai_ms"] = int((datetime.now() - stage_start).total_seconds() * 1000)
            
            # Complete simulation run
            total_latency = int((datetime.now() - start_time).total_seconds() * 1000)
            latency["total_ms"] = total_latency
            
            complete_run(
                run_id=run.id,
                status=RunStatus.COMPLETED,
                decisions=decision,
                content_items=content_items,
                message_preview="\n\n---\n\n".join(previews.values()),
                latency_ms=latency,
            )
            
            # Build result
            result = {
                "success": True,
                "run_id": run.id,
                "channel": {"id": channel.id, "name": channel.name},
                "trigger": trigger.value,
                "snapshot": snapshot.id,
                "channels_evaluated": 1,
                "posts_would_generate": len([d for d in decision.decisions.values() if d]),
                "posts_rejected": len([d for d in decision.decisions.values() if not d]),
                "ai_operations": ai_operations,
                "estimated_telegram_operations": len([d for d in decision.decisions.values() if d]),
                "latency": latency,
                "decisions": {k.value: v for k, v in decision.decisions.items()},
                "reasons": {k.value: v for k, v in decision.reason.items()},
                "previews": previews,
                "errors": [],
                "simulated_at": datetime.now().isoformat(),
            }
            
            emit_event(
                EventType.RUN_COMPLETED,
                run_id=run.id,
                channel_id=channel_id,
                status="success",
                duration_ms=total_latency,
                payload={"simulation": True, **result}
            )
            
            return result
            
        except Exception as e:
            latency["total_ms"] = int((datetime.now() - start_time).total_seconds() * 1000)
            complete_run(
                run_id=run.id,
                status=RunStatus.FAILED,
                latency_ms=latency,
                error=str(e),
            )
            
            return {
                "success": False,
                "run_id": run.id,
                "error": str(e),
                "latency": latency,
            }
    
    def simulate_all(
        self,
        snapshot: Snapshot,
        trigger: TriggerType = TriggerType.SIMULATION
    ) -> Dict[str, Any]:
        """Simulate all enabled channels."""
        channels = self.channel_manager.get_enabled_channels()
        
        results = []
        total_posts = 0
        total_rejected = 0
        total_ai = 0
        total_telegram = 0
        errors = []
        
        for channel in channels:
            result = self.simulate_channel(channel.id, snapshot, trigger)
            results.append(result)
            
            if result.get("success"):
                total_posts += result.get("posts_would_generate", 0)
                total_rejected += result.get("posts_rejected", 0)
                total_ai += result.get("ai_operations", 0)
                total_telegram += result.get("estimated_telegram_operations", 0)
            else:
                errors.append({"channel": channel.id, "error": result.get("error")})
        
        return {
            "success": len(errors) == 0,
            "channels_evaluated": len(channels),
            "results": results,
            "summary": {
                "posts_would_generate": total_posts,
                "posts_rejected": total_rejected,
                "ai_operations": total_ai,
                "estimated_telegram_operations": total_telegram,
                "errors": len(errors),
            },
            "errors": errors,
            "simulated_at": datetime.now().isoformat(),
        }
    
    def _build_content_items(
        self,
        channel: ChannelDefinition,
        decision: OrchestrationDecision,
        snapshot: Snapshot
    ) -> List[ContentItem]:
        """Build content items for approved decisions."""
        items = []
        
        for content_type, should_publish in decision.decisions.items():
            if not should_publish:
                continue
            
            # Extract relevant data from snapshot for this content type
            data = self._extract_content_data(snapshot, content_type, channel)
            
            item = ContentItem(
                id=f"sim_{datetime.now().strftime('%Y%m%d%H%M%S')}_{content_type.value}",
                type=content_type,
                channel_id=channel.id,
                snapshot_id=snapshot.id,
                data=data,
                metadata={"simulation": True},
            )
            items.append(item)
        
        return items
    
    def _extract_content_data(
        self,
        snapshot: Snapshot,
        content_type: ContentType,
        channel: ChannelDefinition
    ) -> Dict[str, Any]:
        """Extract content-type specific data from snapshot."""
        raw = snapshot.raw_data or {}
        norm = snapshot.normalized_data or {}
        
        if content_type == ContentType.PRICE:
            # Get prices for channel's slug groups
            prices = norm.get("prices", raw.get("prices", {}))
            return {"prices": prices, "channel_slugs": self._get_channel_slugs(channel)}
        
        elif content_type == ContentType.NEWS:
            articles = norm.get("articles", raw.get("articles", []))
            return {"articles": articles[:5], "categories": channel.content.get("news", [])}
        
        elif content_type == ContentType.POLL:
            return {"poll_pool": "default", "channel_id": channel.id}
        
        elif content_type == ContentType.ANALYSIS:
            prices = norm.get("prices", raw.get("prices", {}))
            return {"prices": prices, "channel_slugs": self._get_channel_slugs(channel)}
        
        elif content_type == ContentType.AI_ENRICHED:
            prices = norm.get("prices", raw.get("prices", {}))
            return {"prices": prices, "tasks": channel.ai.get("tasks", [])}
        
        return {}
    
    def _get_channel_slugs(self, channel: ChannelDefinition) -> List[str]:
        """Get slugs from channel config (legacy compatibility)."""
        # This would be stored in channel metadata
        return []
    
    def _build_previews(
        self,
        channel: ChannelDefinition,
        content_items: List[ContentItem],
        snapshot: Snapshot
    ) -> Dict[str, str]:
        """Build message previews for each content item."""
        previews = {}
        
        for item in content_items:
            # In real implementation, this would call the formatter
            # For simulation, generate a basic preview
            if item.type == ContentType.PRICE:
                previews[item.type.value] = f"[PREVIEW] Price update for {channel.name} - {len(item.data.get('prices', {}))} items"
            elif item.type == ContentType.NEWS:
                previews[item.type.value] = f"[PREVIEW] News for {channel.name} - {len(item.data.get('articles', []))} articles"
            elif item.type == ContentType.POLL:
                previews[item.type.value] = f"[PREVIEW] Poll for {channel.name}"
            elif item.type == ContentType.ANALYSIS:
                previews[item.type.value] = f"[PREVIEW] Analysis for {channel.name}"
            elif item.type == ContentType.AI_ENRICHED:
                previews[item.type.value] = f"[PREVIEW] AI-enriched content for {channel.name}"
        
        return previews
    
    def _simulate_ai(
        self,
        channel: ChannelDefinition,
        content_items: List[ContentItem],
        snapshot: Snapshot
    ) -> int:
        """Simulate AI operations."""
        if not channel.ai.get("enabled", False):
            return 0
        
        tasks = channel.ai.get("tasks", [])
        return len(tasks) * len(content_items)


# Convenience function
def run_simulation(channel_id: str, snapshot: Snapshot) -> Dict[str, Any]:
    """Run simulation for a channel."""
    runner = SimulationRunner()
    return runner.simulate_channel(channel_id, snapshot)


def run_full_simulation(snapshot: Snapshot) -> Dict[str, Any]:
    """Run simulation for all channels."""
    runner = SimulationRunner()
    return runner.simulate_all(snapshot)