# -*- coding: utf-8 -*-
"""
Idempotency Manager - Prevents duplicate Telegram posts.
"""
from __future__ import annotations
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from .types import IdempotencyKey, generate_idempotency_key, content_hash
from .events import emit_event, EventType


class IdempotencyManager:
    """Manages idempotency keys to prevent duplicate deliveries."""
    
    def __init__(self, persist_dir: Optional[str] = None):
        self._keys: Dict[str, IdempotencyKey] = {}
        self._lock = threading.RLock()
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._ttl = timedelta(days=7)  # Keep keys for 7 days
        
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load()
    
    def _load(self) -> None:
        """Load idempotency keys from disk."""
        if not self._persist_dir:
            return
        
        try:
            cutoff = datetime.now() - self._ttl
            for file_path in self._persist_dir.glob("idempotency_*.json"):
                try:
                    with open(file_path, encoding="utf-8") as f:
                        data = json.load(f)
                    
                    created_at = datetime.fromisoformat(data["created_at"])
                    if created_at < cutoff:
                        file_path.unlink()  # Delete expired
                        continue
                    
                    key = IdempotencyKey(
                        key=data["key"],
                        channel_id=data["channel_id"],
                        content_hash=data["content_hash"],
                        scheduled_slot=data["scheduled_slot"],
                        created_at=created_at,
                        used=data.get("used", False),
                        telegram_message_id=data.get("telegram_message_id"),
                    )
                    self._keys[key.key] = key
                except Exception:
                    continue
        except Exception:
            pass
    
    def _persist(self, key: IdempotencyKey) -> None:
        """Persist idempotency key to disk."""
        if not self._persist_dir:
            return
        
        try:
            file_path = self._persist_dir / f"idempotency_{key.key}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(key.to_dict(), f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    
    def check_and_mark(
        self,
        channel_id: str,
        content_data: Dict[str, Any],
        scheduled_slot: str,
        telegram_message_id: Optional[str] = None
    ) -> tuple[bool, Optional[IdempotencyKey]]:
        """
        Check if operation is idempotent, mark if not.
        
        Returns:
            (is_duplicate, existing_key)
            - is_duplicate: True if this exact operation was already done
            - existing_key: The existing key if duplicate, None otherwise
        """
        key_str = generate_idempotency_key(channel_id, content_hash(content_data), scheduled_slot)
        
        with self._lock:
            existing = self._keys.get(key_str)
            
            if existing and existing.used:
                # Already processed - duplicate
                return True, existing
            
            if existing and not existing.used:
                # Key exists but not marked used - could be in progress
                # If it has a telegram_message_id, it was actually sent
                if existing.telegram_message_id:
                    existing.used = True
                    self._persist(existing)
                    return True, existing
                # Otherwise, it's a stale in-progress key, we can overwrite
            
            # Create new key
            new_key = IdempotencyKey(
                key=key_str,
                channel_id=channel_id,
                content_hash=content_hash(content_data),
                scheduled_slot=scheduled_slot,
                created_at=datetime.now(),
                used=False,
                telegram_message_id=telegram_message_id,
            )
            self._keys[key_str] = new_key
            self._persist(new_key)
            
            return False, None
    
    def mark_used(self, key_str: str, telegram_message_id: str) -> bool:
        """Mark an idempotency key as used with the Telegram message ID."""
        with self._lock:
            key = self._keys.get(key_str)
            if key:
                key.used = True
                key.telegram_message_id = telegram_message_id
                self._persist(key)
                return True
            return False
    
    def check_idempotent(
        self,
        channel_id: str,
        content_data: Dict[str, Any],
        scheduled_slot: str
    ) -> bool:
        """Check if an operation would be a duplicate (without marking)."""
        key_str = generate_idempotency_key(channel_id, content_hash(content_data), scheduled_slot)
        with self._lock:
            existing = self._keys.get(key_str)
            return existing is not None and existing.used
    
    def get_key(self, key_str: str) -> Optional[IdempotencyKey]:
        """Get an idempotency key by its string."""
        with self._lock:
            return self._keys.get(key_str)
    
    def cleanup_expired(self) -> int:
        """Remove expired keys. Returns count removed."""
        cutoff = datetime.now() - self._ttl
        removed = 0
        
        with self._lock:
            expired_keys = [
                k for k, v in self._keys.items()
                if v.created_at < cutoff
            ]
            for k in expired_keys:
                del self._keys[k]
                removed += 1
                
                # Also delete file
                if self._persist_dir:
                    try:
                        (self._persist_dir / f"idempotency_{k}.json").unlink()
                    except Exception:
                        pass
        
        return removed


# Global idempotency manager instance
_idempotency_manager: Optional[IdempotencyManager] = None


def get_idempotency_manager(persist_dir: Optional[str] = None) -> IdempotencyManager:
    """Get or create the global idempotency manager."""
    global _idempotency_manager
    if _idempotency_manager is None:
        _idempotency_manager = IdempotencyManager(persist_dir)
    return _idempotency_manager


def check_idempotent(
    channel_id: str,
    content_data: Dict[str, Any],
    scheduled_slot: str
) -> bool:
    """Check if operation would be duplicate."""
    return get_idempotency_manager().check_idempotent(channel_id, content_data, scheduled_slot)


def mark_idempotent(
    channel_id: str,
    content_data: Dict[str, Any],
    scheduled_slot: str,
    telegram_message_id: Optional[str] = None
) -> tuple[bool, Optional[IdempotencyKey]]:
    """Check and mark idempotent."""
    return get_idempotency_manager().check_and_mark(
        channel_id, content_data, scheduled_slot, telegram_message_id
    )