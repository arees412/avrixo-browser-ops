"""Browser executor boundary used by the governed runtime."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from .models import ActionRequest, ActionType


class RetryableExecutionError(RuntimeError):
    """A bounded retry may safely repeat the failed action."""


class NonRetryableExecutionError(RuntimeError):
    """Repeating the action would be unsafe or cannot succeed unchanged."""


class BrowserExecutor(Protocol):
    browser_target: str

    def execute(self, action: ActionRequest, timeout_seconds: float) -> dict[str, Any]: ...


class HarnessExecutor:
    """Adapter for inherited Browser Harness helpers.

    It intentionally exposes a constrained action vocabulary. Task input cannot
    supply raw JavaScript or raw CDP methods.
    """

    browser_target = "browser-harness-cdp"

    def execute(self, action: ActionRequest, timeout_seconds: float) -> dict[str, Any]:
        from browser_harness import helpers

        if action.action_type is ActionType.NAVIGATE:
            result = helpers.goto_url(action.target or "")
            helpers.wait_for_load(timeout=min(timeout_seconds, 15.0))
            return {"navigation": result, "page": helpers.page_info()}
        if action.action_type is ActionType.EXTRACT:
            return {"data": self._extract(helpers, action)}
        if action.action_type is ActionType.FILL:
            selector = self._required_string(action.metadata, "selector")
            value = self._required_string(action.metadata, "value", maximum=10_000)
            helpers.fill_input(selector, value, clear_first=bool(action.metadata.get("clear_first", True)))
            return {"ok": True}
        if action.action_type is ActionType.CLICK:
            x = self._bounded_number(action.metadata, "x", 0, 100_000)
            y = self._bounded_number(action.metadata, "y", 0, 100_000)
            helpers.click_at_xy(x, y)
            return {"ok": True}
        if action.action_type is ActionType.WAIT:
            seconds = self._bounded_number(action.metadata, "seconds", 0, min(timeout_seconds, 30.0))
            helpers.wait(seconds)
            return {"ok": True, "waited_seconds": seconds}
        if action.action_type is ActionType.SUBMIT_FORM:
            selector = self._required_string(action.metadata, "selector")
            expression = (
                "(()=>{const e=document.querySelector("
                + json.dumps(selector)
                + ");if(!e)throw new Error('submit target not found');e.click();return true})()"
            )
            return {"ok": bool(helpers.js(expression))}
        raise NonRetryableExecutionError(f"no constrained executor is implemented for {action.action_type.value}")

    @staticmethod
    def _required_string(metadata: dict[str, Any], key: str, maximum: int = 500) -> str:
        value = metadata.get(key)
        if not isinstance(value, str) or not value or len(value) > maximum:
            raise NonRetryableExecutionError(f"{key} must be a non-empty string of at most {maximum} characters")
        return value

    @staticmethod
    def _bounded_number(metadata: dict[str, Any], key: str, minimum: float, maximum: float) -> float:
        value = metadata.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
            raise NonRetryableExecutionError(f"{key} must be between {minimum} and {maximum}")
        return float(value)

    def _extract(self, helpers: Any, action: ActionRequest) -> dict[str, Any]:
        raw_fields = action.metadata.get("fields")
        if not isinstance(raw_fields, list) or not 1 <= len(raw_fields) <= 50:
            raise NonRetryableExecutionError("extract metadata.fields must contain 1 to 50 field objects")
        fields: list[dict[str, Any]] = []
        for raw in raw_fields:
            if not isinstance(raw, dict):
                raise NonRetryableExecutionError("each extraction field must be an object")
            name = raw.get("name")
            selector = raw.get("selector")
            attribute = raw.get("attribute")
            multiple = raw.get("multiple", False)
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
                raise NonRetryableExecutionError("extraction field names must be safe identifiers")
            if not isinstance(selector, str) or not selector or len(selector) > 500:
                raise NonRetryableExecutionError("extraction selectors must contain 1 to 500 characters")
            if attribute is not None and (
                not isinstance(attribute, str) or not re.fullmatch(r"[A-Za-z_:][-A-Za-z0-9_:.]{0,99}", attribute)
            ):
                raise NonRetryableExecutionError("extraction attributes must be valid attribute names")
            if not isinstance(multiple, bool):
                raise NonRetryableExecutionError("extraction multiple flags must be boolean")
            fields.append({"name": name, "selector": selector, "attribute": attribute, "multiple": multiple})
        encoded = json.dumps(fields, ensure_ascii=False)
        expression = (
            "(()=>{const specs="
            + encoded
            + ";const out={};for(const s of specs){const nodes=[...document.querySelectorAll(s.selector)];"
            "const read=(e)=>s.attribute?e.getAttribute(s.attribute):(e.textContent||'').trim();"
            "out[s.name]=s.multiple?nodes.map(read):(nodes[0]?read(nodes[0]):null);}return out})()"
        )
        value = helpers.js(expression)
        if not isinstance(value, dict):
            raise NonRetryableExecutionError("structured extraction did not return an object")
        return value
