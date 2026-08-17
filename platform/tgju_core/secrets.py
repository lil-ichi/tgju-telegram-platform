# -*- coding: utf-8 -*-
"""
Secrets Manager - Separates secrets from configuration.
"""
from __future__ import annotations
import os
import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional


class SecretsManager:
    """Manages secrets separately from channel configuration."""
    
    def __init__(self, secrets_path: Optional[str] = None):
        self._secrets: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._secrets_path = Path(secrets_path) if secrets_path else None
        
        # Load from environment first
        self._load_from_env()
        
        # Then load from file (overrides env)
        if self._secrets_path and self._secrets_path.exists():
            self._load_from_file()
    
    def _load_from_env(self) -> None:
        """Load secrets from environment variables."""
        # Common secret prefixes
        prefixes = ["TGJU_", "TELEGRAM_", "AI_", "OPENAI_", "ANTHROPIC_"]
        
        for key, value in os.environ.items():
            for prefix in prefixes:
                if key.startswith(prefix):
                    self._secrets[key] = value
                    break
        
        # Specific known secrets
        known_secrets = [
            "TELEGRAM_BOT_TOKEN",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "HF_TOKEN",
        ]
        for key in known_secrets:
            if key in os.environ:
                self._secrets[key] = os.environ[key]
        
        # Fallback: read from the platform .env file (legacy location)
        try:
            env_paths = [
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
                os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env"),
            ]
            for env_path in env_paths:
                if os.path.exists(env_path):
                    with open(env_path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("#") or "=" not in line:
                                continue
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip('"').strip("'")
                            if k in known_secrets and not v.startswith("***"):
                                self._secrets[k] = v
        except Exception:
            pass
    
    def _load_from_file(self) -> None:
        """Load secrets from JSON file."""
        try:
            with open(self._secrets_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                with self._lock:
                    self._secrets.update({k: str(v) for k, v in data.items()})
        except Exception:
            pass
    
    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a secret value."""
        with self._lock:
            return self._secrets.get(key, default)
    
    def set_secret(self, key: str, value: str) -> None:
        """Set a secret value."""
        with self._lock:
            self._secrets[key] = value
    
    def delete_secret(self, key: str) -> bool:
        """Delete a secret."""
        with self._lock:
            if key in self._secrets:
                del self._secrets[key]
                return True
            return False
    
    def list_secrets(self) -> Dict[str, str]:
        """List all secret keys (values masked)."""
        with self._lock:
            return {k: "****" for k in self._secrets.keys()}
    
    def save_to_file(self) -> bool:
        """Save secrets to file."""
        if not self._secrets_path:
            return False
        try:
            with self._lock:
                with open(self._secrets_path, "w", encoding="utf-8") as f:
                    json.dump(self._secrets, f, ensure_ascii=False, indent=1)
            return True
        except Exception:
            return False
    
    def get_required_missing(self, required: list[str]) -> list[str]:
        """Check which required secrets are missing."""
        with self._lock:
            return [k for k in required if k not in self._secrets]


# Global secrets manager instance
_secrets_manager: Optional[SecretsManager] = None


def get_secrets_manager(secrets_path: Optional[str] = None) -> SecretsManager:
    """Get or create the global secrets manager."""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretsManager(secrets_path)
    return _secrets_manager


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get a secret value."""
    return get_secrets_manager().get_secret(key, default)


def set_secret(key: str, value: str) -> None:
    """Set a secret value."""
    get_secrets_manager().set_secret(key, value)