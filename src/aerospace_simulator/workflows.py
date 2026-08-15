from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .handover import (
    HandoverSpec,
    HandoverValidationError,
    compile_handover,
)
from .outputs import write_outputs
from .protocol import BackendCapabilities, SimulationRequest, UnifiedSimulationResult
from .request_io import request_from_document
from .simulation import create_default_registry, run_request
from .workspace_database import WorkspaceDatabase, WorkspaceDatabaseError

MAX_WORKFLOW_TASKS = 12
MAX_WORKFLOW_HISTORY_PAGE_SIZE = 100
WORKFLOW_EXPORT_SCHEMA = "xaerospace.workflow.v1"
WORKFLOW_RECORD_SCHEMA = "xaerospace.workflow_record.v1"
ASSISTANT_PROVENANCE_SCHEMA = "xaerospace.assistant_provenance.v1"
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TERMINAL_WORKFLOW_STATES = {"completed", "failed", "interrupted"}
_TERMINAL_TASK_STATES = {"completed", "failed", "skipped", "interrupted"}
_WORKFLOW_STATES = {"queued", "running", *_TERMINAL_WORKFLOW_STATES}
_TASK_STATES = {"queued", "running", *_TERMINAL_TASK_STATES}


class WorkflowValidationError(ValueError):
    """Raised when a workflow cannot be submitted safely."""


class WorkflowNotFoundError(KeyError):
    """Raised when a workflow id is not present in the in-memory store."""


class WorkflowArtifactNotFoundError(KeyError):
    """Raised when a task artifact is not present in a workflow."""


class WorkflowConflictError(RuntimeError):
    """Raised when an active workflow cannot be mutated safely."""


@dataclass
class WorkflowTaskRecord:
    task_id: str
    order: int
    submitted_document: dict[str, object]
    request: SimulationRequest
    backend: BackendCapabilities
    handover: HandoverSpec | None = None
    handover_report: dict[str, object] | None = None
    status: str = "queued"
    started_at: str | None = None
    completed_at: str | None = None
    summary: dict[str, object] | None = None
    artifacts: dict[str, Path] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    artifact_integrity: dict[str, str] = field(default_factory=dict)
    error: dict[str, str] | None = None


@dataclass
class WorkflowRecord:
    workflow_id: str
    name: str
    status: str
    created_at: str
    tasks: list[WorkflowTaskRecord]
    provenance: dict[str, object] | None = None
    started_at: str | None = None
    completed_at: str | None = None


class WorkflowStore:
    def __init__(
        self,
        runs_root: str | Path,
        *,
        runner: Callable[[SimulationRequest], UnifiedSimulationResult] = run_request,
        output_writer: Callable[
            [UnifiedSimulationResult, str | Path], dict[str, Path]
        ] = write_outputs,
        database_path: str | Path | None = None,
    ) -> None:
        self._runs_root = Path(runs_root).resolve()
        self._runs_root.mkdir(parents=True, exist_ok=True)
        self._database = WorkspaceDatabase(
            database_path or self._runs_root.parent / "workspace.sqlite3"
        )
        self._runner = runner
        self._output_writer = output_writer
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="aerospace-workflow",
        )
        self._lock = threading.RLock()
        self._workflows: dict[str, WorkflowRecord] = {}
        self._restore_workflows()

    @property
    def runs_root(self) -> Path:
        return self._runs_root

    def submit(
        self,
        name: str,
        task_documents: Sequence[Mapping[str, object]],
        *,
        provenance: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        workflow_name = name.strip()
        if not workflow_name:
            raise WorkflowValidationError("workflow name must not be empty")
        if not task_documents:
            raise WorkflowValidationError("workflow must contain at least one task")
        if len(task_documents) > MAX_WORKFLOW_TASKS:
            raise WorkflowValidationError(
                f"workflow must not contain more than {MAX_WORKFLOW_TASKS} tasks"
            )

        registry = create_default_registry()
        task_records: list[WorkflowTaskRecord] = []
        task_ids: set[str] = set()
        for order, item in enumerate(task_documents, start=1):
            task_id_raw = item.get("task_id")
            if not isinstance(task_id_raw, str) or not _TASK_ID_PATTERN.fullmatch(
                task_id_raw
            ):
                raise WorkflowValidationError(
                    "task_id must match [A-Za-z0-9_-] and contain 1-64 characters"
                )
            if task_id_raw in task_ids:
                raise WorkflowValidationError(
                    f"workflow task ids must be unique: {task_id_raw}"
                )
            task_ids.add(task_id_raw)
            document_raw = item.get("document")
            request = request_from_document(document_raw)
            if not isinstance(document_raw, Mapping):
                raise WorkflowValidationError(
                    "workflow task document must be an object"
                )
            submitted_document = copy.deepcopy(dict(document_raw))
            backend = registry.select(request).capabilities
            handover_raw = item.get("handover")
            try:
                handover = (
                    None
                    if handover_raw is None
                    else HandoverSpec.from_mapping(handover_raw)
                )
            except HandoverValidationError as exc:
                raise WorkflowValidationError(str(exc)) from exc
            task_records.append(
                WorkflowTaskRecord(
                    task_id=task_id_raw,
                    order=order,
                    submitted_document=submitted_document,
                    request=request,
                    backend=backend,
                    handover=handover,
                )
            )
        self._validate_handovers(task_records)

        workflow_id = uuid4().hex
        record = WorkflowRecord(
            workflow_id=workflow_id,
            name=workflow_name,
            status="queued",
            created_at=_timestamp(),
            tasks=task_records,
            provenance=dict(provenance) if provenance is not None else None,
        )
        with self._lock:
            self._workflows[workflow_id] = record
            try:
                self._persist_locked(record)
            except Exception:
                del self._workflows[workflow_id]
                raise
        self._executor.submit(self._execute, workflow_id)
        result = self.get(workflow_id)
        return result

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, object]:
        if limit < 1 or limit > MAX_WORKFLOW_HISTORY_PAGE_SIZE:
            raise WorkflowValidationError(
                "workflow history limit must be between 1 and "
                f"{MAX_WORKFLOW_HISTORY_PAGE_SIZE}"
            )
        if offset < 0:
            raise WorkflowValidationError(
                "workflow history offset must be non-negative"
            )
        if status is not None and status not in _WORKFLOW_STATES:
            raise WorkflowValidationError(
                f"unsupported workflow history status: {status}"
            )
        with self._lock:
            records = sorted(
                self._workflows.values(),
                key=lambda item: (item.created_at, item.workflow_id),
                reverse=True,
            )
            if status is not None:
                records = [item for item in records if item.status == status]
            total = len(records)
            page = records[offset : offset + limit]
            items = [self._history_snapshot(item) for item in page]
        return {
            "workflows": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get(self, workflow_id: str) -> dict[str, object]:
        with self._lock:
            record = self._record(workflow_id)
            result = self._snapshot(record)
        return result

    def export_document(self, workflow_id: str) -> dict[str, object]:
        with self._lock:
            record = self._record(workflow_id)
            tasks = [
                {
                    "task_id": task.task_id,
                    "document": copy.deepcopy(task.submitted_document),
                    "handover": (
                        task.handover.document() if task.handover is not None else None
                    ),
                }
                for task in record.tasks
            ]
            return {
                "workflow_schema": WORKFLOW_EXPORT_SCHEMA,
                "source_workflow_id": record.workflow_id,
                "name": record.name,
                "provenance": copy.deepcopy(record.provenance),
                "tasks": tasks,
            }

    def artifact_path(
        self,
        workflow_id: str,
        task_id: str,
        artifact_name: str,
    ) -> Path:
        with self._lock:
            record = self._record(workflow_id)
            task = next(
                (item for item in record.tasks if item.task_id == task_id),
                None,
            )
            if task is None:
                raise WorkflowArtifactNotFoundError(
                    f"workflow task not found: {task_id}"
                )
            try:
                path = task.artifacts[artifact_name]
            except KeyError as exc:
                raise WorkflowArtifactNotFoundError(
                    f"workflow artifact not found: {artifact_name}"
                ) from exc
        if not path.is_file():
            raise WorkflowArtifactNotFoundError(
                f"workflow artifact file is missing: {artifact_name}"
            )
        expected_hash = task.artifact_hashes.get(artifact_name)
        if expected_hash is None or _sha256(path) != expected_hash:
            raise WorkflowArtifactNotFoundError(
                f"workflow artifact failed integrity validation: {artifact_name}"
            )
        return path

    def delete(self, workflow_id: str) -> None:
        quarantine: Path | None = None
        workflow_directory = self._runs_root / workflow_id
        with self._lock:
            record = self._record(workflow_id)
            if not is_terminal_workflow_status(record.status):
                raise WorkflowConflictError("active workflows cannot be deleted")
            if workflow_directory.exists():
                quarantine = self._runs_root / (f".delete-{workflow_id}-{uuid4().hex}")
                workflow_directory.replace(quarantine)
            try:
                self._database.delete_workflow(workflow_id)
            except Exception:
                if quarantine is not None and quarantine.exists():
                    quarantine.replace(workflow_directory)
                raise
            del self._workflows[workflow_id]
        if quarantine is not None:
            shutil.rmtree(quarantine)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._database.close()

    def _execute(self, workflow_id: str) -> None:
        with self._lock:
            record = self._record(workflow_id)
            record.status = "running"
            record.started_at = _timestamp()
            self._persist_locked(record)

        failed = False
        completed_results: dict[str, UnifiedSimulationResult] = {}
        for task in record.tasks:
            if failed:
                break
            with self._lock:
                task.status = "running"
                task.started_at = _timestamp()
                self._persist_locked(record)
            try:
                if task.handover is not None:
                    source_result = completed_results.get(task.handover.source_task_id)
                    if source_result is None:
                        raise HandoverValidationError(
                            "handover source result is unavailable"
                        )
                    task.request, task.handover_report = compile_handover(
                        task.handover,
                        source_result=source_result,
                        target_request=task.request,
                    )
                result = self._runner(task.request)
                output_directory = (
                    self._runs_root
                    / workflow_id
                    / f"{task.order:02d}-{_path_component(task.request.request_id)}"
                )
                artifacts = self._output_writer(result, output_directory)
                provenance_artifact = _write_assistant_provenance(
                    record,
                    task,
                    output_directory,
                )
                if provenance_artifact is not None:
                    artifacts = {
                        **artifacts,
                        "assistant_provenance": provenance_artifact,
                    }
            # The worker boundary must convert every backend or artifact failure
            # into a terminal workflow state instead of losing the executor task.
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    task.status = "failed"
                    task.completed_at = _timestamp()
                    task.error = {
                        "type": type(exc).__name__,
                        "message": str(exc) or "simulation failed without a message",
                    }
                    if task.handover is not None and task.handover_report is None:
                        task.handover_report = {
                            **task.handover.document(),
                            "status": "failed",
                            "error": task.error["message"],
                        }
                    failed = True
                    self._persist_locked(record)
            else:
                completed_results[task.task_id] = result
                with self._lock:
                    task.status = "completed"
                    task.completed_at = _timestamp()
                    task.summary = result.document(include_samples=False)
                    task.artifacts = artifacts
                    task.artifact_hashes = {
                        name: _sha256(path) for name, path in artifacts.items()
                    }
                    task.artifact_integrity = {name: "ok" for name in artifacts}
                    self._persist_locked(record)

        with self._lock:
            if failed:
                for task in record.tasks:
                    if task.status == "queued":
                        task.status = "skipped"
                        task.error = {
                            "type": "WorkflowStopped",
                            "message": "not executed because an earlier task failed",
                        }
                record.status = "failed"
            else:
                record.status = "completed"
            record.completed_at = _timestamp()
            self._persist_locked(record)

    def _record(self, workflow_id: str) -> WorkflowRecord:
        try:
            record = self._workflows[workflow_id]
        except KeyError as exc:
            raise WorkflowNotFoundError(f"workflow not found: {workflow_id}") from exc
        return record

    def _validate_handovers(
        self,
        tasks: list[WorkflowTaskRecord],
    ) -> None:
        by_id = {task.task_id: task for task in tasks}
        for task in tasks:
            spec = task.handover
            if spec is None:
                continue
            source = by_id.get(spec.source_task_id)
            if source is None:
                raise WorkflowValidationError(
                    f"handover source task not found: {spec.source_task_id}"
                )
            if source.order >= task.order:
                raise WorkflowValidationError(
                    "handover source task must appear before its target"
                )
            if source.backend.backend_id != "rocketpy":
                raise WorkflowValidationError(
                    "rocket-to-orbit handover source must use RocketPy"
                )
            if task.backend.backend_id != "tudatpy":
                raise WorkflowValidationError(
                    "rocket-to-orbit handover target must use TudatPy"
                )

    def _snapshot(self, record: WorkflowRecord) -> dict[str, object]:
        task_snapshots = [
            {
                "task_id": task.task_id,
                "order": task.order,
                "status": task.status,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "request": task.request.document(),
                "backend": task.backend.document(),
                "handover": (
                    task.handover_report
                    if task.handover_report is not None
                    else {
                        **task.handover.document(),
                        "status": "pending",
                    }
                    if task.handover is not None
                    else None
                ),
                "summary": task.summary,
                "artifacts": [
                    {
                        "name": name,
                        "filename": path.name,
                        "media_type": _media_type(path),
                        "sha256": task.artifact_hashes.get(name),
                        "integrity": task.artifact_integrity.get(
                            name,
                            "unknown",
                        ),
                        "url": (
                            f"/api/workflows/{record.workflow_id}/tasks/"
                            f"{task.task_id}/artifacts/{name}"
                        ),
                    }
                    for name, path in sorted(task.artifacts.items())
                ],
                "error": task.error,
            }
            for task in record.tasks
        ]
        succeeded_count = sum(task.status == "completed" for task in record.tasks)
        finished_count = sum(
            task.status in _TERMINAL_TASK_STATES for task in record.tasks
        )
        result = {
            "workflow_id": record.workflow_id,
            "name": record.name,
            "status": record.status,
            "provenance": record.provenance,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "progress": {
                "finished": finished_count,
                "succeeded": succeeded_count,
                "total": len(record.tasks),
                "fraction": finished_count / len(record.tasks),
            },
            "tasks": task_snapshots,
        }
        return result

    def _history_snapshot(self, record: WorkflowRecord) -> dict[str, object]:
        snapshot = self._snapshot(record)
        return {
            key: snapshot[key]
            for key in (
                "workflow_id",
                "name",
                "status",
                "provenance",
                "created_at",
                "started_at",
                "completed_at",
                "progress",
            )
        } | {
            "task_count": len(record.tasks),
            "backends": sorted({task.backend.backend_id for task in record.tasks}),
        }

    def _persist_locked(self, record: WorkflowRecord) -> None:
        self._database.upsert_workflow(self._record_document(record))

    def _record_document(self, record: WorkflowRecord) -> dict[str, object]:
        return {
            "record_schema": WORKFLOW_RECORD_SCHEMA,
            "workflow_id": record.workflow_id,
            "name": record.name,
            "status": record.status,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "provenance": copy.deepcopy(record.provenance),
            "tasks": [
                {
                    "task_id": task.task_id,
                    "order": task.order,
                    "submitted_document": copy.deepcopy(task.submitted_document),
                    "request": task.request.document(),
                    "backend": task.backend.document(),
                    "handover": (
                        task.handover.document() if task.handover is not None else None
                    ),
                    "handover_report": copy.deepcopy(task.handover_report),
                    "status": task.status,
                    "started_at": task.started_at,
                    "completed_at": task.completed_at,
                    "summary": copy.deepcopy(task.summary),
                    "artifacts": {
                        name: {
                            "path": _relative_artifact_path(
                                path,
                                self._runs_root,
                            ),
                            "sha256": task.artifact_hashes.get(name),
                        }
                        for name, path in task.artifacts.items()
                    },
                    "error": copy.deepcopy(task.error),
                }
                for task in record.tasks
            ],
        }

    def _restore_workflows(self) -> None:
        for document in self._database.load_workflows():
            record = self._record_from_document(document)
            if record.workflow_id in self._workflows:
                raise WorkspaceDatabaseError(
                    f"duplicate workflow id in workspace: {record.workflow_id}"
                )
            recovered = self._recover_interrupted(record)
            self._workflows[record.workflow_id] = record
            if recovered:
                self._persist_locked(record)

    def _record_from_document(
        self,
        document: Mapping[str, object],
    ) -> WorkflowRecord:
        if document.get("record_schema") != WORKFLOW_RECORD_SCHEMA:
            raise WorkspaceDatabaseError(
                f"unsupported workflow record schema: {document.get('record_schema')!r}"
            )
        tasks_raw = document.get("tasks")
        if not isinstance(tasks_raw, list) or not tasks_raw:
            raise WorkspaceDatabaseError(
                "persisted workflow must contain at least one task"
            )
        tasks = [self._task_from_document(item) for item in tasks_raw]
        workflow_status = _record_string(document, "status")
        if workflow_status not in _WORKFLOW_STATES:
            raise WorkspaceDatabaseError(
                f"unsupported persisted workflow status: {workflow_status}"
            )
        return WorkflowRecord(
            workflow_id=_record_string(document, "workflow_id"),
            name=_record_string(document, "name"),
            status=workflow_status,
            created_at=_record_string(document, "created_at"),
            tasks=tasks,
            provenance=_optional_mapping(document.get("provenance")),
            started_at=_optional_string(document.get("started_at")),
            completed_at=_optional_string(document.get("completed_at")),
        )

    def _task_from_document(
        self,
        value: object,
    ) -> WorkflowTaskRecord:
        if not isinstance(value, Mapping):
            raise WorkspaceDatabaseError("persisted workflow task must be an object")
        submitted = value.get("submitted_document")
        request_document = value.get("request")
        backend_document = value.get("backend")
        if not isinstance(submitted, Mapping):
            raise WorkspaceDatabaseError(
                "persisted submitted task document must be an object"
            )
        if not isinstance(request_document, Mapping):
            raise WorkspaceDatabaseError("persisted task request must be an object")
        request = request_from_document(request_document)
        backend = _backend_from_document(backend_document)
        handover_raw = value.get("handover")
        handover = (
            None if handover_raw is None else HandoverSpec.from_mapping(handover_raw)
        )
        artifacts_raw = value.get("artifacts", {})
        if not isinstance(artifacts_raw, Mapping):
            raise WorkspaceDatabaseError("persisted task artifacts must be an object")
        artifacts: dict[str, Path] = {}
        artifact_hashes: dict[str, str] = {}
        artifact_integrity: dict[str, str] = {}
        for name, item in artifacts_raw.items():
            if not isinstance(name, str) or not isinstance(item, Mapping):
                raise WorkspaceDatabaseError(
                    "persisted artifact entries must be objects"
                )
            relative = _record_string(item, "path")
            path = _restored_artifact_path(relative, self._runs_root)
            expected_hash = _record_string(item, "sha256")
            artifacts[name] = path
            artifact_hashes[name] = expected_hash
            artifact_integrity[name] = _artifact_integrity(
                path,
                expected_hash,
            )
        task_status = _record_string(value, "status")
        if task_status not in _TASK_STATES:
            raise WorkspaceDatabaseError(
                f"unsupported persisted workflow task status: {task_status}"
            )
        return WorkflowTaskRecord(
            task_id=_record_string(value, "task_id"),
            order=_record_integer(value, "order"),
            submitted_document=copy.deepcopy(dict(submitted)),
            request=request,
            backend=backend,
            handover=handover,
            handover_report=_optional_mapping(value.get("handover_report")),
            status=task_status,
            started_at=_optional_string(value.get("started_at")),
            completed_at=_optional_string(value.get("completed_at")),
            summary=_optional_mapping(value.get("summary")),
            artifacts=artifacts,
            artifact_hashes=artifact_hashes,
            artifact_integrity=artifact_integrity,
            error=_optional_string_mapping(value.get("error")),
        )

    def _recover_interrupted(self, record: WorkflowRecord) -> bool:
        if is_terminal_workflow_status(record.status):
            return False
        now = _timestamp()
        for task in record.tasks:
            if task.status == "running":
                task.status = "interrupted"
                task.completed_at = now
                task.error = {
                    "type": "WorkflowInterrupted",
                    "message": ("execution was interrupted by a previous service stop"),
                }
            elif task.status == "queued":
                task.status = "skipped"
                task.error = {
                    "type": "WorkflowInterrupted",
                    "message": ("not executed because the previous service stopped"),
                }
        record.status = "interrupted"
        record.completed_at = now
        return True


def is_terminal_workflow_status(status: str) -> bool:
    result = status in _TERMINAL_WORKFLOW_STATES
    return result


def _timestamp() -> str:
    result = datetime.now(timezone.utc).isoformat()
    return result


def _path_component(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    if not result:
        result = "task"
    return result[:80]


def _write_assistant_provenance(
    workflow: WorkflowRecord,
    task: WorkflowTaskRecord,
    output_directory: Path,
) -> Path | None:
    provenance = workflow.provenance
    if provenance is None or provenance.get("origin") != "assistant_confirmed":
        return None
    request_document = task.request.document()
    canonical_request = json.dumps(
        request_document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload = {
        "schema": ASSISTANT_PROVENANCE_SCHEMA,
        "workflow_id": workflow.workflow_id,
        "workflow_name": workflow.name,
        "task_id": task.task_id,
        "task_order": task.order,
        "request_id": task.request.request_id,
        "request_sha256": hashlib.sha256(canonical_request).hexdigest(),
        "provenance": provenance,
    }
    path = output_directory / "assistant_provenance.json"
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _media_type(path: Path) -> str:
    media_types = {
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".png": "image/png",
    }
    result = media_types.get(path.suffix.lower(), "application/octet-stream")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_artifact_path(path: Path, runs_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(runs_root)
    except ValueError as exc:
        raise WorkspaceDatabaseError(
            f"workflow artifact is outside the runs directory: {path}"
        ) from exc
    return relative.as_posix()


def _restored_artifact_path(relative: str, runs_root: Path) -> Path:
    path = (runs_root / relative).resolve()
    try:
        path.relative_to(runs_root)
    except ValueError as exc:
        raise WorkspaceDatabaseError(
            f"persisted artifact escapes the runs directory: {relative}"
        ) from exc
    return path


def _artifact_integrity(path: Path, expected_hash: str) -> str:
    if not path.is_file():
        return "missing"
    if _sha256(path) != expected_hash:
        return "corrupt"
    return "ok"


def _backend_from_document(value: object) -> BackendCapabilities:
    if not isinstance(value, Mapping):
        raise WorkspaceDatabaseError("persisted backend capabilities must be an object")
    return BackendCapabilities(
        backend_id=_record_string(value, "backend_id"),
        backend_name=_record_string(value, "backend_name"),
        backend_version=_record_string(value, "backend_version"),
        supported_task_kinds=_string_tuple(value, "supported_task_kinds"),
        supported_contract_schemas=_string_tuple(
            value,
            "supported_contract_schemas",
        ),
        supported_family_ids=_string_tuple(
            value,
            "supported_family_ids",
            required=False,
        ),
        supported_component_ids=_string_tuple(
            value,
            "supported_component_ids",
            required=False,
        ),
    )


def _string_tuple(
    value: Mapping[str, object],
    key: str,
    *,
    required: bool = True,
) -> tuple[str, ...]:
    items = value.get(key)
    if items is None and not required:
        return ()
    if not isinstance(items, list) or any(
        not isinstance(item, str) or not item for item in items
    ):
        raise WorkspaceDatabaseError(
            f"persisted workflow record {key} must be a string array"
        )
    return tuple(items)


def _record_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise WorkspaceDatabaseError(
            f"persisted workflow record {key} must be a string"
        )
    return item


def _record_integer(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise WorkspaceDatabaseError(
            f"persisted workflow record {key} must be an integer"
        )
    return item


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise WorkspaceDatabaseError(
            "persisted workflow optional timestamp must be a string"
        )
    return value


def _optional_mapping(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise WorkspaceDatabaseError(
            "persisted workflow optional value must be an object"
        )
    return copy.deepcopy(dict(value))


def _optional_string_mapping(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise WorkspaceDatabaseError("persisted workflow error must be a string object")
    return dict(value)
