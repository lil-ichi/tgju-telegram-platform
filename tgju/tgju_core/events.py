# -*- coding: utf-8 -*-
"""
Event Bus - Structured event system for observability.
"""
from __future__ import annotations
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict

from .types import EventRecord, EventType, generate_event_id


class EventBus:
    """Thread-safe event bus for structured logging and observability."""
    
    def __init__(self, persist_dir: Optional[str] = None):
        self._subscribers: Dict[EventType, List[Callable[[EventRecord], None]]] = defaultdict(list)
        self._lock = threading.RLock()
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._event_buffer: List[EventRecord] = []
        self._buffer_max = 10000
        
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
    
    def subscribe(self, event_type: EventType, callback: Callable[[EventRecord], None]) -> None:
        """Subscribe to an event type."""
        with self._lock:
            self._subscribers[event_type].append(callback)
    
    def unsubscribe(self, event_type: EventType, callback: Callable[[EventRecord], None]) -> None:
        """Unsubscribe from an event type."""
        with self._lock:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)
    
    def emit(self, event: EventRecord) -> None:
        """Emit an event to all subscribers and persist."""
        # Persist to disk
        if self._persist_dir:
            self._persist_event(event)
        
        # Buffer in memory
        with self._lock:
            self._event_buffer.append(event)
            if len(self._event_buffer) > self._buffer_max:
                self._event_buffer = self._event_buffer[-self._buffer_max:]
        
        # Notify subscribers (async to not block)
        for callback in self._subscribers.get(event.event_type, []):
            try:
                callback(event)
            except Exception:
                pass  # Don't let subscriber errors break emission
    
    def _persist_event(self, event: EventRecord) -> None:
        """Persist event to daily JSONL file."""
        try:
            date_str = event.timestamp.strftime("%Y-%m-%d")
            file_path = self._persist_dir / f"events_{date_str}.jsonl"
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass  # Silently fail persistence
    
    def query_events(
        self,
        event_type: Optional[EventType] = None,
        channel_id: Optional[str] = None,
        run_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[EventRecord]:
        """Query events from memory buffer (fast) or disk (if needed)."""
        with self._lock:
            events = list(self._event_buffer)
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if channel_id:
            events = [e for e in events if e.channel_id == channel_id]
        if run_id:
            events = [e for e in events if e.run_id == run_id]
        if since:
            events = [e for e in events if e.timestamp >= since]
        
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:limit]
    
    def get_recent(self, limit: int = 100) -> List[EventRecord]:
        """Get most recent events."""
        with self._lock:
            return list(self._event_buffer[-limit:])[::-1]


# Global event bus instance
_event_bus: Optional[EventBus] = None


def get_event_bus(persist_dir: Optional[str] = None) -> EventBus:
    """Get or create the global event bus."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(persist_dir)
    return _event_bus


def emit_event(
    event_type: EventType,
    run_id: str,
    channel_id: str,
    status: str = "success",
    duration_ms: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    timestamp: Optional[datetime] = None
) -> EventRecord:
    """Convenience function to emit a structured event."""
    bus = get_event_bus()
    event = EventRecord(
        id=generate_event_id(),
        event_type=event_type,
        run_id=run_id,
        channel_id=channel_id,
        timestamp=timestamp or datetime.now(),
        status=status,
        duration_ms=duration_ms,
        payload=payload or {},
        error=error,
    )
    bus.emit(event)
    return event


def subscribe(event_type: EventType, callback: Callable[[EventRecord], None]) -> None:
    """Subscribe to an event type."""
    get_event_bus().subscribe(event_type, callback)