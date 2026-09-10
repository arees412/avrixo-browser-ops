"""Programmatic interface for governed browser task operations."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

from browser_harness import paths

from .artifacts import ArtifactStore
from .audit import AuditTrail, utc_now
from .executor import BrowserExecutor, HarnessExecutor
from .models import ActionRequest, ActionType, ApprovalStatus, TaskStatus
from .policy import DEFAULT_SENSITIVE_ACTIONS, PolicyConfig
from .redaction import reject_credential_url, reject_sensitive_input
from .runtime import GovernedRuntime
from .store import TaskStore


class BrowserOpsService:
    """Create, inspect, run, cancel, and approve durable browser tasks."""

    def __init__(self, root: Path | None = None, executor: BrowserExecutor | None = None):
        configured = os.environ.get("BH_GOVERNANCE_DIR")
        base = root or (Path(configured).expanduser() if configured else paths.workspace_dir() / "governance")
        self.root = base.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(self.root / "browserops.sqlite3")
        self.audit = AuditTrail(self.root / "audit.jsonl")
        self.artifacts = ArtifactStore(self.root / "artifacts")
        self.runtime = GovernedRuntime(self.store, self.audit, self.artifacts, executor or HarnessExecutor())

    def create_task(
        self,
        *,
        objective: str,
        actions: Iterable[dict[str, Any] | ActionRequest],
        allowed_domains: Iterable[str],
        blocked_domains: Iterable[str] = (),
        max_steps: int = 25,
        task_timeout_seconds: float = 300.0,
        step_timeout_seconds: float = 30.0,
        max_retries: int = 2,
        allow_downloads: bool = False,
        capture_artifacts: bool = False,
        sensitive_actions: Iterable[str | ActionType] | None = None,
        created_by: str = "local-agent",
    ) -> dict[str, Any]:
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 4_000:
            raise ValueError("objective must contain 1 to 4000 characters")
        if not isinstance(created_by, str) or not created_by.strip() or len(created_by) > 100:
            raise ValueError("created_by must contain 1 to 100 characters")
        reject_sensitive_input(objective, "objective")
        reject_sensitive_input(created_by, "created_by")
        parsed_actions = [
            item if isinstance(item, ActionRequest) else ActionRequest.from_dict(item) for item in actions
        ]
        if not parsed_actions or len(parsed_actions) > 500:
            raise ValueError("a task must contain 1 to 500 actions")
        for index, action in enumerate(parsed_actions):
            reject_sensitive_input(action.metadata)
            if action.target is not None:
                reject_sensitive_input(action.target, f"actions[{index}].target")
                reject_credential_url(action.target, f"actions[{index}].target")
            if action.action_type is ActionType.FILL:
                selector = action.metadata.get("selector")
                if isinstance(selector, str) and any(
                    marker in selector.lower() for marker in ("password", "passwd", "token", "api-key", "api_key")
                ):
                    raise ValueError("credential-bearing form fields are not accepted by governed task input")
        parsed_sensitive = frozenset(
            ActionType(item)
            for item in (sensitive_actions if sensitive_actions is not None else DEFAULT_SENSITIVE_ACTIONS)
        )
        config = PolicyConfig(
            allowed_domains=tuple(allowed_domains),
            blocked_domains=tuple(blocked_domains),
            max_steps=max_steps,
            task_timeout_seconds=task_timeout_seconds,
            step_timeout_seconds=step_timeout_seconds,
            max_retries=max_retries,
            allow_downloads=allow_downloads,
            sensitive_actions=parsed_sensitive,
        )
        task_id = f"task_{uuid4().hex}"
        task = self.store.create_task(
            {
                "id": task_id,
                "objective": objective.strip(),
                "status": TaskStatus.PENDING.value,
                "created_at": utc_now(),
                "started_at": None,
                "completed_at": None,
                "max_steps": config.max_steps,
                "task_timeout_seconds": config.task_timeout_seconds,
                "step_timeout_seconds": config.step_timeout_seconds,
                "max_retries": config.max_retries,
                "allowed_domains_json": json.dumps(config.allowed_domains),
                "blocked_domains_json": json.dumps(config.blocked_domains),
                "sensitive_actions_json": json.dumps(sorted(item.value for item in config.sensitive_actions)),
                "allow_downloads": int(config.allow_downloads),
                "capture_artifacts": int(bool(capture_artifacts)),
                "created_by": created_by.strip(),
                "actions_json": json.dumps([action.to_dict() for action in parsed_actions], ensure_ascii=False),
                "result_json": None,
                "error_reason": None,
                "cancel_requested": 0,
            }
        )
        self.audit.record(
            "task_created",
            task_id=task_id,
            action_count=len(parsed_actions),
            allowed_domains=config.allowed_domains,
            blocked_domains=config.blocked_domains,
            capture_artifacts=bool(capture_artifacts),
        )
        return task

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.store.list_tasks()

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self.store.get_task(task_id)

    def run_task(self, task_id: str) -> dict[str, Any]:
        return self.runtime.run(task_id)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return self.runtime.cancel(task_id)

    def list_steps(self, task_id: str) -> list[dict[str, Any]]:
        self.store.get_task(task_id)
        return self.store.list_steps(task_id)

    def list_approvals(self, status: str | None = ApprovalStatus.PENDING.value) -> list[dict[str, Any]]:
        if status is not None:
            ApprovalStatus(status)
        return self.store.list_approvals(status)

    def approve(self, approval_id: str) -> dict[str, Any]:
        return self.runtime.resolve_approval(approval_id, approved=True)

    def reject(self, approval_id: str) -> dict[str, Any]:
        return self.runtime.resolve_approval(approval_id, approved=False)

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        self.store.get_task(task_id)
        return self.store.list_artifacts(task_id)
