import json
import sqlite3

import pytest

from aerospace_simulator.workspace_database import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceDatabase,
    WorkspaceDatabaseError,
)


def test_workspace_database_uses_wal_and_persists_records(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    database = WorkspaceDatabase(path)
    database.upsert_workflow(_workflow_record())
    database.close()

    with sqlite3.connect(path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        schema_version = connection.execute(
            "SELECT version FROM workspace_schema WHERE singleton = 1"
        ).fetchone()[0]

    reopened = WorkspaceDatabase(path)
    try:
        records = reopened.load_workflows()
    finally:
        reopened.close()

    assert journal_mode == "wal"
    assert schema_version == WORKSPACE_SCHEMA_VERSION
    assert records == [_workflow_record()]


def test_workspace_database_rejects_future_schema(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    database = WorkspaceDatabase(path)
    database.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workspace_schema SET version = ? WHERE singleton = 1",
            (WORKSPACE_SCHEMA_VERSION + 1,),
        )

    with pytest.raises(WorkspaceDatabaseError, match="newer"):
        WorkspaceDatabase(path)


def test_workspace_database_rejects_invalid_json_record(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    database = WorkspaceDatabase(path)
    database.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO workflows (
                workflow_id,
                name,
                status,
                created_at,
                record_json
            )
            VALUES ('broken', 'Broken', 'failed', '2026-01-01T00:00:00Z', ?)
            """,
            ("not-json",),
        )

    reopened = WorkspaceDatabase(path)
    try:
        with pytest.raises(WorkspaceDatabaseError, match="invalid"):
            reopened.load_workflows()
    finally:
        reopened.close()


def test_workspace_database_rejects_invalid_workflow_id(tmp_path):
    database = WorkspaceDatabase(tmp_path / "workspace.sqlite3")
    record = _workflow_record()
    record["workflow_id"] = "../outside"
    try:
        with pytest.raises(WorkspaceDatabaseError, match="hexadecimal"):
            database.upsert_workflow(record)
    finally:
        database.close()


def test_workspace_database_rejects_mismatched_index_id(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    database = WorkspaceDatabase(path)
    record = _workflow_record()
    database.upsert_workflow(record)
    database.close()

    record["workflow_id"] = "b" * 32
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflows SET record_json = ?",
            (json.dumps(record),),
        )

    reopened = WorkspaceDatabase(path)
    try:
        with pytest.raises(WorkspaceDatabaseError, match="does not match"):
            reopened.load_workflows()
    finally:
        reopened.close()


def test_workspace_database_normalizes_initialization_errors(tmp_path):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("blocked\n", encoding="utf-8")

    with pytest.raises(WorkspaceDatabaseError, match="unable to initialize"):
        WorkspaceDatabase(blocked_parent / "workspace.sqlite3")


def test_workspace_database_normalizes_sqlite_operation_errors(tmp_path):
    database = WorkspaceDatabase(tmp_path / "workspace.sqlite3")
    database.close()

    with pytest.raises(WorkspaceDatabaseError, match="unable to read"):
        database.load_workflows()
    with pytest.raises(WorkspaceDatabaseError, match="unable to update"):
        database.upsert_workflow(_workflow_record())
    with pytest.raises(WorkspaceDatabaseError, match="unable to delete"):
        database.delete_workflow("a" * 32)


def _workflow_record() -> dict[str, object]:
    return json.loads(
        """
        {
          "record_schema": "wms.aerospace.workflow_record.v1",
          "workflow_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "name": "Persistent workflow",
          "status": "completed",
          "created_at": "2026-01-01T00:00:00+00:00",
          "started_at": "2026-01-01T00:00:01+00:00",
          "completed_at": "2026-01-01T00:00:02+00:00",
          "provenance": null,
          "tasks": []
        }
        """
    )
