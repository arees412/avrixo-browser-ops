# Avrixo BrowserOps

### Governed AI Browser Automation Platform

A production-oriented browser-agent reference platform that adds policy controls, approval gates, execution auditing, resilient task orchestration, structured extraction, and MCP-based integrations around browser automation.

## Upstream & Fork Scope

This repository is a customized derivative of:

[browser-use/browser-harness](https://github.com/browser-use/browser-harness)

The original project, browser-control foundation, Git history, and upstream implementation remain credited to Browser Use and its contributors.

Avrixo-specific engineering work is documented in:

- [FORK_CHANGES.md](FORK_CHANGES.md)
- [UPSTREAM.md](UPSTREAM.md)

## What This Fork Adds

- Durable browser task, session, step, approval, and artifact state in SQLite.
- A centralized policy engine with allow/blocked domains, URL restrictions, step limits, task and step timeouts, download controls, and sensitive-action classification.
- Approval gates that stop execution until a pending action is explicitly approved or rejected.
- Bounded retry handling, retryable/non-retryable errors, cancellation at safe boundaries, and recovery metadata.
- An append-only JSONL audit trail with recursive credential, header, error, and URL redaction.
- Typed, schema-driven extraction without accepting arbitrary JavaScript or raw CDP commands from task input.
- Opt-in JSON artifacts confined to a configured root; browser recordings remain disabled by default.
- A separate governed MCP server whose tools route through the policy and approval layers.
- Deterministic policy, approval, lifecycle, retry, redaction, artifact, extraction, and orchestration tests.
- Fork-specific quality, packaging, documentation-link, and high-confidence secret-pattern CI checks.

## Execution model

```text
Task request
  -> policy evaluation
  -> browser session
  -> constrained action execution
  -> optional human approval
  -> audit event
  -> structured result / opt-in artifact
```

Each planned action receives an explicit `allow`, `deny`, or `requires_approval` decision. A blocked or out-of-scope domain never reaches the browser executor. Sensitive actions enter `awaiting_approval`; rerunning a task cannot skip a pending decision.

## Programmatic interface

```python
from pathlib import Path

from browser_harness.governance import BrowserOpsService

service = BrowserOpsService(root=Path(".browserops"))
task = service.create_task(
    objective="Extract titles from an approved public page",
    allowed_domains=["example.com"],
    actions=[
        {"action_type": "navigate", "target": "https://example.com/"},
        {
            "action_type": "extract",
            "metadata": {
                "fields": [
                    {"name": "title", "selector": "h1"},
                    {"name": "links", "selector": "a", "attribute": "href", "multiple": True},
                ]
            },
        },
    ],
)

result = service.run_task(task["id"])
print(result["status"], result["result"])
```

For an action such as `submit_form`, inspect `service.list_approvals()`, call `service.approve(approval_id)` or `service.reject(approval_id)`, then explicitly call `run_task` to resume. Approval never triggers execution by itself.

## Governed MCP

Install the optional dependency and run the Avrixo server:

```bash
python -m pip install -e ".[mcp]"
avrixo-browser-ops-mcp
```

It exposes:

- `browser_task_create`
- `browser_task_run`
- `browser_task_status`
- `browser_task_cancel`
- `browser_approval_list`
- `browser_approval_resolve`
- `browser_task_artifacts`

The inherited `browser-harness-mcp` server remains available for upstream compatibility. The Avrixo server is intentionally separate and does not expose the inherited raw JavaScript or CDP tools as governed operations.

## Policy defaults

- At least one allowed domain is required.
- Blocked domains take precedence over allowed domains, including subdomains.
- Only `http` and `https` navigation is accepted; credential-bearing URLs are rejected.
- Downloads are denied unless explicitly enabled and still require approval by default.
- Form submission, messages, publishing, purchases, deletion, downloads, and account-setting changes require approval by default.
- Retries are finite, and task cancellation is checked between every attempt and action.
- Credential-bearing input fields are rejected instead of persisted.
- Recordings and filesystem artifacts are opt-in.

## Development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev,mcp]"
python -m pytest tests/unit/test_governance.py tests/e2e -q
ruff format --check src/browser_harness/governance src/browser_harness/governed_mcp.py tests/unit/test_governance.py tests/e2e
ruff check src/browser_harness/governance src/browser_harness/governed_mcp.py tests/unit/test_governance.py tests/e2e
mypy src/browser_harness/governance
python -m build
```

See [development](docs/development.md) for browser connection, the deterministic local fixture flow, MCP configuration, artifacts, and troubleshooting.

## Design and security

- [Architecture](docs/architecture.md) — service boundaries, state machines, MCP routing, retries, persistence, and failure modes.
- [Security](docs/security.md) — trust boundaries, domain policy, credential handling, artifact risks, and intentional non-features.
- [Upstream MCP](docs/MCP.md) — inherited Browser Harness MCP behavior.

This repository is a reference implementation. It does not claim customers, adoption, benchmarks, certification, or deployment readiness. SQLite execution is single-process oriented, synchronous actions rely on cooperative executor timeouts, and approval identity/RBAC is not implemented. Operators must add authentication, authorization, retention, concurrency coordination, and deployment-specific browser isolation before multi-user use.

## Inherited Browser Harness foundation

Browser discovery, daemon lifecycle, browser-profile handling, CDP helpers, recordings, video tooling, the original MCP server, CLI installation, telemetry, agent workspace, domain skills, and inherited tests originated in `browser-use/browser-harness`. Avrixo does not claim original authorship of those components.

The inherited package/CLI remains named `browser-harness`; this fork adds an `avrixo-browser-ops-mcp` entry point instead of disguising the upstream package identity.

## License

MIT. The original 2026 Browser Use copyright and permission notice remain unchanged in [LICENSE](LICENSE).
