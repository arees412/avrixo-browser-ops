"""Central policy decisions for governed browser actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from .models import ActionRequest, ActionType, PolicyOutcome

DEFAULT_SENSITIVE_ACTIONS = frozenset(
    {
        ActionType.SUBMIT_FORM,
        ActionType.SEND_MESSAGE,
        ActionType.PUBLISH_CONTENT,
        ActionType.CONFIRM_PURCHASE,
        ActionType.DELETE_DATA,
        ActionType.DOWNLOAD_FILE,
        ActionType.MODIFY_ACCOUNT_SETTINGS,
    }
)


def normalize_domain(value: str) -> str:
    """Normalize a hostname or URL to an ASCII lowercase hostname."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("domain entries must be non-empty strings")
    candidate = value.strip().lower().rstrip(".")
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"invalid domain: {value!r}")
    try:
        return parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid domain: {value!r}") from exc


def domain_matches(hostname: str, configured: str) -> bool:
    """Match an exact hostname or a true subdomain, never a string suffix peer."""
    host = normalize_domain(hostname)
    rule = normalize_domain(configured)
    return host == rule or host.endswith(f".{rule}")


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    allowed_domains: tuple[str, ...]
    blocked_domains: tuple[str, ...] = ()
    max_steps: int = 25
    task_timeout_seconds: float = 300.0
    step_timeout_seconds: float = 30.0
    max_retries: int = 2
    allow_downloads: bool = False
    sensitive_actions: frozenset[ActionType] = field(default_factory=lambda: DEFAULT_SENSITIVE_ACTIONS)

    def __post_init__(self) -> None:
        normalized_allowed = tuple(dict.fromkeys(normalize_domain(item) for item in self.allowed_domains))
        normalized_blocked = tuple(dict.fromkeys(normalize_domain(item) for item in self.blocked_domains))
        if not normalized_allowed:
            raise ValueError("at least one allowed domain is required")
        if not 1 <= self.max_steps <= 500:
            raise ValueError("max_steps must be between 1 and 500")
        if not 0 < self.task_timeout_seconds <= 86_400:
            raise ValueError("task_timeout_seconds must be between 0 and 86400")
        if not 0 < self.step_timeout_seconds <= self.task_timeout_seconds:
            raise ValueError("step_timeout_seconds must be positive and no greater than task timeout")
        if not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        object.__setattr__(self, "allowed_domains", normalized_allowed)
        object.__setattr__(self, "blocked_domains", normalized_blocked)


@dataclass(frozen=True, slots=True)
class PolicyContext:
    sequence: int
    elapsed_seconds: float
    current_url: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: PolicyOutcome
    reason: str


class PolicyEngine:
    """Return explicit allow/deny/approval decisions before every action."""

    def __init__(self, config: PolicyConfig):
        self.config = config

    def evaluate(self, action: ActionRequest, context: PolicyContext) -> PolicyDecision:
        if context.sequence > self.config.max_steps:
            return PolicyDecision(PolicyOutcome.DENY, "maximum execution steps exceeded")
        if context.elapsed_seconds >= self.config.task_timeout_seconds:
            return PolicyDecision(PolicyOutcome.DENY, "task timeout exceeded")

        url = action.target if action.action_type is ActionType.NAVIGATE else context.current_url
        if url:
            domain_decision = self._evaluate_url(url)
            if domain_decision is not None:
                return domain_decision
        elif action.action_type is ActionType.NAVIGATE:
            return PolicyDecision(PolicyOutcome.DENY, "navigation target is required")
        elif action.action_type is not ActionType.WAIT:
            return PolicyDecision(PolicyOutcome.DENY, "active page URL is unavailable for domain evaluation")

        if action.action_type is ActionType.DOWNLOAD_FILE and not self.config.allow_downloads:
            return PolicyDecision(PolicyOutcome.DENY, "downloads are disabled by policy")
        if action.action_type in self.config.sensitive_actions:
            return PolicyDecision(
                PolicyOutcome.REQUIRES_APPROVAL,
                f"{action.action_type.value} is classified as a sensitive action",
            )
        return PolicyDecision(PolicyOutcome.ALLOW, "action satisfies configured policy")

    def _evaluate_url(self, url: str) -> PolicyDecision | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return PolicyDecision(PolicyOutcome.DENY, "only http and https navigation is allowed")
        if not parsed.hostname or parsed.username or parsed.password:
            return PolicyDecision(PolicyOutcome.DENY, "navigation URL is invalid or contains credentials")
        host = normalize_domain(parsed.hostname)
        if any(domain_matches(host, item) for item in self.config.blocked_domains):
            return PolicyDecision(PolicyOutcome.DENY, f"domain {host} is blocked")
        if not any(domain_matches(host, item) for item in self.config.allowed_domains):
            return PolicyDecision(PolicyOutcome.DENY, f"domain {host} is not in the allowed list")
        return None
