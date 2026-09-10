"""Append-only, redacted JSONL audit trail."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .redaction import redact


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class AuditTrail:
    """Write bounded, sanitized events without credentials or raw headers."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        self._lock = threading.Lock()

    def record(self, event_type: str, **details: Any) -> dict[str, Any]:
        event = {"timestamp": utc_now(), "event_type": event_type, **redact(details)}
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8", errors="replace") as stream:
                stream.write(line + "\n")
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        return event

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._lock:
            return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
