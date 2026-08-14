import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from aerospace_simulator.handover import (
    J2000_GREENWICH_ANGLE_RAD,
    WGS84_SEMI_MAJOR_AXIS_M,
    HandoverSpec,
    compile_handover,
)
from aerospace_simulator.protocol import (
    PROTOCOL_VERSION,
    Diagnostic,
    ResultChannel,
    SimulationEvent,
    UnifiedSimulationResult,
)
from aerospace_simulator.request_io import request_from_document
from aerospace_simulator.simulation import create_default_registry, run_request
from aerospace_simulator.workflows import WorkflowStore, WorkflowValidationError

PROJECT_ROOT = Path(__file__).parents[1]


def test_rocket_local_state_compiles_into_verified_tudat_orbit():
    source_request, source_result, target_request, spec = _handover_fixture()

    compiled, report = compile_handover(
        spec,
        source_result=source_result,
        target_request=target_request,
    )
    result = run_request(compiled)

    initial = compiled.contract.initial_state
    assert source_result.request == source_request
    assert math.isclose(initial.semi_major_axis_m, 7_000_000.0 / 0.99, rel_tol=1e-9)
    assert math.isclose(initial.eccentricity, 0.01, rel_tol=1e-8)
    assert math.isclose(initial.inclination_deg, 45.0, rel_tol=1e-9)
    assert math.isclose(initial.raan_deg, 0.0, abs_tol=1e-9)
    assert report["status"] == "applied"
    assert report["source_backend"] == "rocketpy"
    assert result.backend.backend_id == "tudatpy"
    assert (
        result.event("propagation_start").attributes["epoch_s_since_j2000"]
        == compiled.contract.propagation.start_epoch_s_since_j2000
    )


def test_workflow_applies_explicit_handover_before_target_execution(tmp_path):
    source_request, _, target_request, spec = _handover_fixture()

    def runner(request):
        if request.backend_preference == "rocketpy":
            return _source_result(request)
        return run_request(request)

    store = WorkflowStore(
        tmp_path / "runs",
        runner=runner,
        output_writer=lambda _result, _directory: {},
    )
    try:
        submitted = store.submit(
            "Rocket to orbit",
            [
                {
                    "task_id": "ascent",
                    "document": _source_document(),
                },
                {
                    "task_id": "orbit",
                    "document": _target_document(),
                    "handover": spec.document(),
                },
            ],
        )
        workflow = _wait_for_store(store, submitted["workflow_id"])
    finally:
        store.close()

    assert workflow["status"] == "completed"
    assert workflow["tasks"][0]["request"]["request_id"] == source_request.request_id
    target = workflow["tasks"][1]
    assert target["request"]["request_id"] == target_request.request_id
    assert target["handover"]["status"] == "applied"
    assert target["summary"]["backend"]["backend_id"] == "tudatpy"
    assert (
        target["request"]["contract"]["initial_state"]["eccentricity"]
        == (target["handover"]["derived_initial_state"]["eccentricity"])
    )


def test_handover_rejects_unbound_source_state():
    _, source_result, target_request, spec = _handover_fixture(
        local_east_velocity_m_s=20_000.0
    )

    try:
        compile_handover(
            spec,
            source_result=source_result,
            target_request=target_request,
        )
    except ValueError as exc:
        assert "not a bound elliptic orbit" in str(exc)
    else:
        raise AssertionError("unbound handover state was accepted")


def test_workflow_rejects_missing_or_late_handover_source(tmp_path):
    _, _, _, spec = _handover_fixture()
    store = WorkflowStore(
        tmp_path / "runs",
        output_writer=lambda _result, _directory: {},
    )
    try:
        with pytest.raises(
            WorkflowValidationError,
            match="source task not found",
        ):
            store.submit(
                "Missing source",
                [
                    {
                        "task_id": "orbit",
                        "document": _target_document(),
                        "handover": spec.document(),
                    }
                ],
            )
        with pytest.raises(
            WorkflowValidationError,
            match="must appear before",
        ):
            store.submit(
                "Late source",
                [
                    {
                        "task_id": "orbit",
                        "document": _target_document(),
                        "handover": spec.document(),
                    },
                    {
                        "task_id": "ascent",
                        "document": _source_document(),
                    },
                ],
            )
    finally:
        store.close()


def test_failed_handover_has_terminal_evidence(tmp_path):
    _, _, _, spec = _handover_fixture()

    def runner(request):
        if request.backend_preference == "rocketpy":
            return _source_result(
                request,
                local_east_velocity_m_s=20_000.0,
            )
        return run_request(request)

    store = WorkflowStore(
        tmp_path / "runs",
        runner=runner,
        output_writer=lambda _result, _directory: {},
    )
    try:
        submitted = store.submit(
            "Rejected handover",
            [
                {"task_id": "ascent", "document": _source_document()},
                {
                    "task_id": "orbit",
                    "document": _target_document(),
                    "handover": spec.document(),
                },
            ],
        )
        workflow = _wait_for_store(store, submitted["workflow_id"])
    finally:
        store.close()

    target = workflow["tasks"][1]
    assert workflow["status"] == "failed"
    assert target["handover"]["status"] == "failed"
    assert "not a bound elliptic orbit" in target["handover"]["error"]


def _handover_fixture(local_east_velocity_m_s=None):
    source_request = request_from_document(_source_document())
    target_request = request_from_document(_target_document())
    earth_rate = target_request.contract.central_body.rotation_rate_rad_s
    launch_epoch = -J2000_GREENWICH_ANGLE_RAD / earth_rate
    source_result = _source_result(
        source_request,
        local_east_velocity_m_s=local_east_velocity_m_s,
    )
    spec = HandoverSpec(
        handover_type="rocketpy_to_tudatpy",
        source_task_id="ascent",
        source_event="burnout",
        launch_epoch_s_since_j2000=launch_epoch,
    )
    return source_request, source_result, target_request, spec


def _source_result(request, *, local_east_velocity_m_s=None):
    target = request_from_document(_target_document()).contract
    radius = 7_000_000.0
    semi_major_axis = radius / 0.99
    speed = math.sqrt(
        target.central_body.gravitational_parameter_m3_s2
        * (2.0 / radius - 1.0 / semi_major_axis)
    )
    east_velocity = (
        speed / math.sqrt(2.0) - target.central_body.rotation_rate_rad_s * radius
        if local_east_velocity_m_s is None
        else local_east_velocity_m_s
    )
    north_velocity = speed / math.sqrt(2.0)
    times = np.array([0.0, 1.0])

    def channel(name, quantity, unit, frame, value):
        return ResultChannel(
            name=name,
            quantity=quantity,
            unit=unit,
            frame=frame,
            values=np.full(2, value),
        )

    registry = create_default_registry()
    return UnifiedSimulationResult(
        protocol_version=PROTOCOL_VERSION,
        request=request,
        backend=registry.select(request).capabilities,
        time_s=times,
        channels=(
            channel("x_east", "position", "m", "local_enu", 0.0),
            channel("y_north", "position", "m", "local_enu", 0.0),
            channel(
                "altitude_agl",
                "altitude",
                "m",
                "above_launch_site",
                0.0,
            ),
            channel("vx", "velocity", "m/s", "local_enu", east_velocity),
            channel("vy", "velocity", "m/s", "local_enu", north_velocity),
            channel("vz", "velocity", "m/s", "local_enu", 0.0),
        ),
        events=(SimulationEvent("burnout", 0.0, {}),),
        metrics=(),
        model_manifest={"name": "synthetic verified handover fixture"},
        diagnostics=(
            Diagnostic(
                "info",
                "backend_contract_executed",
                "Synthetic RocketPy-shaped state for transform verification.",
            ),
        ),
    )


def _source_document():
    document = json.loads(
        (PROJECT_ROOT / "scenarios" / "single_stage_demo.json").read_text()
    )
    document["name"] = "verified_handover_source"
    document["environment"]["latitude_deg"] = 0.0
    document["environment"]["longitude_deg"] = 0.0
    document["environment"]["elevation_m"] = 7_000_000.0 - WGS84_SEMI_MAJOR_AXIS_M
    return document


def _target_document():
    return json.loads(
        (PROJECT_ROOT / "scenarios" / "earth_orbit_two_body_demo.json").read_text()
    )


def _wait_for_store(store, workflow_id):
    for _ in range(200):
        workflow = store.get(workflow_id)
        if workflow["status"] in {"completed", "failed"}:
            return workflow
        time.sleep(0.02)
    raise AssertionError("workflow did not reach a terminal state")
