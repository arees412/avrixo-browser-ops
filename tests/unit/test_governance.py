"""Deterministic coverage for Avrixo BrowserOps governance controls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from browser_harness.governance.artifacts import ArtifactStore
from browser_harness.governance.audit import AuditTrail
from browser_harness.governance.executor import NonRetryableExecutionError, RetryableExecutionError
from browser_harness.governance.models import ActionRequest, ActionType, PolicyOutcome
from browser_harness.governance.policy import PolicyConfig, PolicyContext, PolicyEngine
from browser_harness.governance.redaction import REDACTED, redact
from browser_harness.governance.service import BrowserOpsService


class FixtureExecutor:
    """Local, browser-free executor used for deterministic unit and E2E flows."""

    browser_target = "local-fixture"

    def __init__(self, *, retryable_failures: int = 0, non_retryable: bool = False):
        self.retryable_failures = retryable_failures
        self.non_retryable = non_retryable
        self.calls: list[ActionRequest] = []

    def execute(self, action: ActionRequest, timeout_seconds: float) -> dict[str, Any]:
        self.calls.append(action)
        if self.non_retryable:
            raise NonRetryableExecutionError("fixture rejected action")
        if self.retryable_failures:
            self.retryable_failures -= 1
            raise RetryableExecutionError("fixture temporarily unavailable")
        if action.action_type is ActionType.NAVIGATE:
            return {"url": action.target, "title": "Local Fixture"}
        if action.action_type is ActionType.EXTRACT:
            return {"data": {"items": [{"name": "Alpha", "score": 7}, {"name": "Beta", "score": 9}]}}
        return {"ok": True, "action": action.action_type.value, "timeout": timeout_seconds}


def make_service(tmp_path: Path, executor: FixtureExecutor | None = None) -> BrowserOpsService:
    return BrowserOpsService(root=tmp_path / "state", executor=executor or FixtureExecutor())


def create_task(
    service: BrowserOpsService,
    actions: list[dict[str, Any]],
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "objective": "Extract public fixture data under policy control",
        "actions": actions,
        "allowed_domains": ["fixture.local"],
        "created_by": "test-suite",
    }
    values.update(overrides)
    return service.create_task(**values)


def navigate(url: str = "https://fixture.local/catalog") -> dict[str, Any]:
    return {"action_type": "navigate", "target": url}


def extract() -> dict[str, Any]:
    return {
        "action_type": "extract",
        "metadata": {"fields": [{"name": "name", "selector": ".name"}]},
    }


def test_allowed_domain_execution(tmp_path: Path) -> None:
    executor = FixtureExecutor()
    service = make_service(tmp_path, executor)
    task = create_task(service, [navigate()])

    result = service.run_task(task["id"])

    assert result["status"] == "completed"
    assert [call.action_type for call in executor.calls] == [ActionType.NAVIGATE]


def test_blocked_domain_rejection_precedes_allow_list(tmp_path: Path) -> None:
    executor = FixtureExecutor()
    service = make_service(tmp_path, executor)
    task = create_task(service, [navigate("https://private.fixture.local")], blocked_domains=["private.fixture.local"])

    result = service.run_task(task["id"])

    assert result["status"] == "failed"
    assert "blocked" in result["error_reason"]
    assert executor.calls == []


def test_maximum_step_limit_is_enforced(tmp_path: Path) -> None:
    executor = FixtureExecutor()
    service = make_service(tmp_path, executor)
    task = create_task(service, [navigate(), extract()], max_steps=1)

    result = service.run_task(task["id"])

    assert result["status"] == "failed"
    assert "maximum execution steps" in result["error_reason"]
    assert len(executor.calls) == 1


def test_sensitive_action_enters_approval_gate_without_execution(tmp_path: Path) -> None:
    executor = FixtureExecutor()
    service = make_service(tmp_path, executor)
    task = create_task(
        service,
        [navigate(), {"action_type": "submit_form", "metadata": {"selector": "#confirm"}}],
    )

    result = service.run_task(task["id"])
    result_again = service.run_task(task["id"])

    assert result["status"] == result_again["status"] == "awaiting_approval"
    assert len(executor.calls) == 1
    assert service.list_approvals()[0]["action_category"] == "submit_form"


def test_approval_acceptance_resumes_execution(tmp_path: Path) -> None:
    executor = FixtureExecutor()
    service = make_service(tmp_path, executor)
    task = create_task(
        service,
        [navigate(), {"action_type": "submit_form", "metadata": {"selector": "#confirm"}}],
    )
    service.run_task(task["id"])
    approval = service.list_approvals()[0]

    service.approve(approval["id"])
    result = service.run_task(task["id"])

    assert result["status"] == "completed"
    assert [call.action_type for call in executor.calls] == [ActionType.NAVIGATE, ActionType.SUBMIT_FORM]


def test_approval_rejection_cancels_task(tmp_path: Path) -> None:
    executor = FixtureExecutor()
    service = make_service(tmp_path, executor)
    task = create_task(
        service,
        [navigate(), {"action_type": "delete_data", "metadata": {"selector": "#delete"}}],
    )
    service.run_task(task["id"])

    service.reject(service.list_approvals()[0]["id"])

    assert service.get_task(task["id"])["status"] == "cancelled"
    assert len(executor.calls) == 1


def test_pending_task_cancellation_prevents_execution(tmp_path: Path) -> None:
    executor = FixtureExecutor()
    service = make_service(tmp_path, executor)
    task = create_task(service, [navigate()])

    result = service.cancel_task(task["id"])

    assert result["status"] == "cancelled"
    assert executor.calls == []


def test_cancellation_resolves_pending_approval(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    task = create_task(
        service,
        [navigate(), {"action_type": "publish_content", "metadata": {"selector": "#publish"}}],
    )
    service.run_task(task["id"])

    result = service.cancel_task(task["id"])

    assert result["status"] == "cancelled"
    assert service.list_approvals(None)[0]["status"] == "rejected"


def test_retryable_failure_is_bounded_and_recovers(tmp_path: Path) -> None:
    executor = FixtureExecutor(retryable_failures=1)
    service = make_service(tmp_path, executor)
    task = create_task(service, [navigate()], max_retries=2)

    result = service.run_task(task["id"])

    assert result["status"] == "completed"
    assert service.list_steps(task["id"])[0]["attempts"] == 2


def test_retryable_failure_stops_at_configured_bound(tmp_path: Path) -> None:
    executor = FixtureExecutor(retryable_failures=5)
    service = make_service(tmp_path, executor)
    task = create_task(service, [navigate()], max_retries=1)

    result = service.run_task(task["id"])

    assert result["status"] == "failed"
    assert service.list_steps(task["id"])[0]["attempts"] == 2


def test_non_retryable_failure_is_not_repeated(tmp_path: Path) -> None:
    executor = FixtureExecutor(non_retryable=True)
    service = make_service(tmp_path, executor)
    task = create_task(service, [navigate()], max_retries=3)

    result = service.run_task(task["id"])

    assert result["status"] == "failed"
    assert len(executor.calls) == 1


def test_audit_event_creation_records_policy_and_outcome(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    task = create_task(service, [navigate()])
    service.run_task(task["id"])

    events = service.audit.read()

    assert any(event["event_type"] == "policy_evaluated" and event["policy_decision"] == "allow" for event in events)
    assert any(
        event["event_type"] == "step_completed" and event["execution_outcome"] == "completed" for event in events
    )


def test_secret_and_log_redaction_is_recursive(tmp_path: Path) -> None:
    trail = AuditTrail(tmp_path / "audit.jsonl")
    trail.record(
        "example",
        authorization="Bearer top-secret",
        page_url="https://fixture.local/callback?access_token=real-token",
        nested={"api_key": "sk-live", "message": "password=hunter2"},
    )
    rendered = json.dumps(trail.read())

    assert "top-secret" not in rendered
    assert "real-token" not in rendered
    assert "sk-live" not in rendered
    assert "hunter2" not in rendered
    assert REDACTED in rendered


def test_structured_extraction_result_and_local_e2e(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    task = create_task(service, [navigate(), extract()])

    result = service.run_task(task["id"])

    assert result["status"] == "completed"
    assert result["result"]["last"]["data"]["items"][1] == {"name": "Beta", "score": 9}
    assert [step["status"] for step in service.list_steps(task["id"])] == ["completed", "completed"]


def test_opt_in_artifacts_are_root_confined_and_sanitized(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    task = create_task(service, [navigate(), extract()], capture_artifacts=True)
    service.run_task(task["id"])

    artifacts = service.list_artifacts(task["id"])

    assert {item["kind"] for item in artifacts} == {"structured_extraction", "execution_summary", "action_timeline"}
    assert all((service.artifacts.root / item["relative_path"]).is_file() for item in artifacts)


def test_artifact_path_traversal_is_rejected(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="invalid artifact"):
        store.write_json("task_safe", "../escape.json", {"ok": False})


def test_downloads_are_denied_unless_explicitly_enabled(tmp_path: Path) -> None:
    config = PolicyConfig(allowed_domains=("fixture.local",), allow_downloads=False)
    decision = PolicyEngine(config).evaluate(
        ActionRequest(ActionType.DOWNLOAD_FILE),
        PolicyContext(sequence=1, elapsed_seconds=0, current_url="https://fixture.local/file"),
    )

    assert decision.outcome is PolicyOutcome.DENY


def test_task_input_rejects_credential_bearing_fields(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with pytest.raises(ValueError, match="credential-bearing"):
        create_task(
            service,
            [navigate(), {"action_type": "fill", "metadata": {"selector": "#password", "value": "secret"}}],
        )


def test_task_input_rejects_credential_bearing_urls_before_persistence(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with pytest.raises(ValueError, match="credential-bearing URL query"):
        create_task(service, [navigate("https://fixture.local/callback?session_token=do-not-store")])

    assert service.list_tasks() == []


def test_redact_handles_nested_values_without_mutating_shape() -> None:
    value = {"items": [{"name": "public", "refresh_token": "secret"}]}

    assert redact(value) == {"items": [{"name": "public", "refresh_token": REDACTED}]}
