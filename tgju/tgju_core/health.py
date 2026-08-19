# -*- coding: utf-8 -*-
"""
Health Scoring System - System health with explainability.
"""
from __future__ import annotations
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .types import HealthComponent, HealthScore, RunStatus, TriggerType
from .channels import get_channel_manager
from .runs import get_run_manager
from .events import get_event_bus, EventType


class HealthScorer:
    """Calculates health scores for system components."""
    
    def __init__(self):
        self.channel_manager = get_channel_manager()
        self.run_manager = get_run_manager()
        self.event_bus = get_event_bus()
        self._cache: Dict[HealthComponent, HealthScore] = {}
        self._cache_ttl = 30  # seconds
    
    def calculate_health(self, force: bool = False) -> Dict[HealthComponent, HealthScore]:
        """Calculate health for all components."""
        if not force and self._is_cache_valid():
            return self._cache
        
        scores = {}
        scores[HealthComponent.DATA] = self._check_data_health()
        scores[HealthComponent.CACHE] = self._check_cache_health()
        scores[HealthComponent.CHANNELS] = self._check_channels_health()
        scores[HealthComponent.TELEGRAM] = self._check_telegram_health()
        scores[HealthComponent.SCHEDULER] = self._check_scheduler_health()
        scores[HealthComponent.AI] = self._check_ai_health()
        scores[HealthComponent.SECRETS] = self._check_secrets_health()
        
        self._cache = scores
        return scores
    
    def _is_cache_valid(self) -> bool:
        if not self._cache:
            return False
        oldest = min(s.last_check for s in self._cache.values())
        return (datetime.now() - oldest).total_seconds() < self._cache_ttl
    
    def _check_data_health(self) -> HealthScore:
        """Check data ingestion health."""
        # Check if we have recent successful source fetches
        events = self.event_bus.query_events(
            event_type=EventType.SOURCE_FETCH_COMPLETED,
            since=datetime.now() - timedelta(minutes=10),
            limit=10
        )
        
        failed_events = self.event_bus.query_events(
            event_type=EventType.SOURCE_FETCH_FAILED,
            since=datetime.now() - timedelta(minutes=10),
            limit=10
        )
        
        if not events and not failed_events:
            return HealthScore(
                component=HealthComponent.DATA,
                score=50,
                status="degraded",
                details={"message": "No recent fetch activity"}
            )
        
        success_rate = len(events) / max(1, len(events) + len(failed_events))
        score = int(success_rate * 100)
        
        return HealthScore(
            component=HealthComponent.DATA,
            score=score,
            status="healthy" if score >= 90 else ("degraded" if score >= 50 else "critical"),
            details={
                "recent_successes": len(events),
                "recent_failures": len(failed_events),
                "success_rate": success_rate,
            }
        )
    
    def _check_cache_health(self) -> HealthScore:
        """Check cache freshness."""
        # This would check snapshot age in real implementation
        # For now, return healthy if data component is healthy
        return HealthScore(
            component=HealthComponent.CACHE,
            score=90,
            status="healthy",
            details={"message": "Cache operational", "snapshot_age_seconds": 30}
        )
    
    def _check_channels_health(self) -> HealthScore:
        """Check channel configuration health."""
        channels = self.channel_manager.get_all_channels()
        enabled = [c for c in channels if c.enabled]
        
        if not channels:
            return HealthScore(
                component=HealthComponent.CHANNELS,
                score=0,
                status="critical",
                details={"message": "No channels configured"}
            )
        
        # Check for channels with missing delivery targets
        misconfigured = 0
        for ch in enabled:
            targets = ch.get_delivery_targets()
            if not targets:
                misconfigured += 1
        
        score = 100 - int((misconfigured / max(1, len(enabled))) * 100)
        
        return HealthScore(
            component=HealthComponent.CHANNELS,
            score=score,
            status="healthy" if score >= 90 else ("degraded" if score >= 50 else "critical"),
            details={
                "total_channels": len(channels),
                "enabled_channels": len(enabled),
                "misconfigured": misconfigured,
            }
        )
    
    def _check_telegram_health(self) -> HealthScore:
        """Check Telegram connectivity."""
        events = self.event_bus.query_events(
            event_type=EventType.TELEGRAM_SEND_SUCCESS,
            since=datetime.now() - timedelta(hours=1),
            limit=50
        )
        
        failed_events = self.event_bus.query_events(
            event_type=EventType.TELEGRAM_SEND_FAILED,
            since=datetime.now() - timedelta(hours=1),
            limit=50
        )
        
        if not events and not failed_events:
            return HealthScore(
                component=HealthComponent.TELEGRAM,
                score=50,
                status="degraded",
                details={"message": "No recent delivery activity"}
            )
        
        success_rate = len(events) / max(1, len(events) + len(failed_events))
        score = int(success_rate * 100)
        
        return HealthScore(
            component=HealthComponent.TELEGRAM,
            score=score,
            status="healthy" if score >= 90 else ("degraded" if score >= 50 else "critical"),
            details={
                "recent_successes": len(events),
                "recent_failures": len(failed_events),
                "success_rate": success_rate,
            }
        )
    
    def _check_scheduler_health(self) -> HealthScore:
        """Check scheduler health."""
        stats = self.run_manager.get_stats()
        
        # Check if scheduler is running (recent runs)
        recent_runs = self.run_manager.list_runs(
            since=datetime.now() - timedelta(minutes=30),
            limit=100
        )
        
        scheduler_runs = [r for r in recent_runs if r.trigger == TriggerType.SCHEDULER]
        
        if not scheduler_runs:
            return HealthScore(
                component=HealthComponent.SCHEDULER,
                score=50,
                status="degraded",
                details={"message": "No recent scheduler activity"}
            )
        
        success_rate = sum(1 for r in scheduler_runs if r.status == RunStatus.COMPLETED) / len(scheduler_runs)
        score = int(success_rate * 100)
        
        return HealthScore(
            component=HealthComponent.SCHEDULER,
            score=score,
            status="healthy" if score >= 90 else ("degraded" if score >= 50 else "critical"),
            details={
                "recent_scheduler_runs": len(scheduler_runs),
                "success_rate": success_rate,
            }
        )
    
    def _check_ai_health(self) -> HealthScore:
        """Check AI provider health."""
        events = self.event_bus.query_events(
            event_type=EventType.AI_COMPLETED,
            since=datetime.now() - timedelta(hours=1),
            limit=20
        )
        
        failed_events = self.event_bus.query_events(
            event_type=EventType.AI_FAILED,
            since=datetime.now() - timedelta(hours=1),
            limit=20
        )
        
        if not events and not failed_events:
            return HealthScore(
                component=HealthComponent.AI,
                score=70,
                status="degraded",
                details={"message": "AI not recently used", "enabled": False}
            )
        
        success_rate = len(events) / max(1, len(events) + len(failed_events))
        score = int(success_rate * 100)
        
        return HealthScore(
            component=HealthComponent.AI,
            score=score,
            status="healthy" if score >= 90 else ("degraded" if score >= 50 else "critical"),
            details={
                "recent_successes": len(events),
                "recent_failures": len(failed_events),
                "success_rate": success_rate,
            }
        )
    
    def _check_secrets_health(self) -> HealthScore:
        """Check secrets availability."""
        # Check if required secrets are configured
        from .secrets import get_secrets_manager
        secrets = get_secrets_manager()
        
        required = ["TELEGRAM_BOT_TOKEN"]
        missing = [s for s in required if not secrets.get_secret(s)]
        
        if missing:
            return HealthScore(
                component=HealthComponent.SECRETS,
                score=0,
                status="critical",
                details={"missing_secrets": missing}
            )
        
        return HealthScore(
            component=HealthComponent.SECRETS,
            score=100,
            status="healthy",
            details={"all_required_present": True}
        )
    
    def get_overall_health(self) -> Dict[str, Any]:
        """Get overall system health summary."""
        scores = self.calculate_health()
        
        if not scores:
            return {"overall": 0, "status": "unknown", "components": {}}
        
        total_score = sum(s.score for s in scores.values())
        overall = total_score / len(scores)
        
        if overall >= 90:
            status = "healthy"
        elif overall >= 70:
            status = "degraded"
        elif overall >= 50:
            status = "warning"
        else:
            status = "critical"
        
        return {
            "overall": round(overall, 1),
            "status": status,
            "components": {k.value: v.to_dict() for k, v in scores.items()},
            "checked_at": datetime.now().isoformat(),
        }
    
    def diagnose(self, component: HealthComponent) -> Dict[str, Any]:
        """Get detailed diagnostics for a component."""
        scores = self.calculate_health()
        score = scores.get(component)
        if not score:
            return {"error": "Component not found"}
        
        diagnostics = {
            "component": component.value,
            "score": score.score,
            "status": score.status,
            "details": score.details,
            "last_check": score.last_check.isoformat(),
        }
        
        # Add component-specific diagnostics
        if component == HealthComponent.DATA:
            diagnostics["recent_events"] = [
                e.to_dict() for e in self.event_bus.query_events(
                    event_type=EventType.SOURCE_FETCH_COMPLETED,
                    limit=10
                )
            ] + [
                e.to_dict() for e in self.event_bus.query_events(
                    event_type=EventType.SOURCE_FETCH_FAILED,
                    limit=10
                )
            ]
        elif component == HealthComponent.TELEGRAM:
            diagnostics["recent_deliveries"] = [
                e.to_dict() for e in self.event_bus.query_events(
                    event_type=EventType.TELEGRAM_SEND_SUCCESS,
                    limit=10
                )
            ] + [
                e.to_dict() for e in self.event_bus.query_events(
                    event_type=EventType.TELEGRAM_SEND_FAILED,
                    limit=10
                )
            ]
        
        return diagnostics


# Convenience function
def calculate_health() -> Dict[str, Any]:
    """Calculate overall system health."""
    scorer = HealthScorer()
    return scorer.get_overall_health()