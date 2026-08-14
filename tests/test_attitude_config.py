import copy
import json
from pathlib import Path

import pytest

from aerospace_simulator.attitude_config import (
    ATTITUDE_CONTRACT_SCHEMA,
    SpacecraftAttitudeConfig,
    load_attitude_scenario,
)
from aerospace_simulator.config import ScenarioValidationError
from aerospace_simulator.request_io import (
    load_request,
    request_from_attitude_scenario,
)

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "spacecraft_attitude_gnc_demo.json"
)
RATE_DAMPING_SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "spacecraft_rate_damping_demo.json"
)


def test_attitude_contract_loads_as_an_independent_typed_schema():
    config = load_attitude_scenario(SCENARIO_PATH)
    request = request_from_attitude_scenario(config)

    assert config.dynamics == "spacecraft_inertial_pointing_gnc"
    assert config.gnc.enabled is True
    assert config.reaction_wheels.model_id == "Honeywell_HR16"
    assert config.reaction_wheels.spin_axes_body[2] == (0.0, 0.0, 1.0)
    assert request.contract_schema == ATTITUDE_CONTRACT_SCHEMA
    assert request.backend_preference == "basilisk"
    assert "backend" not in request.document()["contract"]
    assert "dynamics" not in request.document()["contract"]


def test_public_loader_detects_and_replays_attitude_requests(tmp_path):
    request = load_request(SCENARIO_PATH)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request.document()), encoding="utf-8")

    replayed = load_request(request_path)

    assert replayed.document() == request.document()
    assert isinstance(replayed.contract, SpacecraftAttitudeConfig)


def test_rate_damping_contract_requires_enabled_controller_and_zero_mrp_gain():
    raw = json.loads(RATE_DAMPING_SCENARIO_PATH.read_text(encoding="utf-8"))
    config = SpacecraftAttitudeConfig.from_mapping(raw)

    assert config.dynamics == "spacecraft_rate_damping_gnc"
    assert config.gnc.controller == "rate_damping"
    assert config.gnc.mrp_gain_n_m == 0.0

    disabled = copy.deepcopy(raw)
    disabled["gnc"]["enabled"] = False
    with pytest.raises(ScenarioValidationError, match="requires gnc.enabled=true"):
        SpacecraftAttitudeConfig.from_mapping(disabled)

    attitude_gain = copy.deepcopy(raw)
    attitude_gain["gnc"]["mrp_gain_n_m"] = 1.0
    with pytest.raises(ScenarioValidationError, match="must be zero"):
        SpacecraftAttitudeConfig.from_mapping(attitude_gain)


def test_attitude_contract_rejects_unknown_wheels_and_nonorthogonal_axes():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    unknown_wheel = copy.deepcopy(raw)
    unknown_wheel["reaction_wheels"]["model_id"] = "custom_rw"

    with pytest.raises(ScenarioValidationError, match="model_id"):
        SpacecraftAttitudeConfig.from_mapping(unknown_wheel)

    nonorthogonal = copy.deepcopy(raw)
    nonorthogonal["reaction_wheels"]["spin_axes_body"][2] = [1.0, 0.0, 0.0]
    with pytest.raises(ScenarioValidationError, match="mutually orthogonal"):
        SpacecraftAttitudeConfig.from_mapping(nonorthogonal)


def test_attitude_contract_rejects_nonphysical_inertia_and_nonprincipal_mrp():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    invalid_inertia = copy.deepcopy(raw)
    invalid_inertia["spacecraft"]["principal_inertia_kg_m2"] = [
        2000.0,
        800.0,
        600.0,
    ]

    with pytest.raises(ScenarioValidationError, match="triangle inequality"):
        SpacecraftAttitudeConfig.from_mapping(invalid_inertia)

    invalid_mrp = copy.deepcopy(raw)
    invalid_mrp["initial_state"]["mrp_sigma_bn"] = [1.0, 0.0, 0.0]
    with pytest.raises(ScenarioValidationError, match="principal MRP set"):
        SpacecraftAttitudeConfig.from_mapping(invalid_mrp)


def test_attitude_wheel_limits_and_propagation_must_match_pinned_model():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    overspeed = copy.deepcopy(raw)
    overspeed["reaction_wheels"]["initial_speed_rpm"][0] = 6001.0

    with pytest.raises(ScenarioValidationError, match="exceeds max_speed_rpm"):
        SpacecraftAttitudeConfig.from_mapping(overspeed)

    misaligned = copy.deepcopy(raw)
    misaligned["propagation"]["output_interval_s"] = 1.05
    with pytest.raises(ScenarioValidationError, match="integer multiple"):
        SpacecraftAttitudeConfig.from_mapping(misaligned)
