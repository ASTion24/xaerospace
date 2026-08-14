import copy
import json
from pathlib import Path

import pytest

from aerospace_simulator.config import ScenarioValidationError
from aerospace_simulator.orbit_config import (
    ORBIT_CONTRACT_SCHEMA,
    OrbitPropagationConfig,
    load_orbit_scenario,
)
from aerospace_simulator.request_io import load_request, request_from_orbit_scenario

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "earth_orbit_two_body_demo.json"
)


def test_orbit_contract_loads_as_a_separate_typed_schema():
    config = load_orbit_scenario(SCENARIO_PATH)
    request = request_from_orbit_scenario(config)

    assert config.dynamics == "earth_orbit_two_body"
    assert config.schema_version == 2
    assert config.aerodynamics.enabled is False
    assert config.frame == "J2000"
    assert config.central_body.name == "Earth"
    assert request.contract_schema == ORBIT_CONTRACT_SCHEMA
    assert request.backend_preference == "tudatpy"
    assert "backend" not in request.document()["contract"]
    assert "dynamics" not in request.document()["contract"]


def test_public_loader_detects_orbit_scenarios_and_replays_request(tmp_path):
    request = load_request(SCENARIO_PATH)
    replay_path = tmp_path / "request.json"
    replay_path.write_text(json.dumps(request.document()), encoding="utf-8")

    replayed = load_request(replay_path)

    assert replayed.document() == request.document()
    assert isinstance(replayed.contract, OrbitPropagationConfig)


def test_orbit_contract_rejects_unknown_force_models_without_fallback():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["dynamics"] = "earth_orbit_drag"

    with pytest.raises(ScenarioValidationError, match="dynamics must be one of"):
        OrbitPropagationConfig.from_mapping(raw)


def test_orbit_contract_rejects_drag_without_rotating_j2_environment():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["aerodynamics"]["enabled"] = True

    with pytest.raises(ScenarioValidationError, match="requires earth_orbit_j2"):
        OrbitPropagationConfig.from_mapping(raw)


def test_orbit_contract_rejects_body_intersection_and_ambiguous_sampling():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    intersects = copy.deepcopy(raw)
    intersects["initial_state"]["semi_major_axis_m"] = 6400000.0
    intersects["initial_state"]["eccentricity"] = 0.1

    with pytest.raises(ScenarioValidationError, match="intersects"):
        OrbitPropagationConfig.from_mapping(intersects)

    ambiguous = copy.deepcopy(raw)
    ambiguous["propagation"]["output_interval_s"] = 65.0
    with pytest.raises(ScenarioValidationError, match="integer multiple"):
        OrbitPropagationConfig.from_mapping(ambiguous)


def test_orbit_contract_rejects_non_j2000_frames():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["frame"] = "TEME"

    with pytest.raises(ScenarioValidationError, match="frame must be 'J2000'"):
        OrbitPropagationConfig.from_mapping(raw)
