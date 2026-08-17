# -*- coding: utf-8 -*-
"""
Run Manager - Execution tracking with full traceability.
"""
from __future__ import annotations
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import (
    RunRecord, RunStatus, TriggerType, OrchestrationDecision,
    ContentItem, generate_run_id
)


class RunManager:
    """Manages execution runs with persistence and querying."""
    
    def __init__(self, persist_dir: Optional[str] = None):
        self._runs: Dict[str, RunRecord] = {}
        self._lock = threading.RLock()
        self._persist_dir = Path(persist_dir) if persist_dir else None
        
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_recent()
    
    def _load_recent(self, days: int = 7) -> None:
        """Load recent runs from disk."""
        if not self._persist_dir:
            return
        try:
            cutoff = datetime.now().timestamp() - (days * 86400)
            for file_path in sorted(self._persist_dir.glob("run_*.json")):
                try:
                    mtime = file_path.stat().st_mtime
                    if mtime < cutoff:
                        continue
                    with open(file_path, encoding="utf-8") as f:
                        data = json.load(f)
                    run = RunRecord.from_dict(data)
                    self._runs[run.id] = run
                except Exception:
                    continue
        except Exception:
            pass
    
    def _persist_run(self, run: RunRecord) -> None:
        """Persist run to disk."""
        if not self._persist_dir:
            return
        try:
            file_path = self._persist_dir / f"run_{run.id}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(run.to_dict(), f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    
    def create_run(
        self,
        channel_id: str,
        trigger: TriggerType,
        snapshot_id: str = "",
        parent_run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> RunRecord:
        """Create a new run record."""
        run = RunRecord(
            id=generate_run_id(),
            channel_id=channel_id,
            trigger=trigger,
            status=RunStatus.PENDING,
            started_at=datetime.now(),
            snapshot_id=snapshot_id,
            parent_run_id=parent_run_id,
            metadata=metadata or {},
        )
        with self._lock:
            self._runs[run.id] = run
        self._persist_run(run)
        return run
    
    def start_run(self, run_id: str) -> Optional[RunRecord]:
        """Mark run as running."""
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run.status = RunStatus.RUNNING
                self._persist_run(run)
            return run
    
    def complete_run(
        self,
        run_id: str,
        status: RunStatus = RunStatus.COMPLETED,
        decisions: Optional[OrchestrationDecision] = None,
        content_items: Optional[List[ContentItem]] = None,
        message_preview: str = "",
        telegram_message_id: Optional[str] = None,
        delivery_target: str = "",
        latency_ms: Optional[Dict[str, int]] = None,
        error: Optional[str] = None
    ) -> Optional[RunRecord]:
        """Complete a run with results."""
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return None
            
            run.status = status
            run.completed_at = datetime.now()
            if decisions:
                run.decisions = decisions
            if content_items:
                run.content_items = content_items
            run.message_preview = message_preview
            run.telegram_message_id = telegram_message_id
            run.delivery_target = delivery_target
            if latency_ms:
                run.latency_ms = latency_ms
            run.error = error
            
            self._persist_run(run)
            return run
    
    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """Get a run by ID."""
        with self._lock:
            return self._runs.get(run_id)
    
    def list_runs(
        self,
        channel_id: Optional[str] = None,
        trigger: Optional[TriggerType] = None,
        status: Optional[RunStatus] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[RunRecord]:
        """List runs with filters."""
        with self._lock:
            runs = list(self._runs.values())
        
        if channel_id:
            runs = [r for r in runs if r.channel_id == channel_id]
        if trigger:
            runs = [r for r in runs if r.trigger == trigger]
        if status:
            runs = [r for r in runs if r.status == status]
        if since:
            runs = [r for r in runs if r.started_at >= since]
        
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]
    
    def get_channel_history(self, channel_id: str, limit: int = 50) -> List[RunRecord]:
        """Get run history for a channel."""
        return self.list_runs(channel_id=channel_id, limit=limit)
    
    def get_recent_failures(self, limit: int = 20) -> List[RunRecord]:
        """Get recent failed runs."""
        return self.list_runs(status=RunStatus.FAILED, limit=limit)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get run statistics."""
        with self._lock:
            runs = list(self._runs.values())
        
        total = len(runs)
        completed = sum(1 for r in runs if r.status == RunStatus.COMPLETED)
        failed = sum(1 for r in runs if r.status == RunStatus.FAILED)
        running = sum(1 for r in runs if r.status == RunStatus.RUNNING)
        pending = sum(1 for r in runs if r.status == RunStatus.PENDING)
        
        durations = [r.duration_ms() for r in runs if r.duration_ms()]
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        by_channel: Dict[str, int] = {}
        by_trigger: Dict[str, int] = {}
        for r in runs:
            by_channel[r.channel_id] = by_channel.get(r.channel_id, 0) + 1
            by_trigger[r.trigger.value] = by_trigger.get(r.trigger.value, 0) + 1
        
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": pending,
            "success_rate": completed / total if total > 0 else 0,
            "avg_duration_ms": avg_duration,
            "by_channel": by_channel,
            "by_trigger": by_trigger,
        }


# Global run manager instance
_run_manager: Optional[RunManager] = None


def get_run_manager(persist_dir: Optional[str] = None) -> RunManager:
    """Get or create the global run manager."""
    global _run_manager
    if _run_manager is None:
        _run_manager = RunManager(persist_dir)
    return _run_manager


def create_run(
    channel_id: str,
    trigger: TriggerType,
    snapshot_id: str = "",
    parent_run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> RunRecord:
    """Create a new run."""
    return get_run_manager().create_run(channel_id, trigger, snapshot_id, parent_run_id, metadata)


def start_run(run_id: str) -> Optional[RunRecord]:
    """Start a run."""
    return get_run_manager().start_run(run_id)


def complete_run(
    run_id: str,
    status: RunStatus = RunStatus.COMPLETED,
    decisions: Optional[OrchestrationDecision] = None,
    content_items: Optional[List[ContentItem]] = None,
    message_preview: str = "",
    telegram_message_id: Optional[str] = None,
    delivery_target: str = "",
    latency_ms: Optional[Dict[str, int]] = None,
    error: Optional[str] = None
) -> Optional[RunRecord]:
    """Complete a run."""
    return get_run_manager().complete_run(
        run_id, status, decisions, content_items,
        message_preview, telegram_message_id, delivery_target,
        latency_ms, error
    )


def get_run(run_id: str) -> Optional[RunRecord]:
    """Get a run by ID."""
    return get_run_manager().get_run(run_id)