"""Typed domain models for governed browser operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"


class ActionType(StrEnum):
    NAVIGATE = "navigate"
    EXTRACT = "extract"
    CLICK = "click"
    FILL = "fill"
    WAIT = "wait"
    SUBMIT_FORM = "submit_form"
    SEND_MESSAGE = "send_message"
    PUBLISH_CONTENT = "publish_content"
    CONFIRM_PURCHASE = "confirm_purchase"
    DELETE_DATA = "delete_data"
    DOWNLOAD_FILE = "download_file"
    MODIFY_ACCOUNT_SETTINGS = "modify_account_settings"


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """One policy-evaluated action in a browser task plan."""

    action_type: ActionType
    target: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ActionRequest:
        if not isinstance(value, dict):
            raise ValueError("each action must be an object")
        raw_type = value.get("action_type")
        if not isinstance(raw_type, str):
            raise ValueError("action_type must be a string")
        try:
            action_type = ActionType(raw_type)
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(item.value for item in ActionType)
            raise ValueError(f"unsupported action_type; expected one of: {allowed}") from exc
        target = value.get("target")
        if target is not None and (not isinstance(target, str) or len(target) > 2_048):
            raise ValueError("action target must be a string of at most 2048 characters")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("action metadata must be an object")
        return cls(action_type=action_type, target=target, metadata=metadata)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["action_type"] = self.action_type.value
        return value
