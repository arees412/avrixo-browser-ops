"""Credential-aware sanitization for persisted governance data."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(^|[_-])(password|passwd|secret|token|api[_-]?key|authorization|cookie|session|credential|otp|mfa)([_-]|$)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(password|passwd|client_secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\s*[:=]\s*([^\s,;]+)",
    re.IGNORECASE,
)


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(str(key)))


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if not parts.scheme or not parts.netloc:
            return value
        query = urlencode(
            [
                (key, REDACTED if is_sensitive_key(key) else item)
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        fragment = (
            REDACTED
            if parts.fragment and ("token=" in parts.fragment.lower() or "code=" in parts.fragment.lower())
            else parts.fragment
        )
        hostname = parts.hostname or ""
        netloc = hostname
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
    except (TypeError, ValueError):
        return value


def redact_text(value: str, *, limit: int = 2_000) -> str:
    text = redact_url(str(value))
    text = _BEARER.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    return text[:limit]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): REDACTED if is_sensitive_key(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def reject_sensitive_input(value: Any, path: str = "metadata") -> None:
    """Fail closed rather than persisting credentials supplied as task input."""
    if isinstance(value, dict):
        for key, item in value.items():
            if is_sensitive_key(str(key)):
                raise ValueError(f"credential-bearing field is not accepted: {path}.{key}")
            reject_sensitive_input(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            reject_sensitive_input(item, f"{path}[{index}]")
    elif isinstance(value, str) and (_BEARER.search(value) or _SECRET_ASSIGNMENT.search(value)):
        raise ValueError(f"credential-like value is not accepted: {path}")


def reject_credential_url(value: str, path: str = "target") -> None:
    """Reject credentials and sensitive query/fragment fields before persistence."""
    try:
        parts = urlsplit(value)
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError as exc:
        raise ValueError(f"invalid URL in {path}") from exc
    if parts.username or parts.password:
        raise ValueError(f"credential-bearing URL is not accepted: {path}")
    if any(is_sensitive_key(key) for key, _ in query):
        raise ValueError(f"credential-bearing URL query is not accepted: {path}")
    if parts.fragment and re.search(r"(?:^|[&])(code|token|access_token|id_token|session)=", parts.fragment, re.I):
        raise ValueError(f"credential-bearing URL fragment is not accepted: {path}")
