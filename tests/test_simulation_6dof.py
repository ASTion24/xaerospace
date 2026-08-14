from pathlib import Path

import numpy as np
import pytest

from aerospace_simulator.config import load_scenario
from aerospace_simulator.simulation import simulate

SCENARIO_PATH = Path(__file__).parents[1] / "scenarios" / "single_stage_6dof_demo.json"


@pytest.fixture(scope="module")
def result_6dof():
    return simulate(load_scenario(SCENARIO_PATH))


def test_six_dof_flight_completes_with_all_expected_events(result_6dof):
    assert result_6dof.protocol_version == 1
    assert result_6dof.request.task_kind == "single_stage_rigid_body_6dof"
    assert result_6dof.backend.backend_id == "rocketpy"
    assert result_6dof.channel("omega3").unit == "rad/s"
    assert result_6dof.channel("omega3").frame == "body"
    assert tuple(event.name for event in result_6dof.events) == (
        "rail_departure",
        "burnout",
        "apogee",
        "impact",
    )
    assert result_6dof.metric("apogee_agl").value > 500
    assert (
        result_6dof.metric("flight_time").value
        > result_6dof.metric("apogee_time").value
    )


def test_six_dof_solves_real_attitude_and_angular_rate_dynamics(result_6dof):
    e0 = result_6dof.channel("quaternion_e0").values
    quaternion_norm = np.sqrt(
        e0**2
        + result_6dof.channel("quaternion_e1").values ** 2
        + result_6dof.channel("quaternion_e2").values ** 2
        + result_6dof.channel("quaternion_e3").values ** 2
    )
    omega3 = result_6dof.channel("omega3").values
    angular_rate = result_6dof.channel("angular_rate").values

    assert np.max(np.abs(quaternion_norm - 1.0)) < 1e-3
    assert np.ptp(e0) > 0.1
    assert np.max(np.abs(omega3)) > 1.0
    assert result_6dof.metric("max_angular_rate").value > 1.0
    assert np.count_nonzero(np.abs(angular_rate) > 0.1) > 10


def test_six_dof_manifest_exposes_inertia_aerodynamics_and_rotation(result_6dof):
    manifest = result_6dof.model_manifest
    equation_ids = {equation.id for equation in manifest.equations}
    parameters = {
        parameter.symbol: parameter.value for parameter in manifest.parameters
    }
    state_roles = {state.symbol: state.role for state in manifest.state_vector}

    assert {
        "aerodynamic_resultants",
        "coupled_force_terms",
        "rotational_dynamics",
        "quaternion_kinematics",
        "translation_6dof",
    } <= equation_ids
    assert state_roles["e_0"] == "integrated attitude"
    assert state_roles["omega_3"] == "integrated rotation"
    assert parameters["I_vehicle,11"] == pytest.approx(6.321)
    assert parameters["cant_fin"] == pytest.approx(0.5)
    assert any(
        reference.endswith("Flight.u_dot_generalized")
        for reference in manifest.implementation_references
    )
