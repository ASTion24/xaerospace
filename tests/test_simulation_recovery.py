import csv
from pathlib import Path

import pytest

from aerospace_simulator.config import load_scenario
from aerospace_simulator.outputs import write_outputs
from aerospace_simulator.simulation import simulate

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "single_stage_6dof_recovery_demo.json"
)
POINT_MASS_SCENARIO_PATH = (
    Path(__file__).parents[1]
    / "scenarios"
    / "single_stage_point_mass_recovery_demo.json"
)


@pytest.fixture(scope="module")
def recovery_result():
    return simulate(load_scenario(SCENARIO_PATH))


@pytest.fixture(scope="module")
def point_mass_recovery_result():
    return simulate(load_scenario(POINT_MASS_SCENARIO_PATH))


def test_dual_deployment_triggers_and_inflates_both_parachutes(recovery_result):
    assert recovery_result.request.task_kind == (
        "single_stage_rigid_body_6dof_recovery"
    )
    drogue_trigger = recovery_result.event("parachute_drogue_trigger")
    drogue_deployment = recovery_result.event("parachute_drogue_deployment")
    main_trigger = recovery_result.event("parachute_main_trigger")
    main_deployment = recovery_result.event("parachute_main_deployment")

    assert drogue_trigger.time_s >= recovery_result.event("apogee").time_s
    assert drogue_deployment.time_s - drogue_trigger.time_s == pytest.approx(0.5)
    assert main_trigger.attributes["altitude_agl_m"] == pytest.approx(250.0, abs=1.0)
    assert main_deployment.time_s - main_trigger.time_s == pytest.approx(0.7)
    assert recovery_result.metric("recovery_deployment_count").value == 2


def test_recovery_reduces_impact_velocity_to_a_survivable_regime(recovery_result):
    assert recovery_result.metric("impact_speed").value < 10.0
    assert recovery_result.metric("impact_vertical_speed").value < 10.0
    assert recovery_result.metric("recovery_descent_duration").value > 40.0
    assert recovery_result.metric("flight_time").value > 60.0


def test_recovery_model_is_visible_in_equations_and_parameters(recovery_result):
    manifest = recovery_result.model_manifest
    equation_ids = {equation.id for equation in manifest.equations}
    event_ids = {event.id for event in manifest.events}
    parameters = {
        parameter.symbol: parameter.value for parameter in manifest.parameters
    }

    assert "parachute_descent" in equation_ids
    assert {
        "parachute_drogue_trigger",
        "parachute_drogue_deployment",
        "parachute_main_trigger",
        "parachute_main_deployment",
    } <= event_ids
    assert parameters["cd_s,drogue"] == pytest.approx(0.8)
    assert parameters["cd_s,main"] == pytest.approx(8.0)
    assert any(
        reference.endswith("Flight.u_dot_parachute")
        for reference in manifest.implementation_references
    )


def test_recovery_outputs_expose_recovery_phase_and_profile(recovery_result, tmp_path):
    artifacts = write_outputs(recovery_result, tmp_path)

    assert artifacts["recovery_profile"].is_file()
    with artifacts["trajectory"].open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert "recovery" in {row["phase"] for row in rows}


def test_point_mass_recovery_executes_dual_deployment_without_rigid_body(
    point_mass_recovery_result,
):
    result = point_mass_recovery_result

    assert result.request.task_kind == "single_stage_point_mass_3dof_recovery"
    assert result.request.contract.rigid_body is None
    assert result.metric("recovery_deployment_count").value == 2
    assert result.metric("impact_speed").value < 6.0
    assert result.metric("recovery_descent_duration").value > 60.0
    assert {
        "parachute_drogue_deployment",
        "parachute_main_deployment",
    } <= {event.name for event in result.events}
    assert "parachute_descent" in {
        equation.id for equation in result.model_manifest.equations
    }
    assert any(
        reference.endswith("Flight.u_dot_parachute")
        for reference in result.model_manifest.implementation_references
    )
