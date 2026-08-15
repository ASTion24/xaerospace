from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path

WORKSPACE_SCHEMA_VERSION = 1
_WORKFLOW_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class WorkspaceDatabaseError(RuntimeError):
    """Raised when the durable workspace cannot be read or migrated safely."""


class WorkspaceDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        connection: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                timeout=30.0,
                check_same_thread=False,
            )
            self._connection = connection
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._migrate()
        except WorkspaceDatabaseError:
            if connection is not None:
                connection.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise WorkspaceDatabaseError(
                f"unable to initialize workspace database: {self.path}"
            ) from exc

    def load_workflows(self) -> list[dict[str, object]]:
        try:
            rows = self._connection.execute(
                """
                SELECT workflow_id, record_json
                FROM workflows
                ORDER BY created_at ASC, workflow_id ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise WorkspaceDatabaseError(
                f"unable to read workspace database: {self.path}"
            ) from exc
        documents: list[dict[str, object]] = []
        for row in rows:
            try:
                document = json.loads(row["record_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise WorkspaceDatabaseError(
                    "workspace contains an invalid workflow record"
                ) from exc
            if not isinstance(document, dict):
                raise WorkspaceDatabaseError(
                    "workspace workflow record must be a JSON object"
                )
            workflow_id = _required_workflow_id(document)
            if workflow_id != row["workflow_id"]:
                raise WorkspaceDatabaseError(
                    "workspace workflow id does not match its index record"
                )
            documents.append(document)
        return documents

    def upsert_workflow(self, document: Mapping[str, object]) -> None:
        workflow_id = _required_workflow_id(document)
        name = _required_string(document, "name")
        status = _required_string(document, "status")
        created_at = _required_string(document, "created_at")
        payload = json.dumps(
            dict(document),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO workflows (
                        workflow_id,
                        name,
                        status,
                        created_at,
                        updated_at,
                        record_json
                    )
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                    ON CONFLICT(workflow_id) DO UPDATE SET
                        name = excluded.name,
                        status = excluded.status,
                        created_at = excluded.created_at,
                        updated_at = CURRENT_TIMESTAMP,
                        record_json = excluded.record_json
                    """,
                    (workflow_id, name, status, created_at, payload),
                )
        except sqlite3.Error as exc:
            raise WorkspaceDatabaseError(
                f"unable to update workspace database: {self.path}"
            ) from exc

    def delete_workflow(self, workflow_id: str) -> None:
        try:
            with self._connection:
                cursor = self._connection.execute(
                    "DELETE FROM workflows WHERE workflow_id = ?",
                    (workflow_id,),
                )
        except sqlite3.Error as exc:
            raise WorkspaceDatabaseError(
                f"unable to delete from workspace database: {self.path}"
            ) from exc
        if cursor.rowcount != 1:
            raise WorkspaceDatabaseError(
                f"workflow disappeared during deletion: {workflow_id}"
            )

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_schema (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    version INTEGER NOT NULL
                )
                """
            )
            row = self._connection.execute(
                "SELECT version FROM workspace_schema WHERE singleton = 1"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO workspace_schema (singleton, version)
                    VALUES (1, ?)
                    """,
                    (WORKSPACE_SCHEMA_VERSION,),
                )
                version = WORKSPACE_SCHEMA_VERSION
            else:
                version = int(row["version"])
            if version > WORKSPACE_SCHEMA_VERSION:
                raise WorkspaceDatabaseError(
                    "workspace schema is newer than this Xaerospace version: "
                    f"{version} > {WORKSPACE_SCHEMA_VERSION}"
                )
            if version < 1:
                raise WorkspaceDatabaseError(
                    f"unsupported workspace schema version: {version}"
                )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    record_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS workflows_created_at_idx
                ON workflows (created_at DESC, workflow_id DESC)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS workflows_status_idx
                ON workflows (status, created_at DESC)
                """
            )


def _required_string(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise WorkspaceDatabaseError(f"workflow record {key} must be a string")
    return value


def _required_workflow_id(document: Mapping[str, object]) -> str:
    workflow_id = _required_string(document, "workflow_id")
    if not _WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
        raise WorkspaceDatabaseError(
            "workflow record workflow_id must be a 32-character hexadecimal id"
        )
    return workflow_id
