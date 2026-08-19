# -*- coding: utf-8 -*-
"""
Approval System - AUTO / APPROVAL / MANUAL modes for channels.
"""
from __future__ import annotations
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import ChannelMode, RunStatus, TriggerType
from .runs import get_run_manager, RunRecord
from .channels import get_channel_manager
from .events import emit_event, EventType


class ApprovalManager:
    """Manages content approval workflows."""
    
    def __init__(self, persist_dir: Optional[str] = None):
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._persist_dir = Path(persist_dir) if persist_dir else None
        
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load()
    
    def _load(self) -> None:
        """Load pending approvals from disk."""
        if not self._persist_dir:
            return
        
        try:
            for file_path in self._persist_dir.glob("approval_*.json"):
                try:
                    with open(file_path, encoding="utf-8") as f:
                        data = json.load(f)
                    self._pending[data["id"]] = data
                except Exception:
                    continue
        except Exception:
            pass
    
    def _persist(self, approval: Dict[str, Any]) -> None:
        """Persist approval to disk."""
        if not self._persist_dir:
            return
        
        try:
            file_path = self._persist_dir / f"approval_{approval['id']}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(approval, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    
    def _delete(self, approval_id: str) -> None:
        """Delete approval from disk."""
        if not self._persist_dir:
            return
        
        try:
            (self._persist_dir / f"approval_{approval_id}.json").unlink()
        except Exception:
            pass
    
    def create_approval_request(
        self,
        run_id: str,
        channel_id: str,
        content_preview: str,
        content_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create an approval request for a run."""
        approval_id = f"appr_{run_id}_{datetime.now().strftime('%H%M%S')}"
        
        approval = {
            "id": approval_id,
            "run_id": run_id,
            "channel_id": channel_id,
            "content_preview": content_preview,
            "content_type": content_type,
            "status": "pending",  # pending, approved, rejected
            "created_at": datetime.now().isoformat(),
            "decided_at": None,
            "decided_by": None,
            "metadata": metadata or {},
        }
        
        with self._lock:
            self._pending[approval_id] = approval
            self._persist(approval)
        
        return approval
    
    def approve(self, approval_id: str, decided_by: str = "user") -> bool:
        """Approve a pending request."""
        with self._lock:
            approval = self._pending.get(approval_id)
            if not approval or approval["status"] != "pending":
                return False
            
            approval["status"] = "approved"
            approval["decided_at"] = datetime.now().isoformat()
            approval["decided_by"] = decided_by
            self._persist(approval)
            
            # Update run status to allow continuation
            run_manager = get_run_manager()
            run = run_manager.get_run(approval["run_id"])
            if run and run.status == RunStatus.AWAITING_APPROVAL:
                run.status = RunStatus.RUNNING
                run_manager._persist_run(run)
        
        emit_event(
            EventType.RUN_COMPLETED,
            run_id=approval["run_id"],
            channel_id=approval["channel_id"],
            status="success",
            payload={"approval": "approved", "approval_id": approval_id}
        )
        
        return True
    
    def reject(self, approval_id: str, decided_by: str = "user", reason: str = "") -> bool:
        """Reject a pending request."""
        with self._lock:
            approval = self._pending.get(approval_id)
            if not approval or approval["status"] != "pending":
                return False
            
            approval["status"] = "rejected"
            approval["decided_at"] = datetime.now().isoformat()
            approval["decided_by"] = decided_by
            approval["rejection_reason"] = reason
            self._persist(approval)
            
            # Update run status
            run_manager = get_run_manager()
            run = run_manager.get_run(approval["run_id"])
            if run and run.status == RunStatus.AWAITING_APPROVAL:
                run.status = RunStatus.CANCELLED
                run.error = f"Approval rejected: {reason}"
                run_manager._persist_run(run)
        
        emit_event(
            EventType.RUN_FAILED,
            run_id=approval["run_id"],
            channel_id=approval["channel_id"],
            status="failed",
            payload={"approval": "rejected", "approval_id": approval_id, "reason": reason}
        )
        
        return True
    
    def get_pending(self, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get pending approval requests."""
        with self._lock:
            approvals = list(self._pending.values())
        
        if channel_id:
            approvals = [a for a in approvals if a["channel_id"] == channel_id]
        
        # Only pending
        approvals = [a for a in approvals if a["status"] == "pending"]
        approvals.sort(key=lambda a: a["created_at"])
        return approvals
    
    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Get approval by ID."""
        with self._lock:
            return self._pending.get(approval_id)
    
    def get_history(self, channel_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get approval history."""
        with self._lock:
            approvals = list(self._pending.values())
        
        if channel_id:
            approvals = [a for a in approvals if a["channel_id"] == channel_id]
        
        approvals.sort(key=lambda a: a["created_at"], reverse=True)
        return approvals[:limit]


# Global approval manager instance
_approval_manager: Optional[ApprovalManager] = None


def get_approval_manager(persist_dir: Optional[str] = None) -> ApprovalManager:
    """Get or create the global approval manager."""
    global _approval_manager
    if _approval_manager is None:
        _approval_manager = ApprovalManager(persist_dir)
    return _approval_manager


def request_approval(
    run_id: str,
    channel_id: str,
    content_preview: str,
    content_type: str,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Request approval for content."""
    return get_approval_manager().create_approval_request(
        run_id, channel_id, content_preview, content_type, metadata
    )


def approve_content(approval_id: str, decided_by: str = "user") -> bool:
    """Approve content."""
    return get_approval_manager().approve(approval_id, decided_by)


def reject_content(approval_id: str, decided_by: str = "user", reason: str = "") -> bool:
    """Reject content."""
    return get_approval_manager().reject(approval_id, decided_by, reason)


def get_pending_approvals(channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get pending approvals."""
    return get_approval_manager().get_pending(channel_id)