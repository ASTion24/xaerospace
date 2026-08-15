from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from aerospace_simulator.protocol import UnifiedSimulationResult
from aerospace_simulator.simulation import run_request
from aerospace_simulator.workflows import (
    WorkflowArtifactNotFoundError,
    WorkflowConflictError,
    WorkflowStore,
)

PROJECT_ROOT = Path(__file__).parents[1]
SCENARIO = PROJECT_ROOT / "scenarios" / "single_stage_demo.json"


def test_completed_workflow_survives_restart_with_verified_artifacts(tmp_path):
    runs_root = tmp_path / "runs"
    store = WorkflowStore(runs_root)
    submitted = store.submit(
        "Durable workflow",
        [{"task_id": "rocket", "document": _scenario_document()}],
    )
    completed = _wait_for_store(store, submitted["workflow_id"])
    store.close()

    restored_store = WorkflowStore(runs_root)
    try:
        restored = restored_store.get(completed["workflow_id"])
        history = restored_store.list()
        result_path = restored_store.artifact_path(
            completed["workflow_id"],
            "rocket",
            "result",
        )
    finally:
        restored_store.close()

    assert restored["status"] == "completed"
    assert restored["tasks"][0]["summary"] == completed["tasks"][0]["summary"]
    assert {item["integrity"] for item in restored["tasks"][0]["artifacts"]} == {"ok"}
    assert history["total"] == 1
    assert history["workflows"][0]["workflow_id"] == completed["workflow_id"]
    assert (
        json.loads(result_path.read_text(encoding="utf-8"))["backend"]["backend_id"]
        == "rocketpy"
    )


def test_incomplete_workflow_is_interrupted_after_restart(tmp_path):
    runs_root = tmp_path / "runs"
    signal_path = tmp_path / "running.json"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _CRASH_WORKER,
            str(runs_root),
            str(SCENARIO),
            str(signal_path),
        ],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
    )
    try:
        for _ in range(200):
            if signal_path.is_file():
                break
            if child.poll() is not None:
                raise AssertionError(
                    f"crash worker exited unexpectedly: {child.returncode}"
                )
            time.sleep(0.025)
        else:
            raise AssertionError("crash worker did not reach running state")
        workflow_id = json.loads(signal_path.read_text(encoding="utf-8"))["workflow_id"]
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    restored_store = WorkflowStore(runs_root)
    try:
        restored = restored_store.get(workflow_id)
    finally:
        restored_store.close()

    assert restored["status"] == "interrupted"
    assert restored["tasks"][0]["status"] == "interrupted"
    assert restored["tasks"][0]["error"]["type"] == "WorkflowInterrupted"
    assert restored["progress"]["fraction"] == 1.0


def test_corrupt_artifact_is_reported_and_cannot_be_downloaded(tmp_path):
    runs_root = tmp_path / "runs"
    store = WorkflowStore(runs_root)
    submitted = store.submit(
        "Integrity workflow",
        [{"task_id": "rocket", "document": _scenario_document()}],
    )
    completed = _wait_for_store(store, submitted["workflow_id"])
    result_path = store.artifact_path(
        completed["workflow_id"],
        "rocket",
        "result",
    )
    store.close()
    result_path.write_text("tampered\n", encoding="utf-8")

    restored_store = WorkflowStore(runs_root)
    try:
        restored = restored_store.get(completed["workflow_id"])
        with pytest.raises(
            WorkflowArtifactNotFoundError,
            match="integrity validation",
        ):
            restored_store.artifact_path(
                completed["workflow_id"],
                "rocket",
                "result",
            )
    finally:
        restored_store.close()

    artifacts = {item["name"]: item for item in restored["tasks"][0]["artifacts"]}
    assert artifacts["result"]["integrity"] == "corrupt"


def test_delete_terminal_workflow_removes_database_and_artifacts(tmp_path):
    runs_root = tmp_path / "runs"
    store = WorkflowStore(runs_root)
    submitted = store.submit(
        "Disposable workflow",
        [{"task_id": "rocket", "document": _scenario_document()}],
    )
    completed = _wait_for_store(store, submitted["workflow_id"])
    workflow_directory = runs_root / completed["workflow_id"]

    store.delete(completed["workflow_id"])

    assert not workflow_directory.exists()
    assert store.list()["total"] == 0
    store.close()

    restored_store = WorkflowStore(runs_root)
    try:
        assert restored_store.list()["total"] == 0
    finally:
        restored_store.close()


def test_delete_running_workflow_is_rejected(tmp_path):
    gate = threading.Event()

    def blocked_runner(request) -> UnifiedSimulationResult:
        gate.wait(timeout=5)
        return run_request(request)

    store = WorkflowStore(tmp_path / "runs", runner=blocked_runner)
    submitted = store.submit(
        "Active workflow",
        [{"task_id": "rocket", "document": _scenario_document()}],
    )
    _wait_for_task_status(store, submitted["workflow_id"], "running")

    with pytest.raises(WorkflowConflictError, match="cannot be deleted"):
        store.delete(submitted["workflow_id"])

    gate.set()
    store.close()


def _wait_for_store(
    store: WorkflowStore,
    workflow_id: str,
) -> dict[str, object]:
    for _ in range(400):
        workflow = store.get(workflow_id)
        if workflow["status"] in {"completed", "failed", "interrupted"}:
            return workflow
        time.sleep(0.05)
    raise AssertionError(f"workflow did not finish: {workflow_id}")


def _scenario_document() -> dict[str, object]:
    return json.loads(SCENARIO.read_text(encoding="utf-8"))


def _wait_for_task_status(
    store: WorkflowStore,
    workflow_id: str,
    status: str,
) -> None:
    for _ in range(200):
        workflow = store.get(workflow_id)
        if workflow["tasks"][0]["status"] == status:
            return
        time.sleep(0.01)
    raise AssertionError(f"task did not reach {status}: {workflow_id}")


_CRASH_WORKER = r"""
import json
import sys
import threading
import time
from pathlib import Path

from aerospace_simulator.workflows import WorkflowStore

runs_root = Path(sys.argv[1])
scenario = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
signal_path = Path(sys.argv[3])
gate = threading.Event()

def blocked_runner(_request):
    gate.wait()

store = WorkflowStore(runs_root, runner=blocked_runner)
submitted = store.submit(
    "Interrupted workflow",
    [{"task_id": "rocket", "document": scenario}],
)
for _ in range(500):
    workflow = store.get(submitted["workflow_id"])
    if workflow["tasks"][0]["status"] == "running":
        signal_path.write_text(
            json.dumps({"workflow_id": submitted["workflow_id"]}),
            encoding="utf-8",
        )
        break
    time.sleep(0.01)
else:
    raise RuntimeError("workflow did not reach running state")
while True:
    time.sleep(1)
"""
