# -*- coding: utf-8 -*-
"""
Channel Manager - Channel definitions with versioning and migration.
"""
from __future__ import annotations
import json
import threading
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import ChannelDefinition, generate_snapshot_id
from .runs import get_run_manager
from .events import emit_event, EventType


class ChannelManager:
    """Manages channel definitions with versioning."""
    
    def __init__(
        self,
        config_path: Optional[str] = None,
        versions_dir: Optional[str] = None
    ):
        self._channels: Dict[str, ChannelDefinition] = {}
        self._lock = threading.RLock()
        self._config_path = Path(config_path) if config_path else None
        self._versions_dir = Path(versions_dir) if versions_dir else None
        
        if self._versions_dir:
            self._versions_dir.mkdir(parents=True, exist_ok=True)
        
        self._load()
    
    def _load(self) -> None:
        """Load channels from config file."""
        if not self._config_path or not self._config_path.exists():
            self._load_defaults()
            return
        
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = json.load(f)
            
            channels_data = data.get("channels", [])
            for ch_data in channels_data:
                # Migrate v1 to v2 if needed
                ch_data = self._migrate_to_v2(ch_data)
                ch = ChannelDefinition.from_dict(ch_data)
                self._channels[ch.id] = ch
        except Exception:
            self._load_defaults()
    
    def _migrate_to_v2(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Migrate old channel format to v2 schema."""
        # Already v2
        if "content" in data and isinstance(data["content"], dict):
            return data
        
        # Migrate from old format (flat keys)
        migrated = {
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "description": data.get("header", ""),
            "sources": ["tgju_prices"],
            "content": {
                "price_updates": True,
                "news": data.get("news_categories", []),
                "polls": data.get("poll_enabled", False),
                "analysis": data.get("with_analysis", True),
                "ai_enrichment": False,
            },
            "schedule": {
                "price": data.get("schedule_minutes", 10),
                "news": 60,
                "poll": 240,
            },
            "max_posts_per_day": {
                "price": 144,
                "news": 24,
                "poll": 6,
            },
            "formatting": {
                "template": data.get("format", "chips"),
                "with_star": data.get("with_star", True),
                "with_footer": data.get("with_footer", True),
                "footer_text": data.get("footer", "به‌روزرسانی: هر ۱۰ دقیقه | منبع: tgju.org"),
                "locale": "fa_IR",
            },
            "ai": {
                "enabled": False,
                "provider": "",
                "model": "",
                "tasks": [],
            },
            "delivery": {
                "targets": [{"type": "telegram", "chat_id": data.get("telegram_id", "")}],
                "mode": "auto",
                "approval_required": False,
            },
            "enabled": data.get("enabled", True),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "version": 2,
        }
        
        # Add slug groups
        slugs = list(data.get("slugs", []))
        for group_slugs in data.get("slug_groups", {}).values():
            slugs.extend(group_slugs)
        if slugs:
            migrated["sources"].append("tgju_prices")
        
        return migrated
    
    def _load_defaults(self) -> None:
        """Load default channels from legacy channels.yaml if available."""
        # Will be populated from legacy when needed
        pass
    
    def _save(self) -> None:
        """Save channels to config file."""
        if not self._config_path:
            return
        
        try:
            data = {
                "channels": [ch.to_dict() for ch in self._channels.values()],
                "updated_at": datetime.now().isoformat(),
            }
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    
    def _save_version(self, channel: ChannelDefinition, changed_by: str, changes: Dict[str, Any]) -> None:
        """Save a version snapshot of the channel config."""
        if not self._versions_dir:
            return
        
        try:
            version_data = {
                "channel_id": channel.id,
                "version": channel.version,
                "changed_by": changed_by,
                "changed_at": datetime.now().isoformat(),
                "changes": changes,
                "previous": channel.to_dict(),
            }
            file_path = self._versions_dir / f"{channel.id}_v{channel.version}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(version_data, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    
    def register_in_memory(self, channel: ChannelDefinition) -> None:
        """Register/replace a channel in memory ONLY (no disk writes).
        Used by the legacy bridge so the orchestrator sees live channels
        without rewriting the v2 config file on every tick."""
        with self._lock:
            self._channels[channel.id] = channel

    def get_channel(self, channel_id: str) -> Optional[ChannelDefinition]:
        """Get a channel by ID."""
        with self._lock:
            return self._channels.get(channel_id)
    
    def get_all_channels(self) -> List[ChannelDefinition]:
        """Get all channels."""
        with self._lock:
            return list(self._channels.values())
    
    def get_enabled_channels(self) -> List[ChannelDefinition]:
        """Get only enabled channels."""
        with self._lock:
            return [ch for ch in self._channels.values() if ch.enabled]
    
    def create_channel(self, channel: ChannelDefinition, changed_by: str = "system") -> ChannelDefinition:
        """Create a new channel."""
        with self._lock:
            if channel.id in self._channels:
                raise ValueError(f"Channel {channel.id} already exists")
            self._channels[channel.id] = channel
            self._save()
            self._save_version(channel, changed_by, {"action": "created"})
        return channel
    
    def update_channel(
        self,
        channel_id: str,
        updates: Dict[str, Any],
        changed_by: str = "system"
    ) -> Optional[ChannelDefinition]:
        """Update a channel with version tracking."""
        with self._lock:
            channel = self._channels.get(channel_id)
            if not channel:
                return None
            
            # Track changes
            old_dict = channel.to_dict()
            changes = {}
            
            for key, value in updates.items():
                if key == "id":
                    continue
                if hasattr(channel, key):
                    old_value = getattr(channel, key)
                    if old_value != value:
                        changes[key] = {"old": old_value, "new": value}
                        setattr(channel, key, value)
            
            if changes:
                channel.version += 1
                channel.updated_at = datetime.now()
                self._save()
                self._save_version(channel, changed_by, changes)
                
                # Emit config change event
                emit_event(
                    EventType.CONTENT_SELECTED,  # reuse for config change
                    run_id=f"config_{channel_id}",
                    channel_id=channel_id,
                    status="success",
                    payload={"action": "config_update", "changes": changes}
                )
        
        return channel
    
    def delete_channel(self, channel_id: str) -> bool:
        """Delete a channel."""
        with self._lock:
            if channel_id not in self._channels:
                return False
            del self._channels[channel_id]
            self._save()
            return True
    
    def get_version_history(self, channel_id: str) -> List[Dict[str, Any]]:
        """Get version history for a channel."""
        if not self._versions_dir:
            return []
        
        history = []
        try:
            for file_path in sorted(self._versions_dir.glob(f"{channel_id}_v*.json")):
                with open(file_path, encoding="utf-8") as f:
                    history.append(json.load(f))
        except Exception:
            pass
        return history
    
    def rollback_channel(self, channel_id: str, target_version: int, changed_by: str = "system") -> Optional[ChannelDefinition]:
        """Rollback channel to a previous version."""
        history = self.get_version_history(channel_id)
        target = next((v for v in history if v["version"] == target_version), None)
        if not target:
            return None
        
        with self._lock:
            channel = ChannelDefinition.from_dict(target["previous"])
            channel.version = target_version + 1
            channel.updated_at = datetime.now()
            self._channels[channel_id] = channel
            self._save()
            self._save_version(channel, changed_by, {"action": "rollback", "from_version": target_version})
        
        return channel


# Global channel manager instance
_channel_manager: Optional[ChannelManager] = None


def get_channel_manager(
    config_path: Optional[str] = None,
    versions_dir: Optional[str] = None
) -> ChannelManager:
    """Get or create the global channel manager."""
    global _channel_manager
    if _channel_manager is None:
        _channel_manager = ChannelManager(config_path, versions_dir)
    return _channel_manager


def load_channels(config_path: Optional[str] = None) -> List[ChannelDefinition]:
    """Load all channels."""
    manager = get_channel_manager(config_path)
    return manager.get_all_channels()


def save_channels(channels: List[ChannelDefinition], config_path: Optional[str] = None) -> None:
    """Save channels (legacy compatibility)."""
    manager = get_channel_manager(config_path)
    for ch in channels:
        if ch.id in {c.id for c in manager.get_all_channels()}:
            manager.update_channel(ch.id, ch.to_dict())
        else:
            manager.create_channel(ch)


def get_channel(channel_id: str, config_path: Optional[str] = None) -> Optional[ChannelDefinition]:
    """Get a channel by ID."""
    manager = get_channel_manager(config_path)
    return manager.get_channel(channel_id)