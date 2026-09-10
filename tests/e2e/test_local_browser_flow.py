"""Local-only BrowserOps flows with no private profile or external website."""

from __future__ import annotations

import os
import re
import threading
import urllib.request
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from browser_harness.governance import ActionRequest, ActionType, BrowserOpsService, HarnessExecutor


class QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def local_fixture(tmp_path: Path) -> Iterator[str]:
    source = Path(__file__).parents[1] / "fixtures" / "catalog.html"
    public = tmp_path / "public"
    public.mkdir()
    (public / "index.html").write_bytes(source.read_bytes())

    def handler(*args: Any, **kwargs: Any) -> QuietFixtureHandler:
        return QuietFixtureHandler(*args, directory=str(public), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


class LocalHttpFixtureExecutor:
    """Deterministic transport substitute for CI orchestration verification."""

    browser_target = "local-http-fixture"

    def __init__(self):
        self.url: str | None = None
        self.html = ""
        self.calls: list[ActionType] = []

    def execute(self, action: ActionRequest, timeout_seconds: float) -> dict[str, Any]:
        self.calls.append(action.action_type)
        if action.action_type is ActionType.NAVIGATE:
            self.url = action.target
            with urllib.request.urlopen(action.target or "", timeout=timeout_seconds) as response:
                self.html = response.read().decode("utf-8")
            return {"url": self.url, "status": 200}
        if action.action_type is ActionType.EXTRACT:
            names = re.findall(r'class="name">([^<]+)<', self.html)
            scores = [int(item) for item in re.findall(r'data-score="(\d+)"', self.html)]
            return {
                "data": {"items": [dict(name=name, score=score) for name, score in zip(names, scores, strict=True)]}
            }
        if action.action_type is ActionType.SUBMIT_FORM:
            return {"ok": True, "approved": True}
        raise AssertionError(f"fixture executor received unexpected action: {action.action_type}")


def test_task_policy_local_fixture_extraction_audit_and_result(tmp_path: Path, local_fixture: str) -> None:
    executor = LocalHttpFixtureExecutor()
    service = BrowserOpsService(root=tmp_path / "state", executor=executor)
    task = service.create_task(
        objective="Extract a deterministic local public catalog",
        allowed_domains=["127.0.0.1"],
        actions=[
            {"action_type": "navigate", "target": local_fixture},
            {"action_type": "extract", "metadata": {"fields": [{"name": "items", "selector": ".product"}]}},
        ],
        capture_artifacts=True,
        created_by="e2e-test",
    )

    result = service.run_task(task["id"])

    assert result["status"] == "completed"
    assert result["result"]["last"]["data"]["items"] == [
        {"name": "Alpha", "score": 7},
        {"name": "Beta", "score": 9},
    ]
    assert executor.calls == [ActionType.NAVIGATE, ActionType.EXTRACT]
    assert {item["kind"] for item in service.list_artifacts(task["id"])} == {
        "structured_extraction",
        "execution_summary",
        "action_timeline",
    }
    assert any(event["event_type"] == "task_completed" for event in service.audit.read())


def test_task_approval_local_fixture_stops_then_resumes(tmp_path: Path, local_fixture: str) -> None:
    executor = LocalHttpFixtureExecutor()
    service = BrowserOpsService(root=tmp_path / "state", executor=executor)
    task = service.create_task(
        objective="Exercise the local approval gate",
        allowed_domains=["127.0.0.1"],
        actions=[
            {"action_type": "navigate", "target": local_fixture},
            {"action_type": "submit_form", "metadata": {"selector": "#confirm"}},
        ],
        created_by="e2e-test",
    )

    waiting = service.run_task(task["id"])
    assert waiting["status"] == "awaiting_approval"
    assert executor.calls == [ActionType.NAVIGATE]

    approval = service.list_approvals()[0]
    service.approve(approval["id"])
    completed = service.run_task(task["id"])

    assert completed["status"] == "completed"
    assert executor.calls == [ActionType.NAVIGATE, ActionType.SUBMIT_FORM]


@pytest.mark.skipif(os.environ.get("BH_RUN_BROWSER_E2E") != "1", reason="requires opt-in disposable Chrome/CDP")
def test_real_chrome_local_fixture_extraction(tmp_path: Path, local_fixture: str) -> None:
    service = BrowserOpsService(root=tmp_path / "state", executor=HarnessExecutor())
    task = service.create_task(
        objective="Validate governed extraction through a disposable Chrome/CDP session",
        allowed_domains=["127.0.0.1"],
        actions=[
            {"action_type": "navigate", "target": local_fixture},
            {
                "action_type": "extract",
                "metadata": {
                    "fields": [
                        {"name": "names", "selector": ".name", "multiple": True},
                        {"name": "scores", "selector": ".product", "attribute": "data-score", "multiple": True},
                    ]
                },
            },
        ],
        created_by="live-e2e-test",
    )

    result = service.run_task(task["id"])

    assert result["status"] == "completed"
    assert result["result"]["last"]["data"] == {"names": ["Alpha", "Beta"], "scores": ["7", "9"]}
