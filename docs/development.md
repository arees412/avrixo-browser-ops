# Development

## Prerequisites

- Python 3.11 or 3.12
- A virtual environment tool (`venv` or `uv`)
- Chrome with remote debugging enabled only for opt-in live integration tests

No paid AI API, private website, or real user profile is required for unit and deterministic orchestration tests.

## Setup

Using standard Python:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
python -m pip install -e ".[dev,mcp]"
```

Using `uv`:

```bash
uv sync --extra dev --extra mcp
```

Copy `.env.example` to `.env` only if upstream remote-browser functionality is needed. The governed deterministic tests do not require `BROWSER_USE_API_KEY`.

## Environment variables

- `BH_GOVERNANCE_DIR`: private directory for SQLite state, JSONL audit data, and the artifact root. Default: `<Browser Harness workspace>/governance`.
- `BH_RECORD`: inherited Browser Harness recording override. Keep `0` unless recording is explicitly required and appropriate.
- `BH_AGENT_WORKSPACE`, `BH_CONFIG_DIR`, `BH_RUNTIME_DIR`, `BH_TMP_DIR`: inherited Browser Harness path overrides.
- `BU_CDP_URL` / `BU_CDP_WS`: inherited explicit CDP connection settings.
- `BH_RUN_BROWSER_E2E=1`: opt into the real Chrome/CDP local-fixture test.

Never commit `.env`, SQLite state, audit logs, browser profiles, cookies, tokens, or recorded sessions.

## Tests

Run deterministic governance and local-fixture coverage:

```bash
python -m pytest tests/unit/test_governance.py tests/e2e -q
```

The tests cover allowed and blocked domains, maximum steps, approval pending/accept/reject, cancellation, retryable and non-retryable errors, audit events, secret redaction, structured extraction, artifact containment, credential-bearing input rejection, deterministic HTTP-fixture orchestration, and an approval-resume flow.

The deterministic HTTP fixture binds only to `127.0.0.1` on an ephemeral port. It does not use external websites or credentials.

## Optional live browser fixture

The opt-in test starts a loopback fixture server and routes the same governed task through `HarnessExecutor` and the inherited Chrome/CDP runtime. Use a disposable browser profile, enable remote debugging according to [upstream install guidance](../install.md), then run:

```bash
BH_RUN_BROWSER_E2E=1 python -m pytest tests/e2e/test_local_browser_flow.py -k real_chrome -q
```

On Windows PowerShell:

```powershell
$env:BH_RUN_BROWSER_E2E = "1"
python -m pytest tests/e2e/test_local_browser_flow.py -k real_chrome -q
```

This test is skipped in default CI so it never depends on a real user browser profile. CI runs the deterministic transport-independent E2E flow instead.

## Quality and packaging

```bash
ruff format --check src/browser_harness/governance src/browser_harness/governed_mcp.py tests/unit/test_governance.py tests/e2e
ruff check src/browser_harness/governance src/browser_harness/governed_mcp.py tests/unit/test_governance.py tests/e2e
mypy src/browser_harness/governance
python scripts/secret_scan.py
python scripts/check_docs.py
python -m build
```

## MCP usage

Start the governed server:

```bash
avrixo-browser-ops-mcp
```

`browser_task_create` accepts `actions_json`, an encoded array of constrained actions, plus domain and runtime policy. Call `browser_task_run`; if it returns `awaiting_approval`, list approvals, resolve exactly one, then call `browser_task_run` again.

The inherited `browser-harness-mcp` remains a separate compatibility server. Do not describe its raw helper tools as governed BrowserOps operations.

## Recordings and artifacts

`capture_artifacts=False` is the task default. Enabling it writes only sanitized JSON extraction/summary/timeline files beneath the configured artifact root. It does not enable upstream screenshots or recordings.

If upstream recording is explicitly enabled, review its output before sharing because screenshots can contain authenticated content even when trace text is redacted.

## Troubleshooting

- `domain ... is not in the allowed list`: add the exact host (or intended parent domain) to `allowed_domains`; do not use wildcard strings.
- `active page URL is unavailable`: begin browser interaction plans with a governed `navigate` action.
- `awaiting_approval`: resolve the pending approval and explicitly resume the task.
- `credential-bearing field is not accepted`: pass secrets through deployment/browser authentication mechanisms, never task metadata.
- `step timeout exceeded`: lower action scope or use an executor that cooperatively honors the configured timeout.
- Live test cannot attach to Chrome: keep deterministic tests as the required gate and follow [upstream install guidance](../install.md) for the isolated optional run.
