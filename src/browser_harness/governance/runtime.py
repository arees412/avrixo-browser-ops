"""Governed task state machine around a constrained browser executor."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .artifacts import ArtifactStore
from .audit import AuditTrail, utc_now
from .executor import BrowserExecutor, NonRetryableExecutionError, RetryableExecutionError
from .models import (
    ActionRequest,
    ActionType,
    ApprovalStatus,
    PolicyOutcome,
    SessionStatus,
    StepStatus,
    TaskStatus,
)
from .policy import PolicyConfig, PolicyContext, PolicyEngine
from .redaction import redact, redact_text
from .store import TaskStore

_TERMINAL = {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}


class GovernedRuntime:
    def __init__(
        self,
        store: TaskStore,
        audit: AuditTrail,
        artifacts: ArtifactStore,
        executor: BrowserExecutor,
    ):
        self.store = store
        self.audit = audit
        self.artifacts = artifacts
        self.executor = executor

    def run(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] in _TERMINAL:
            return task
        if task["cancel_requested"]:
            return self._cancel(task, "task cancellation requested")

        started_at = task["started_at"] or utc_now()
        if task["started_at"] is None:
            task = self.store.update_task(task_id, status=TaskStatus.RUNNING.value, started_at=started_at)
        else:
            task = self.store.update_task(task_id, status=TaskStatus.RUNNING.value)
        session = self.store.active_session(task_id) or self._create_session(task_id)
        self.store.update_session(session["id"], session_status=SessionStatus.RUNNING.value)
        config = self._policy_config(task)
        engine = PolicyEngine(config)
        actions = [ActionRequest.from_dict(item) for item in task["actions"]]
        current_url = self._current_url(task_id)

        for sequence, action in enumerate(actions, start=1):
            task = self.store.get_task(task_id)
            if task["cancel_requested"]:
                return self._cancel(task, "task cancellation requested", session_id=session["id"])
            elapsed = self._elapsed_seconds(started_at)
            if elapsed >= config.task_timeout_seconds:
                return self._fail_task(task, session["id"], "task timeout exceeded")

            step = self.store.step_for_sequence(task_id, sequence)
            if step and step["status"] == StepStatus.COMPLETED.value:
                if action.action_type is ActionType.NAVIGATE:
                    current_url = action.target
                continue
            if step and step["status"] == StepStatus.AWAITING_APPROVAL.value:
                approval = self.store.approval_for_step(step["id"])
                if approval is None:
                    return self._fail_task(task, session["id"], "approval state is missing", step_id=step["id"])
                if approval["status"] == ApprovalStatus.PENDING.value:
                    self.store.update_task(task_id, status=TaskStatus.AWAITING_APPROVAL.value)
                    self.store.update_session(session["id"], session_status=SessionStatus.AWAITING_APPROVAL.value)
                    return self.store.get_task(task_id)
                if approval["status"] == ApprovalStatus.REJECTED.value:
                    self.store.update_step(
                        step["id"],
                        status=StepStatus.CANCELLED.value,
                        completed_at=utc_now(),
                        error_message="approval rejected",
                    )
                    return self._cancel(task, "approval rejected", session_id=session["id"])
                step = self.store.update_step(step["id"], status=StepStatus.RUNNING.value, started_at=utc_now())

            if step is None:
                decision = engine.evaluate(
                    action,
                    PolicyContext(sequence=sequence, elapsed_seconds=elapsed, current_url=current_url),
                )
                step = self._create_step(task_id, session["id"], sequence, action, decision.outcome, decision.reason)
                self._audit_step(task, session["id"], step, current_url, "policy_evaluated")
                if decision.outcome is PolicyOutcome.DENY:
                    self.store.update_step(
                        step["id"],
                        status=StepStatus.FAILED.value,
                        completed_at=utc_now(),
                        error_message=decision.reason,
                    )
                    return self._fail_task(task, session["id"], decision.reason, step_id=step["id"])
                if decision.outcome is PolicyOutcome.REQUIRES_APPROVAL:
                    approval = self._create_approval(task_id, step["id"], action, decision.reason)
                    self.store.update_step(step["id"], status=StepStatus.AWAITING_APPROVAL.value)
                    self.store.update_task(task_id, status=TaskStatus.AWAITING_APPROVAL.value)
                    self.store.update_session(session["id"], session_status=SessionStatus.AWAITING_APPROVAL.value)
                    self._audit_step(
                        task,
                        session["id"],
                        self.store.get_step(step["id"]),
                        current_url,
                        "approval_requested",
                        approval_state=approval["status"],
                    )
                    return self.store.get_task(task_id)

            result = self._execute_step(task, session["id"], step, action, config, current_url)
            if result is None:
                return self.store.get_task(task_id)
            if action.action_type is ActionType.NAVIGATE:
                current_url = action.target

        return self._complete_task(self.store.get_task(task_id), session["id"])

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task["status"] in _TERMINAL:
            return task
        task = self.store.update_task(task_id, cancel_requested=1)
        if task["status"] in {TaskStatus.PENDING.value, TaskStatus.AWAITING_APPROVAL.value}:
            return self._cancel(task, "task cancellation requested")
        self.audit.record("task_cancellation_requested", task_id=task_id, execution_outcome="pending safe boundary")
        return task

    def resolve_approval(self, approval_id: str, approved: bool) -> dict[str, Any]:
        approval = self.store.get_approval(approval_id)
        if approval["status"] != ApprovalStatus.PENDING.value:
            raise ValueError("approval has already been resolved")
        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        approval = self.store.resolve_approval(approval_id, status.value, utc_now())
        self.audit.record(
            "approval_resolved",
            task_id=approval["task_id"],
            step_id=approval["step_id"],
            approval_state=status.value,
        )
        if not approved:
            task = self.store.get_task(approval["task_id"])
            step = self.store.get_step(approval["step_id"])
            self.store.update_step(
                step["id"], status=StepStatus.CANCELLED.value, completed_at=utc_now(), error_message="approval rejected"
            )
            self._cancel(task, "approval rejected", session_id=step["session_id"])
        return approval

    def _execute_step(
        self,
        task: dict[str, Any],
        session_id: str,
        step: dict[str, Any],
        action: ActionRequest,
        config: PolicyConfig,
        current_url: str | None,
    ) -> dict[str, Any] | None:
        attempts = int(step["attempts"])
        while attempts <= config.max_retries:
            if self.store.get_task(task["id"])["cancel_requested"]:
                self.store.update_step(
                    step["id"],
                    status=StepStatus.CANCELLED.value,
                    completed_at=utc_now(),
                    error_message="task cancelled",
                )
                self._cancel(task, "task cancellation requested", session_id=session_id)
                return None
            attempts += 1
            self.store.update_step(
                step["id"],
                status=StepStatus.RUNNING.value,
                started_at=step["started_at"] or utc_now(),
                attempts=attempts,
            )
            started = time.monotonic()
            try:
                result = self.executor.execute(action, config.step_timeout_seconds)
                duration = time.monotonic() - started
                if duration > config.step_timeout_seconds:
                    raise NonRetryableExecutionError("step timeout exceeded")
                safe_result = redact(result)
                completed = self.store.update_step(
                    step["id"],
                    status=StepStatus.COMPLETED.value,
                    completed_at=utc_now(),
                    result_json=json.dumps(safe_result, ensure_ascii=False),
                )
                self._audit_step(
                    task,
                    session_id,
                    completed,
                    action.target if action.action_type is ActionType.NAVIGATE else current_url,
                    "step_completed",
                    execution_outcome="completed",
                    duration_seconds=round(duration, 3),
                )
                if task["capture_artifacts"] and action.action_type is ActionType.EXTRACT:
                    self._write_artifact(
                        task["id"],
                        session_id,
                        step["id"],
                        f"extraction-{step['sequence']}.json",
                        "structured_extraction",
                        safe_result,
                    )
                return safe_result
            except RetryableExecutionError as exc:
                duration = time.monotonic() - started
                error = redact_text(str(exc) or type(exc).__name__, limit=500)
                if attempts <= config.max_retries:
                    self.audit.record(
                        "step_retry_scheduled",
                        task_id=task["id"],
                        session_id=session_id,
                        step_id=step["id"],
                        action_type=action.action_type.value,
                        execution_outcome="retryable_failure",
                        attempt=attempts,
                        duration_seconds=round(duration, 3),
                        sanitized_error=error,
                    )
                    continue
                self.store.update_step(
                    step["id"],
                    status=StepStatus.FAILED.value,
                    completed_at=utc_now(),
                    error_message=error,
                    attempts=attempts,
                )
                self._audit_step(
                    task,
                    session_id,
                    self.store.get_step(step["id"]),
                    current_url,
                    "step_failed",
                    execution_outcome="retryable_failure_exhausted",
                    duration_seconds=round(duration, 3),
                    sanitized_error=error,
                )
                self._fail_task(task, session_id, error, step_id=step["id"])
                return None
            except Exception as exc:  # noqa: BLE001 -- persist and sanitize all executor failures.
                duration = time.monotonic() - started
                error = redact_text(str(exc) or type(exc).__name__, limit=500)
                self.store.update_step(
                    step["id"],
                    status=StepStatus.FAILED.value,
                    completed_at=utc_now(),
                    error_message=error,
                    attempts=attempts,
                )
                self._audit_step(
                    task,
                    session_id,
                    self.store.get_step(step["id"]),
                    current_url,
                    "step_failed",
                    execution_outcome="non_retryable_failure",
                    duration_seconds=round(duration, 3),
                    sanitized_error=error,
                )
                self._fail_task(task, session_id, error, step_id=step["id"])
                return None
        return None

    def _complete_task(self, task: dict[str, Any], session_id: str) -> dict[str, Any]:
        steps = self.store.list_steps(task["id"])
        results = [
            {"sequence": step["sequence"], "action_type": step["action_type"], "result": step["result"]}
            for step in steps
        ]
        result = {"steps": results, "last": results[-1]["result"] if results else None}
        now = utc_now()
        task = self.store.update_task(
            task["id"],
            status=TaskStatus.COMPLETED.value,
            completed_at=now,
            result_json=json.dumps(result, ensure_ascii=False),
        )
        self.store.update_session(session_id, session_status=SessionStatus.COMPLETED.value, ended_at=now)
        self.audit.record("task_completed", task_id=task["id"], session_id=session_id, execution_outcome="completed")
        if task["capture_artifacts"]:
            self._write_artifact(task["id"], session_id, None, "execution-summary.json", "execution_summary", result)
            timeline = [
                {
                    "sequence": step["sequence"],
                    "action_type": step["action_type"],
                    "status": step["status"],
                    "started_at": step["started_at"],
                    "completed_at": step["completed_at"],
                    "attempts": step["attempts"],
                }
                for step in steps
            ]
            self._write_artifact(task["id"], session_id, None, "action-timeline.json", "action_timeline", timeline)
        return self.store.get_task(task["id"])

    def _fail_task(
        self, task: dict[str, Any], session_id: str, reason: str, step_id: str | None = None
    ) -> dict[str, Any]:
        error = redact_text(reason, limit=500)
        now = utc_now()
        task = self.store.update_task(task["id"], status=TaskStatus.FAILED.value, completed_at=now, error_reason=error)
        self.store.update_session(
            session_id,
            session_status=SessionStatus.FAILED.value,
            ended_at=now,
            error_reason=error,
            recovery_json=json.dumps({"retryable": False, "failed_step_id": step_id}),
        )
        self.audit.record(
            "task_failed",
            task_id=task["id"],
            session_id=session_id,
            step_id=step_id,
            execution_outcome="failed",
            sanitized_error=error,
        )
        return task

    def _cancel(self, task: dict[str, Any], reason: str, session_id: str | None = None) -> dict[str, Any]:
        session = self.store.active_session(task["id"])
        session_id = session_id or (session["id"] if session else None)
        now = utc_now()
        task = self.store.update_task(
            task["id"], status=TaskStatus.CANCELLED.value, completed_at=now, error_reason=reason, cancel_requested=1
        )
        for approval in self.store.list_approvals(ApprovalStatus.PENDING.value):
            if approval["task_id"] == task["id"]:
                self.store.resolve_approval(approval["id"], ApprovalStatus.REJECTED.value, now)
        if session_id:
            self.store.update_session(
                session_id, session_status=SessionStatus.CANCELLED.value, ended_at=now, error_reason=reason
            )
        self.audit.record(
            "task_cancelled", task_id=task["id"], session_id=session_id, execution_outcome="cancelled", reason=reason
        )
        return task

    def _create_session(self, task_id: str) -> dict[str, Any]:
        session = self.store.create_session(
            {
                "id": f"session_{uuid4().hex}",
                "task_id": task_id,
                "session_status": SessionStatus.RUNNING.value,
                "started_at": utc_now(),
                "ended_at": None,
                "browser_target": getattr(self.executor, "browser_target", "unknown"),
                "error_reason": None,
                "recovery_json": json.dumps({"bounded_retries": True}),
            }
        )
        self.audit.record(
            "browser_session_started",
            task_id=task_id,
            session_id=session["id"],
            browser_target=session["browser_target"],
        )
        return session

    def _create_step(
        self,
        task_id: str,
        session_id: str,
        sequence: int,
        action: ActionRequest,
        outcome: PolicyOutcome,
        reason: str,
    ) -> dict[str, Any]:
        status = StepStatus.FAILED if outcome is PolicyOutcome.DENY else StepStatus.RUNNING
        return self.store.create_step(
            {
                "id": f"step_{uuid4().hex}",
                "task_id": task_id,
                "session_id": session_id,
                "sequence": sequence,
                "action_type": action.action_type.value,
                "target": redact_text(action.target, limit=2_048) if action.target else None,
                "status": status.value,
                "started_at": utc_now() if status is StepStatus.RUNNING else None,
                "completed_at": None,
                "error_message": None,
                "metadata_json": json.dumps(redact(action.metadata), ensure_ascii=False),
                "policy_decision": outcome.value,
                "policy_reason": reason,
                "attempts": 0,
                "action_json": json.dumps(redact(action.to_dict()), ensure_ascii=False),
                "result_json": None,
            }
        )

    def _create_approval(self, task_id: str, step_id: str, action: ActionRequest, reason: str) -> dict[str, Any]:
        return self.store.create_approval(
            {
                "id": f"approval_{uuid4().hex}",
                "task_id": task_id,
                "step_id": step_id,
                "action_category": action.action_type.value,
                "reason": reason,
                "status": ApprovalStatus.PENDING.value,
                "created_at": utc_now(),
                "resolved_at": None,
            }
        )

    def _write_artifact(
        self,
        task_id: str,
        session_id: str,
        step_id: str | None,
        filename: str,
        kind: str,
        value: Any,
    ) -> None:
        path = self.artifacts.write_json(task_id, filename, value)
        self.store.create_artifact(
            {
                "id": f"artifact_{uuid4().hex}",
                "task_id": task_id,
                "session_id": session_id,
                "step_id": step_id,
                "kind": kind,
                "relative_path": self.artifacts.relative_path(path),
                "created_at": utc_now(),
                "metadata_json": json.dumps({"content_type": "application/json"}),
            }
        )

    def _audit_step(
        self,
        task: dict[str, Any],
        session_id: str,
        step: dict[str, Any],
        page_url: str | None,
        event_type: str,
        **details: Any,
    ) -> None:
        domain = urlparse(page_url).hostname if page_url else None
        self.audit.record(
            event_type,
            task_id=task["id"],
            session_id=session_id,
            step_id=step["id"],
            action_type=step["action_type"],
            page_url=page_url,
            page_domain=domain,
            policy_decision=step["policy_decision"],
            approval_state=details.pop("approval_state", None),
            **details,
        )

    def _current_url(self, task_id: str) -> str | None:
        current = None
        for step in self.store.list_steps(task_id):
            if step["status"] == StepStatus.COMPLETED.value and step["action_type"] == ActionType.NAVIGATE.value:
                current = step["action"].get("target")
        return current

    @staticmethod
    def _elapsed_seconds(started_at: str) -> float:
        return max(0.0, (datetime.fromisoformat(utc_now()) - datetime.fromisoformat(started_at)).total_seconds())

    @staticmethod
    def _policy_config(task: dict[str, Any]) -> PolicyConfig:
        return PolicyConfig(
            allowed_domains=tuple(task["allowed_domains"]),
            blocked_domains=tuple(task["blocked_domains"]),
            max_steps=task["max_steps"],
            task_timeout_seconds=task["task_timeout_seconds"],
            step_timeout_seconds=task["step_timeout_seconds"],
            max_retries=task["max_retries"],
            allow_downloads=task["allow_downloads"],
            sensitive_actions=frozenset(ActionType(item) for item in task["sensitive_actions"]),
        )
