import json
from pathlib import Path

import numpy as np
import pytest

from aerospace_simulator.outputs import write_outputs
from aerospace_simulator.request_io import load_request
from aerospace_simulator.simulation import create_default_registry, run_request

SCENARIO_DIR = Path(__file__).parents[1] / "scenarios"
CONTROLLED_SCENARIO_PATH = SCENARIO_DIR / "spacecraft_attitude_gnc_demo.json"
UNCONTROLLED_SCENARIO_PATH = SCENARIO_DIR / "spacecraft_attitude_uncontrolled_demo.json"
RATE_DAMPING_SCENARIO_PATH = SCENARIO_DIR / "spacecraft_rate_damping_demo.json"


@pytest.fixture(scope="module")
def controlled_result():
    return run_request(load_request(CONTROLLED_SCENARIO_PATH))


@pytest.fixture(scope="module")
def uncontrolled_result():
    return run_request(load_request(UNCONTROLLED_SCENARIO_PATH))


@pytest.fixture(scope="module")
def rate_damping_result():
    return run_request(load_request(RATE_DAMPING_SCENARIO_PATH))


def test_default_registry_routes_attitude_contract_to_basilisk(controlled_result):
    registry = create_default_registry()

    assert {capability.backend_id for capability in registry.capabilities()} == {
        "basilisk",
        "jsbsim",
        "rocketpy",
        "tudatpy",
    }
    assert controlled_result.backend.backend_id == "basilisk"
    assert controlled_result.backend.backend_version == "2.11.0"
    assert controlled_result.request.task_kind == ("spacecraft_inertial_pointing_gnc")
    assert controlled_result.diagnostics[0].code == "backend_contract_executed"


def test_mrp_feedback_converges_but_uncontrolled_spacecraft_does_not(
    controlled_result,
    uncontrolled_result,
):
    controlled_attitude = controlled_result.metric("final_attitude_error_norm").value
    uncontrolled_attitude = uncontrolled_result.metric(
        "final_attitude_error_norm"
    ).value
    controlled_rate = controlled_result.metric("final_angular_rate_error_norm").value
    uncontrolled_rate = uncontrolled_result.metric(
        "final_angular_rate_error_norm"
    ).value

    assert controlled_result.metric("attitude_error_reduction_factor").value < 0.002
    assert controlled_result.metric("angular_rate_error_reduction_factor").value < (
        0.002
    )
    assert controlled_attitude < 1e-3
    assert controlled_rate < 1e-4
    assert uncontrolled_attitude > 0.1
    assert uncontrolled_rate > 0.01
    assert controlled_attitude < 0.01 * uncontrolled_attitude
    assert controlled_rate < 0.01 * uncontrolled_rate


def test_reaction_wheels_apply_saturated_torque_and_exchange_momentum(
    controlled_result,
    uncontrolled_result,
):
    assert controlled_result.metric("maximum_requested_wheel_motor_torque").value > 0.2
    assert controlled_result.metric(
        "maximum_applied_wheel_motor_torque"
    ).value == pytest.approx(0.2, abs=1e-12)
    assert controlled_result.metric("maximum_reaction_wheel_speed_change").value > 100.0
    assert (
        controlled_result.metric("maximum_reaction_wheel_momentum_change").value > 10.0
    )
    assert uncontrolled_result.metric("maximum_applied_wheel_motor_torque").value == 0.0
    assert uncontrolled_result.metric("maximum_reaction_wheel_speed_change").value < 0.1

    for wheel_number in range(1, 4):
        applied = controlled_result.channel(
            f"applied_wheel_motor_torque_{wheel_number}"
        ).values
        assert np.max(np.abs(applied)) <= 0.2 + 1e-12
        assert (
            np.ptp(
                controlled_result.channel(f"reaction_wheel_speed_{wheel_number}").values
            )
            > 1.0
        )


def test_rate_damping_removes_body_rate_without_claiming_attitude_hold(
    rate_damping_result,
):
    result = rate_damping_result

    assert result.request.task_kind == "spacecraft_rate_damping_gnc"
    assert result.metric("angular_rate_error_reduction_factor").value < 1e-8
    assert result.metric("final_angular_rate_error_norm").value < 1e-9
    assert result.metric("final_attitude_error_norm").value > 0.1
    assert result.metric("maximum_applied_wheel_motor_torque").value == pytest.approx(
        0.2,
        abs=1e-12,
    )
    equation_ids = {equation.id for equation in result.model_manifest.equations}
    assert "angular_rate_damping_control" in equation_ids
    assert "mrp_feedback_control" not in equation_ids
    assert any(
        "does not hold an attitude" in limitation
        for limitation in result.model_manifest.limitations
    )


def test_attitude_result_exposes_units_frames_events_and_equations(
    controlled_result,
):
    assert controlled_result.channel("attitude_mrp_x").unit == "1"
    assert controlled_result.channel("attitude_mrp_x").frame == (
        "body_B_relative_inertial_N"
    )
    assert controlled_result.channel("angular_rate_error_x").unit == "rad/s"
    assert controlled_result.channel("angular_rate_error_x").frame == "body_B"
    assert controlled_result.channel("reaction_wheel_speed_1").unit == "rad/s"
    assert (
        controlled_result.channel("reaction_wheel_angular_momentum_1").unit == "N m s"
    )
    assert tuple(event.name for event in controlled_result.events) == (
        "simulation_start",
        "propagation_end",
    )
    assert controlled_result.event("simulation_start").attributes["gnc_enabled"]

    manifest = controlled_result.model_manifest
    equation_ids = {equation.id for equation in manifest.equations}
    parameters = {
        parameter.symbol: parameter.value for parameter in manifest.parameters
    }
    assert len(manifest.state_vector) == 15
    assert {
        "mrp_kinematics",
        "spacecraft_wheel_angular_momentum",
        "reaction_wheel_spin_dynamics",
        "perfect_attitude_navigation",
        "inertial_fixed_guidance",
        "mrp_tracking_error",
        "mrp_feedback_control",
        "minimum_norm_wheel_allocation",
        "fixed_step_rk4",
    } <= equation_ids
    assert parameters["J_s,1"] == pytest.approx(0.07957747154594767)
    assert parameters["u_max"] == 0.2
    assert any(
        reference.endswith("svIntegrators.svIntegratorRK4")
        for reference in manifest.implementation_references
    )


def test_attitude_outputs_and_request_replay_are_deterministic(
    controlled_result,
    tmp_path,
):
    artifacts = write_outputs(controlled_result, tmp_path / "initial")
    replayed = run_request(load_request(artifacts["request"]))
    replay_artifacts = write_outputs(replayed, tmp_path / "replay")

    assert {"spacecraft_attitude", "reaction_wheel_response"} <= set(artifacts)
    assert all(
        path.is_file() and path.stat().st_size > 0 for path in artifacts.values()
    )
    assert replayed.request.document() == controlled_result.request.document()
    assert np.array_equal(replayed.time_s, controlled_result.time_s)
    assert np.array_equal(
        replayed.channel("attitude_error_norm").values,
        controlled_result.channel("attitude_error_norm").values,
    )
    assert np.array_equal(
        replayed.channel("reaction_wheel_speed_2").values,
        controlled_result.channel("reaction_wheel_speed_2").values,
    )
    assert artifacts["result"].read_bytes() == replay_artifacts["result"].read_bytes()
    assert artifacts["trajectory"].read_bytes() == (
        replay_artifacts["trajectory"].read_bytes()
    )
    normalized = json.loads(artifacts["result"].read_text(encoding="utf-8"))
    assert normalized["backend"]["backend_id"] == "basilisk"
    assert normalized["request"]["contract_schema"] == (
        "wms.aerospace.spacecraft_attitude.v1"
    )
    assert replay_artifacts["spacecraft_attitude"].is_file()
