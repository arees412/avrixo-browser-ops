# Fork Changes

This file lists functionality implemented specifically in the Avrixo derivative based on upstream revision `afbcc381b963040c19627d788e40c7e7663171ee`.

## Governed operations domain

- Typed action and lifecycle states for tasks, browser sessions, execution steps, approvals, policy decisions, and artifacts.
- SQLite persistence with task/session/step/approval/artifact tables, foreign keys, uniqueness constraints, and ordered state lookup.
- A programmatic `BrowserOpsService` for creating, listing, inspecting, running, resuming, cancelling, approving, rejecting, and listing artifacts.

## Policy and approvals

- Central `PolicyEngine.evaluate()` decisions with `allow`, `deny`, and `requires_approval` outcomes.
- Exact and subdomain-aware allowed/blocked domain checks, with blocked rules taking precedence.
- HTTP(S)-only navigation, credential-bearing URL rejection, maximum-step enforcement, task timeout checks, cooperative step timeout limits, finite retry configuration, and download restrictions.
- Default approval classification for form submission, messages, publishing, purchases, deletion, downloads, and account-setting changes.
- A persisted approval state machine that cannot resume while a decision is pending and cancels execution when rejected.

## Runtime, auditing, and artifacts

- A constrained Browser Harness executor for navigation, typed extraction, fill, coordinate click, wait, and approved form submission without task-supplied raw JavaScript or raw CDP.
- Explicit retryable and non-retryable failures, bounded retry attempts, cancellation checks at action boundaries, durable failure reasons, and recovery metadata.
- Append-only JSONL audit events carrying task/session/step identity, action type, page URL/domain, policy and approval state, outcome, duration, and sanitized errors.
- Recursive redaction for authorization values, cookies, passwords, tokens, API keys, credential assignments, and sensitive URL query/fragment values.
- Rejection of credential-bearing task fields before persistence.
- Opt-in structured-extraction, execution-summary, and action-timeline JSON artifacts with atomic writes and path traversal prevention.

## Governed MCP

- A separate `avrixo-browser-ops-mcp` server exposing governed task create/run/status/cancel, approval list/resolve, and artifact-list tools.
- Governed MCP inputs route through validation, policy, approval, persistence, audit, and constrained execution layers.

## Verification and documentation

- Deterministic tests covering domain policy, step limits, approvals, cancellation, retry classes, auditing, redaction, structured extraction, artifacts, and input validation.
- A deterministic local HTTP-fixture orchestration flow and an opt-in real Chrome/CDP fixture test.
- Fork-specific CI for formatting, lint, typing, unit/E2E tests, high-confidence secret patterns, Markdown links/Mermaid fences, and package builds.
- Architecture, security, and development documentation plus explicit upstream scope and attribution.

No inherited Browser Harness feature is listed above as Avrixo-authored work.
