import copy
import json
from pathlib import Path

import pytest

from aerospace_simulator.config import (
    ScenarioConfig,
    ScenarioValidationError,
    load_scenario,
)

SCENARIO_PATH = Path(__file__).parents[1] / "scenarios" / "single_stage_demo.json"
SCENARIO_6DOF_PATH = (
    Path(__file__).parents[1] / "scenarios" / "single_stage_6dof_demo.json"
)
RECOVERY_SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "single_stage_6dof_recovery_demo.json"
)
POINT_RECOVERY_SCENARIO_PATH = (
    Path(__file__).parents[1]
    / "scenarios"
    / "single_stage_point_mass_recovery_demo.json"
)


def test_example_scenario_loads_with_explicit_backend_contract():
    scenario = load_scenario(SCENARIO_PATH)

    assert scenario.backend == "rocketpy"
    assert scenario.dynamics == "single_stage_point_mass_3dof"
    assert scenario.motor.thrust_curve[-1][0] == scenario.motor.burn_time_s
    assert scenario.launch.max_time_s > scenario.motor.burn_time_s


def test_unknown_dynamics_is_rejected_instead_of_falling_back():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["dynamics"] = "multi_stage_6dof"

    with pytest.raises(ScenarioValidationError, match="dynamics must be one of"):
        ScenarioConfig.from_mapping(raw)


def test_thrust_curve_must_end_at_declared_burn_time():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    changed = copy.deepcopy(raw)
    changed["motor"]["burn_time_s"] = 4.0

    with pytest.raises(ScenarioValidationError, match="final motor.thrust_curve"):
        ScenarioConfig.from_mapping(changed)


def test_unknown_fields_are_rejected():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["vehicle"]["silently_ignored_value"] = 1

    with pytest.raises(ScenarioValidationError, match="unknown fields"):
        ScenarioConfig.from_mapping(raw)


def test_backend_preference_is_not_hardcoded_to_the_current_library():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["backend"] = "future_backend"

    scenario = ScenarioConfig.from_mapping(raw)

    assert scenario.backend == "future_backend"


def test_six_dof_scenario_requires_explicit_rigid_body_model():
    raw = json.loads(SCENARIO_6DOF_PATH.read_text(encoding="utf-8"))
    scenario = ScenarioConfig.from_mapping(raw)

    assert scenario.dynamics == "single_stage_rigid_body_6dof"
    assert scenario.rigid_body is not None
    assert scenario.rigid_body.fins.count == 4
    assert scenario.rigid_body.fins.cant_angle_deg == 0.5
    assert scenario.rigid_body.vehicle_dry_inertia_kg_m2 == (
        6.321,
        6.321,
        0.034,
    )


def test_six_dof_does_not_fall_back_when_rigid_body_model_is_missing():
    raw = json.loads(SCENARIO_6DOF_PATH.read_text(encoding="utf-8"))
    del raw["rigid_body"]

    with pytest.raises(ScenarioValidationError, match="rigid_body must be an object"):
        ScenarioConfig.from_mapping(raw)


def test_point_mass_contract_rejects_rigid_body_configuration():
    raw = json.loads(SCENARIO_6DOF_PATH.read_text(encoding="utf-8"))
    raw["dynamics"] = "single_stage_point_mass_3dof"

    with pytest.raises(ScenarioValidationError, match="rigid_body is not valid"):
        ScenarioConfig.from_mapping(raw)


def test_recovery_contract_parses_typed_dual_deployment():
    scenario = load_scenario(RECOVERY_SCENARIO_PATH)

    assert scenario.dynamics == "single_stage_rigid_body_6dof_recovery"
    assert scenario.recovery is not None
    assert [parachute.id for parachute in scenario.recovery.parachutes] == [
        "drogue",
        "main",
    ]
    assert scenario.recovery.parachutes[0].trigger.kind == "apogee"
    assert scenario.recovery.parachutes[1].trigger.altitude_agl_m == 250.0


def test_point_mass_recovery_contract_reuses_typed_recovery_without_rigid_body():
    scenario = load_scenario(POINT_RECOVERY_SCENARIO_PATH)

    assert scenario.dynamics == "single_stage_point_mass_3dof_recovery"
    assert scenario.rigid_body is None
    assert scenario.recovery is not None
    assert [parachute.id for parachute in scenario.recovery.parachutes] == [
        "drogue",
        "main",
    ]


def test_recovery_contract_requires_recovery_configuration():
    raw = json.loads(RECOVERY_SCENARIO_PATH.read_text(encoding="utf-8"))
    del raw["recovery"]

    with pytest.raises(ScenarioValidationError, match="recovery must be an object"):
        ScenarioConfig.from_mapping(raw)


def test_non_recovery_contract_rejects_recovery_configuration():
    raw = json.loads(RECOVERY_SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["dynamics"] = "single_stage_rigid_body_6dof"

    with pytest.raises(ScenarioValidationError, match="recovery is not valid"):
        ScenarioConfig.from_mapping(raw)


def test_recovery_parachute_ids_must_be_unique():
    raw = json.loads(RECOVERY_SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["recovery"]["parachutes"][1]["id"] = "drogue"

    with pytest.raises(ScenarioValidationError, match="ids must be unique"):
        ScenarioConfig.from_mapping(raw)


def test_recovery_rejects_unknown_trigger_kinds():
    raw = json.loads(RECOVERY_SCENARIO_PATH.read_text(encoding="utf-8"))
    raw["recovery"]["parachutes"][0]["trigger"] = {"kind": "timer"}

    with pytest.raises(ScenarioValidationError, match="trigger.kind"):
        ScenarioConfig.from_mapping(raw)
