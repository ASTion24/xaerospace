from __future__ import annotations

import copy
import hashlib
import json
import re
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

MAX_WORKFLOW_TASKS = 12
WORKFLOW_EXPORT_SCHEMA = "wms.aerospace.workflow.v1"
ASSISTANT_PROVENANCE_SCHEMA = "wms.aerospace.assistant_provenance.v1"
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TERMINAL_WORKFLOW_STATES = {"completed", "failed"}


class WorkflowValidationError(ValueError):
    """Raised when a workflow cannot be submitted safely."""


class WorkflowNotFoundError(KeyError):
    """Raised when a workflow id is not present in the in-memory store."""


class WorkflowArtifactNotFoundError(KeyError):
    """Raised when a task artifact is not present in a workflow."""


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
    ) -> None:
        self._runs_root = Path(runs_root)
        self._runs_root.mkdir(parents=True, exist_ok=True)
        self._runner = runner
        self._output_writer = output_writer
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="aerospace-workflow",
        )
        self._lock = threading.RLock()
        self._workflows: dict[str, WorkflowRecord] = {}

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
        self._executor.submit(self._execute, workflow_id)
        result = self.get(workflow_id)
        return result

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
        return path

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _execute(self, workflow_id: str) -> None:
        with self._lock:
            record = self._record(workflow_id)
            record.status = "running"
            record.started_at = _timestamp()

        failed = False
        completed_results: dict[str, UnifiedSimulationResult] = {}
        for task in record.tasks:
            if failed:
                break
            with self._lock:
                task.status = "running"
                task.started_at = _timestamp()
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
            else:
                completed_results[task.task_id] = result
                with self._lock:
                    task.status = "completed"
                    task.completed_at = _timestamp()
                    task.summary = result.document(include_samples=False)
                    task.artifacts = artifacts

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
            task.status in {"completed", "failed", "skipped"} for task in record.tasks
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
