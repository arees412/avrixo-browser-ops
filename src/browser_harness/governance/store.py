"""Small SQLite state store for tasks, sessions, steps, approvals, and artifacts."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

_TASK_JSON = {
    "actions_json": "actions",
    "allowed_domains_json": "allowed_domains",
    "blocked_domains_json": "blocked_domains",
    "sensitive_actions_json": "sensitive_actions",
    "result_json": "result",
}
_SESSION_JSON = {"recovery_json": "recovery_metadata"}
_STEP_JSON = {"action_json": "action", "metadata_json": "metadata", "result_json": "result"}
_ARTIFACT_JSON = {"metadata_json": "metadata"}


class TaskStore:
    """Thread-safe SQLite access with explicit update field allowlists."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, objective TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
            max_steps INTEGER NOT NULL, task_timeout_seconds REAL NOT NULL,
            step_timeout_seconds REAL NOT NULL, max_retries INTEGER NOT NULL,
            allowed_domains_json TEXT NOT NULL, blocked_domains_json TEXT NOT NULL,
            sensitive_actions_json TEXT NOT NULL, allow_downloads INTEGER NOT NULL,
            capture_artifacts INTEGER NOT NULL, created_by TEXT NOT NULL,
            actions_json TEXT NOT NULL, result_json TEXT, error_reason TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            session_status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
            browser_target TEXT, error_reason TEXT, recovery_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS steps (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL, action_type TEXT NOT NULL, target TEXT,
            status TEXT NOT NULL, started_at TEXT, completed_at TEXT, error_message TEXT,
            metadata_json TEXT NOT NULL, policy_decision TEXT NOT NULL,
            policy_reason TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            action_json TEXT NOT NULL, result_json TEXT,
            UNIQUE(task_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            step_id TEXT NOT NULL UNIQUE REFERENCES steps(id) ON DELETE CASCADE,
            action_category TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            step_id TEXT REFERENCES steps(id) ON DELETE SET NULL, kind TEXT NOT NULL,
            relative_path TEXT NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);
        """
        with self._lock, self._connect() as connection:
            connection.executescript(schema)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    @staticmethod
    def _decode(row: sqlite3.Row | None, json_columns: dict[str, str]) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        for column, output in json_columns.items():
            raw = value.pop(column, None)
            value[output] = json.loads(raw) if raw is not None else None
        for flag in ("allow_downloads", "capture_artifacts", "cancel_requested"):
            if flag in value:
                value[flag] = bool(value[flag])
        return value

    def create_task(self, task: dict[str, Any]) -> dict[str, Any]:
        columns = (
            "id",
            "objective",
            "status",
            "created_at",
            "started_at",
            "completed_at",
            "max_steps",
            "task_timeout_seconds",
            "step_timeout_seconds",
            "max_retries",
            "allowed_domains_json",
            "blocked_domains_json",
            "sensitive_actions_json",
            "allow_downloads",
            "capture_artifacts",
            "created_by",
            "actions_json",
            "result_json",
            "error_reason",
            "cancel_requested",
        )
        values = [task.get(column) for column in columns]
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT INTO tasks ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
        return self.get_task(task["id"])

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        decoded = self._decode(row, _TASK_JSON)
        if decoded is None:
            raise KeyError(f"task not found: {task_id}")
        return decoded

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [self._decode(row, _TASK_JSON) for row in rows]  # type: ignore[misc]

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"status", "started_at", "completed_at", "result_json", "error_reason", "cancel_requested"}
        self._update("tasks", task_id, changes, allowed)
        return self.get_task(task_id)

    def create_session(self, session: dict[str, Any]) -> dict[str, Any]:
        columns = (
            "id",
            "task_id",
            "session_status",
            "started_at",
            "ended_at",
            "browser_target",
            "error_reason",
            "recovery_json",
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT INTO sessions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [session.get(column) for column in columns],
            )
        return self.get_session(session["id"])

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        decoded = self._decode(row, _SESSION_JSON)
        if decoded is None:
            raise KeyError(f"session not found: {session_id}")
        return decoded

    def active_session(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE task_id = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._decode(row, _SESSION_JSON)

    def update_session(self, session_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"session_status", "ended_at", "browser_target", "error_reason", "recovery_json"}
        self._update("sessions", session_id, changes, allowed)
        return self.get_session(session_id)

    def create_step(self, step: dict[str, Any]) -> dict[str, Any]:
        columns = (
            "id",
            "task_id",
            "session_id",
            "sequence",
            "action_type",
            "target",
            "status",
            "started_at",
            "completed_at",
            "error_message",
            "metadata_json",
            "policy_decision",
            "policy_reason",
            "attempts",
            "action_json",
            "result_json",
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT INTO steps ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [step.get(column) for column in columns],
            )
        return self.get_step(step["id"])

    def get_step(self, step_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
        decoded = self._decode(row, _STEP_JSON)
        if decoded is None:
            raise KeyError(f"step not found: {step_id}")
        return decoded

    def step_for_sequence(self, task_id: str, sequence: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM steps WHERE task_id = ? AND sequence = ?", (task_id, sequence)
            ).fetchone()
        return self._decode(row, _STEP_JSON)

    def list_steps(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT * FROM steps WHERE task_id = ? ORDER BY sequence", (task_id,)).fetchall()
        return [self._decode(row, _STEP_JSON) for row in rows]  # type: ignore[misc]

    def update_step(self, step_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"status", "started_at", "completed_at", "error_message", "attempts", "result_json"}
        self._update("steps", step_id, changes, allowed)
        return self.get_step(step_id)

    def create_approval(self, approval: dict[str, Any]) -> dict[str, Any]:
        columns = ("id", "task_id", "step_id", "action_category", "reason", "status", "created_at", "resolved_at")
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT INTO approvals ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [approval.get(column) for column in columns],
            )
        return self.get_approval(approval["id"])

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"approval not found: {approval_id}")
        return dict(row)

    def approval_for_step(self, step_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE step_id = ?", (step_id,)).fetchone()
        return dict(row) if row is not None else None

    def list_approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM approvals"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def resolve_approval(self, approval_id: str, status: str, resolved_at: str) -> dict[str, Any]:
        self._update(
            "approvals", approval_id, {"status": status, "resolved_at": resolved_at}, {"status", "resolved_at"}
        )
        return self.get_approval(approval_id)

    def create_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        columns = ("id", "task_id", "session_id", "step_id", "kind", "relative_path", "created_at", "metadata_json")
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT INTO artifacts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [artifact.get(column) for column in columns],
            )
        return self.get_artifact(artifact["id"])

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        decoded = self._decode(row, _ARTIFACT_JSON)
        if decoded is None:
            raise KeyError(f"artifact not found: {artifact_id}")
        return decoded

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at", (task_id,)
            ).fetchall()
        return [self._decode(row, _ARTIFACT_JSON) for row in rows]  # type: ignore[misc]

    def _update(self, table: str, row_id: str, changes: dict[str, Any], allowed: set[str]) -> None:
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported {table} fields: {', '.join(sorted(unknown))}")
        if not changes:
            return
        assignments = ", ".join(f"{column} = ?" for column in changes)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                [*changes.values(), row_id],
            )
            if cursor.rowcount != 1:
                raise KeyError(f"{table.rstrip('s')} not found: {row_id}")
