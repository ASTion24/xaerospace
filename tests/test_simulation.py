from math import pi
from pathlib import Path

import numpy as np
import pytest

from aerospace_simulator.config import load_scenario
from aerospace_simulator.simulation import simulate

SCENARIO_PATH = Path(__file__).parents[1] / "scenarios" / "single_stage_demo.json"


@pytest.fixture(scope="module")
def result():
    return simulate(load_scenario(SCENARIO_PATH))


def test_rocketpy_backend_runs_a_complete_single_stage_flight(result):
    assert result.backend.backend_name == "RocketPy"
    assert result.backend.backend_version == "1.13.0"
    assert result.request.task_kind == "single_stage_point_mass_3dof"
    assert tuple(event.name for event in result.events) == (
        "rail_departure",
        "burnout",
        "apogee",
        "impact",
    )
    assert (
        result.metric("rail_departure_time").value < result.metric("burnout_time").value
    )
    assert result.metric("burnout_time").value < result.metric("apogee_time").value
    assert result.metric("apogee_agl").value > 500
    assert result.metric("max_speed").value > 100
    assert result.metric("flight_time").value > result.metric("apogee_time").value


def test_sampled_profile_has_explicit_phases_and_reaches_ground(result):
    assert len(result.time_s) > 100
    altitude = result.channel("altitude_agl").values
    horizontal_range = result.channel("horizontal_range").values
    angular_rate = result.channel("angular_rate").values
    assert np.isclose(altitude[0], 0.0, atol=1e-6)
    assert np.isclose(altitude[-1], 0.0, atol=1e-3)
    assert np.all(horizontal_range >= 0)
    assert np.allclose(angular_rate, 0.0)
    assert result.metric("max_angular_rate").value == 0.0


def test_model_manifest_matches_the_pinned_runtime_contract(result):
    manifest = result.model_manifest
    equation_ids = {equation.id for equation in manifest.equations}
    parameters = {
        parameter.symbol: parameter.value for parameter in manifest.parameters
    }
    initial_state = {
        parameter.symbol: parameter.value for parameter in manifest.initial_state
    }

    assert manifest.backend_version == "1.13.0"
    assert len(manifest.state_vector) == 13
    assert len(manifest.initial_state) == 13
    assert {
        "mass_depletion",
        "axial_drag",
        "rail_constraint",
        "free_flight_translation",
        "attitude",
    } <= equation_ids
    assert parameters["m_0"] == pytest.approx(17.5)
    assert parameters["A_ref"] == pytest.approx(pi * 0.075**2)
    assert parameters["k_wc"] == 0
    assert initial_state["z_0"] == pytest.approx(1400.0)
    assert initial_state["v_z0"] == 0
    attitude = next(
        equation for equation in manifest.equations if equation.id == "attitude"
    )
    assert attitude.expression == "q_dot = 0; omega_dot = 0"
    assert any(
        "u_dot_generalized_3dof" in reference
        for reference in manifest.implementation_references
    )
