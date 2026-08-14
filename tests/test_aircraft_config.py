import copy
import json
from pathlib import Path

import pytest

from aerospace_simulator.aircraft_config import (
    AIRCRAFT_CONTRACT_SCHEMA,
    SUPPORTED_AIRCRAFT_MODELS,
    AircraftFlightConfig,
    load_aircraft_scenario,
)
from aerospace_simulator.config import ScenarioValidationError
from aerospace_simulator.request_io import (
    load_request,
    request_from_aircraft_scenario,
)

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "c172p_aileron_pulse_demo.json"
)


def test_aircraft_contract_loads_as_an_independent_typed_schema():
    config = load_aircraft_scenario(SCENARIO_PATH)
    request = request_from_aircraft_scenario(config)

    assert config.dynamics == "fixed_wing_trimmed_6dof"
    assert config.aircraft.model_id == "c172p"
    assert config.controls.segments[0].id == "aileron_pulse"
    assert request.contract_schema == AIRCRAFT_CONTRACT_SCHEMA
    assert request.backend_preference == "jsbsim"
    assert "backend" not in request.document()["contract"]
    assert "dynamics" not in request.document()["contract"]


def test_public_loader_detects_and_replays_aircraft_requests(tmp_path):
    request = load_request(SCENARIO_PATH)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request.document()), encoding="utf-8")

    replayed = load_request(request_path)

    assert replayed.document() == request.document()
    assert isinstance(replayed.contract, AircraftFlightConfig)


def test_aircraft_contract_rejects_unknown_models_and_atmospheres():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    unknown_model = copy.deepcopy(raw)
    unknown_model["aircraft"]["model_id"] = "f16"

    with pytest.raises(ScenarioValidationError, match="aircraft.model_id"):
        AircraftFlightConfig.from_mapping(unknown_model)

    unknown_atmosphere = copy.deepcopy(raw)
    unknown_atmosphere["environment"]["atmosphere"] = "custom_weather"
    with pytest.raises(ScenarioValidationError, match="environment.atmosphere"):
        AircraftFlightConfig.from_mapping(unknown_atmosphere)


def test_aircraft_contract_whitelists_physically_tested_models():
    assert SUPPORTED_AIRCRAFT_MODELS == (
        "c172p",
        "c172r",
        "c182",
        "c310",
        "J3Cub",
    )
    for model_id in SUPPORTED_AIRCRAFT_MODELS:
        raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
        raw["aircraft"]["model_id"] = model_id
        assert AircraftFlightConfig.from_mapping(raw).aircraft.model_id == model_id


def test_aircraft_control_segments_must_be_unique_and_non_overlapping():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    overlapping = copy.deepcopy(raw)
    second = copy.deepcopy(overlapping["controls"]["segments"][0])
    second["id"] = "second"
    second["start_time_s"] = 6.0
    second["end_time_s"] = 8.0
    overlapping["controls"]["segments"].append(second)

    with pytest.raises(ScenarioValidationError, match="must not overlap"):
        AircraftFlightConfig.from_mapping(overlapping)

    duplicate = copy.deepcopy(raw)
    duplicate["controls"]["segments"].append(
        copy.deepcopy(duplicate["controls"]["segments"][0])
    )
    with pytest.raises(ScenarioValidationError, match="ids must be unique"):
        AircraftFlightConfig.from_mapping(duplicate)


def test_aircraft_control_events_must_align_with_integration_steps():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["controls"]["segments"][0]["start_time_s"] = 5.001

    with pytest.raises(ScenarioValidationError, match="integer multiple"):
        AircraftFlightConfig.from_mapping(raw)
