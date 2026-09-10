"""Opt-in artifact storage constrained to a configured root."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .redaction import redact

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.root, 0o700)

    def _safe_path(self, task_id: str, filename: str) -> Path:
        if not _SAFE_ID.fullmatch(task_id):
            raise ValueError("invalid artifact task identifier")
        if not _SAFE_ID.fullmatch(filename.rsplit(".", 1)[0]) or "/" in filename or "\\" in filename:
            raise ValueError("invalid artifact filename")
        destination = (self.root / task_id / filename).resolve()
        if self.root not in destination.parents:
            raise ValueError("artifact path escapes configured root")
        return destination

    def write_json(self, task_id: str, filename: str, value: Any) -> Path:
        if not filename.endswith(".json"):
            raise ValueError("JSON artifact filenames must end in .json")
        destination = self._safe_path(task_id, filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(redact(value), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        return destination

    def relative_path(self, path: Path) -> str:
        resolved = path.resolve()
        if self.root not in resolved.parents:
            raise ValueError("artifact path escapes configured root")
        return resolved.relative_to(self.root).as_posix()
