"""MCP entry point exposing only Avrixo governed browser operations."""

from __future__ import annotations

import functools
import json
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .governance.redaction import redact, redact_text
from .governance.service import BrowserOpsService

SERVER = MCPServer("avrixo-browser-ops")
_SERVICE: BrowserOpsService | None = None


def _service() -> BrowserOpsService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = BrowserOpsService()
    return _SERVICE


def _dump(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False, allow_nan=False, default=str)


def _tool(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return _dump(function(*args, **kwargs))
        except (KeyError, ValueError, RuntimeError) as exc:
            raise ToolError(redact_text(str(exc), limit=500)) from exc

    return SERVER.tool(name=function.__name__, description=function.__doc__ or "")(wrapper)


@_tool
def browser_task_create(
    objective: str,
    actions_json: str,
    allowed_domains: list[str],
    blocked_domains: list[str] | None = None,
    max_steps: int = 25,
    task_timeout_seconds: float = 300.0,
    step_timeout_seconds: float = 30.0,
    max_retries: int = 2,
    allow_downloads: bool = False,
    capture_artifacts: bool = False,
):
    """Create a governed task. actions_json is a JSON array of constrained action objects."""
    try:
        actions = json.loads(actions_json)
    except json.JSONDecodeError as exc:
        raise ValueError("actions_json must be valid JSON") from exc
    if not isinstance(actions, list):
        raise ValueError("actions_json must decode to an array")
    return _service().create_task(
        objective=objective,
        actions=actions,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains or (),
        max_steps=max_steps,
        task_timeout_seconds=task_timeout_seconds,
        step_timeout_seconds=step_timeout_seconds,
        max_retries=max_retries,
        allow_downloads=allow_downloads,
        capture_artifacts=capture_artifacts,
        created_by="mcp-client",
    )


@_tool
def browser_task_run(task_id: str):
    """Run or resume a task until completion, failure, cancellation, or an approval gate."""
    return _service().run_task(task_id)


@_tool
def browser_task_status(task_id: str):
    """Return durable task state and its ordered execution steps."""
    service = _service()
    return {"task": service.get_task(task_id), "steps": service.list_steps(task_id)}


@_tool
def browser_task_cancel(task_id: str):
    """Request cancellation; execution stops at the next safe action boundary."""
    return _service().cancel_task(task_id)


@_tool
def browser_approval_list(status: str = "pending"):
    """List approval requests by status; use all to return every status."""
    return _service().list_approvals(None if status == "all" else status)


@_tool
def browser_approval_resolve(approval_id: str, decision: str):
    """Approve or reject one pending action. Approval never runs the task implicitly."""
    if decision == "approve":
        return _service().approve(approval_id)
    if decision == "reject":
        return _service().reject(approval_id)
    raise ValueError("decision must be approve or reject")


@_tool
def browser_task_artifacts(task_id: str):
    """List opt-in, root-confined artifacts for a task."""
    return _service().list_artifacts(task_id)


def main() -> None:
    """Run the governed MCP server over stdio."""
    SERVER.run()


if __name__ == "__main__":
    main()
