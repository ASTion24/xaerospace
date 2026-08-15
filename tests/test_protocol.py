import json
from dataclasses import replace
from pathlib import Path

import pytest

from aerospace_simulator.config import load_scenario
from aerospace_simulator.protocol import (
    BackendCapabilities,
    ProtocolValidationError,
    SimulationRequest,
)
from aerospace_simulator.registry import (
    BackendRegistrationError,
    BackendRegistry,
    BackendSelectionError,
    UnsupportedTaskError,
)
from aerospace_simulator.request_io import (
    AEROSPACE_CONTRACT_SCHEMA,
    load_request,
    request_from_document,
    request_from_scenario,
)
from aerospace_simulator.simulation import (
    RocketPyBackend,
    run_request,
)

SCENARIO_PATH = Path(__file__).parents[1] / "scenarios" / "single_stage_demo.json"
RECOVERY_SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "single_stage_6dof_recovery_demo.json"
)


class StubBackend:
    def __init__(self, backend_id: str, task_kind: str) -> None:
        self._capabilities = BackendCapabilities(
            backend_id=backend_id,
            backend_name=backend_id,
            backend_version="test",
            supported_task_kinds=(task_kind,),
            supported_contract_schemas=(AEROSPACE_CONTRACT_SCHEMA,),
        )

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def run(self, request: SimulationRequest):
        raise AssertionError("selection tests must not execute stub backends")


def test_request_envelope_is_backend_independent_and_serializable():
    scenario = load_scenario(SCENARIO_PATH)
    request = request_from_scenario(scenario, request_id="protocol-test")
    document = request.document()

    assert document["protocol_version"] == 1
    assert document["request_id"] == "protocol-test"
    assert document["task_kind"] == "single_stage_point_mass_3dof"
    assert document["contract_schema"] == AEROSPACE_CONTRACT_SCHEMA
    assert document["contract"]["vehicle"]["radius_m"] == pytest.approx(0.075)
    assert "backend" not in document["contract"]
    assert "dynamics" not in document["contract"]


def test_request_envelope_round_trips_through_the_public_loader(tmp_path):
    request = request_from_scenario(load_scenario(SCENARIO_PATH))
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request.document()), encoding="utf-8")

    loaded = load_request(path)

    assert loaded.document() == request.document()


def test_removed_wms_contract_schema_is_rejected():
    document = request_from_scenario(load_scenario(SCENARIO_PATH)).document()
    document["contract_schema"] = "wms.aerospace.scenario.v1"

    with pytest.raises(ProtocolValidationError, match="unsupported contract_schema"):
        request_from_document(document)


def test_optional_recovery_fields_round_trip_without_null_placeholders(tmp_path):
    request = request_from_scenario(load_scenario(RECOVERY_SCENARIO_PATH))
    document = request.document()
    drogue_trigger = document["contract"]["recovery"]["parachutes"][0]["trigger"]
    path = tmp_path / "recovery_request.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    loaded = load_request(path)

    assert drogue_trigger == {"kind": "apogee"}
    assert loaded.document() == document


def test_registry_routes_auto_requests_by_declared_capability():
    scenario = replace(load_scenario(SCENARIO_PATH), backend="auto")
    request = request_from_scenario(scenario)
    registry = BackendRegistry()
    registry.register(RocketPyBackend())

    assert registry.select(request).capabilities.backend_id == "rocketpy"


def test_registry_rejects_unsupported_and_unknown_backends_without_fallback():
    request = request_from_scenario(load_scenario(SCENARIO_PATH))
    registry = BackendRegistry()
    registry.register(RocketPyBackend())

    with pytest.raises(UnsupportedTaskError, match="does not support task"):
        registry.select(replace(request, task_kind="orbital_propagation"))
    with pytest.raises(BackendSelectionError, match="not registered"):
        registry.select(replace(request, backend_preference="missing"))


def test_registry_rejects_duplicate_and_ambiguous_backend_choices():
    request = replace(
        request_from_scenario(load_scenario(SCENARIO_PATH)),
        backend_preference="auto",
    )
    registry = BackendRegistry()
    first = StubBackend("first", request.task_kind)
    registry.register(first)

    with pytest.raises(BackendRegistrationError, match="already registered"):
        registry.register(first)

    registry.register(StubBackend("second", request.task_kind))
    with pytest.raises(BackendSelectionError, match="multiple backends"):
        registry.select(request)


def test_normalized_result_enforces_time_and_channel_contracts():
    result = run_request(request_from_scenario(load_scenario(SCENARIO_PATH)))

    assert result.backend.backend_id == "rocketpy"
    assert result.channel("altitude_agl").unit == "m"
    assert result.channel("altitude_agl").frame == "above_launch_site"
    assert result.channel("omega3").unit == "rad/s"
    assert result.metric("apogee_agl").unit == "m"

    with pytest.raises(ProtocolValidationError, match="strictly increasing"):
        replace(result, time_s=result.time_s[::-1])
