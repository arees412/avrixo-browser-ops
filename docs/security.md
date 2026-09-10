# Security

This document describes implemented controls and explicit limits. It is not a certification or an “enterprise-grade security” claim.

## Trust boundaries

- Task objectives, action targets, selectors, values, extraction schemas, MCP clients, and page content are untrusted input.
- The task service is the boundary that validates and persists requests.
- The policy engine is the only route from governed task input to the constrained browser executor.
- Chrome profiles, Browser Harness configuration, SQLite state, audit logs, and artifacts may contain sensitive operational context and require filesystem access controls.
- Websites remain external systems. An approved action can still have real consequences; approval is a control point, not proof that an action is harmless.

## Implemented controls

### Domain and navigation policy

Allowed domains are mandatory. Matching is exact or true-subdomain based, so `notexample.com` does not match `example.com`. Blocked domains take precedence. Governed navigation accepts only HTTP(S), rejects missing hostnames and URL userinfo, and evaluates the current page domain before non-navigation browser actions.

### Approval model

Form submission, sending messages, publishing, confirming purchases, deleting data, downloading files, and modifying account settings require approval by default. Pending actions are not sent to the executor. Approval and rejection are single-use persisted decisions. Approval does not automatically resume execution.

The current reference implementation does not authenticate approvers or implement role-based access control. Do not expose it as a multi-user service until approval identity and authorization are added.

### Input and code execution

Task input cannot contain raw JavaScript, raw CDP methods, Python, shell commands, or output paths. Structured extraction accepts bounded field names, CSS selectors, optional attribute names, and a `multiple` flag. Credential-like input keys/assignments and password/token form selectors are rejected before persistence.

### Logging and credentials

Audit and persisted execution results pass through recursive redaction. The sanitizer covers passwords, tokens, API keys, authorization, cookies, sessions, credentials, OTP/MFA fields, bearer/basic values, common secret assignments, and sensitive URL query/fragment values. Errors are length-bounded and sanitized.

No governed component intentionally logs passwords, cookies, access tokens, raw authentication headers, or API keys. Operators should still treat logs as sensitive because page URLs, titles, objectives, selectors, and extracted public data can reveal activity.

The inherited Browser Harness runtime has its own telemetry, recording, and logging behavior. Review and configure those upstream features separately before using a sensitive profile.

### Artifacts and recordings

Governed artifacts are disabled by default. When enabled, only sanitized JSON extraction results, summaries, and timelines are written. Identifiers and filenames are constrained, paths are resolved beneath the configured root, and writes use an atomic temporary file replacement. On POSIX systems the state directories/files are created with private modes where possible.

Browser recordings can capture private content. BrowserOps does not enable upstream recordings; `.env.example` defaults `BH_RECORD=0`. Screenshots and video are not automatically copied into governed artifacts.

### Retry and cancellation safety

Retries are finite and only occur for errors explicitly classified as retryable. Non-retryable failures stop immediately. Cancellation is checked at each safe action/attempt boundary. A crash or timeout during an external side effect may be ambiguous, so operators must reconcile state before manual replay.

## Browser-profile risk

A browser profile can expose authenticated sessions, messages, files, and account controls to automation. Use a dedicated least-privilege profile where possible, restrict allowed domains, keep recordings off, and do not place long-lived secrets in task input. Remote debugging expands the local attack surface; follow upstream installation guidance and do not expose the CDP port to untrusted networks.

## Artifact retention

No automatic retention or secure deletion policy is implemented. Operators are responsible for storage encryption, backups, retention windows, access control, deletion, and incident response. Do not store private/authenticated extraction results merely because the path is confined.

## Environment and repository hygiene

`.env`, SQLite state, JSONL audits, local virtual environments, caches, and `.browserops` state are ignored. `.env.example` contains placeholders only. CI runs high-confidence secret-pattern checks, but scanning does not prove that a repository is secret-free or that an exposed credential is invalid.

If a real secret is committed, revoke or rotate it first, update deployment secrets, then clean history only with explicit coordination.

## Intentionally unsupported

This project does not implement CAPTCHA, MFA, OTP, or security-control bypasses.

It also does not implement stealth/evasion controls, paywall bypasses, arbitrary remote code execution from tasks, automatic purchase confirmation, automatic approval, or unrestricted governed MCP browser primitives.
