import json
from pathlib import Path

import numpy as np
import pytest

from aerospace_simulator.outputs import write_outputs
from aerospace_simulator.request_io import load_request
from aerospace_simulator.simulation import create_default_registry, run_request

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "earth_orbit_two_body_demo.json"
)


@pytest.fixture(scope="module")
def two_body_result():
    return run_request(load_request(SCENARIO_PATH))


def test_default_registry_routes_orbit_contract_to_tudatpy(two_body_result):
    registry = create_default_registry()

    assert {capability.backend_id for capability in registry.capabilities()} == {
        "basilisk",
        "jsbsim",
        "rocketpy",
        "tudatpy",
    }
    assert two_body_result.backend.backend_id == "tudatpy"
    assert two_body_result.backend.backend_version == "1.0.0"
    assert two_body_result.request.task_kind == "earth_orbit_two_body"
    assert two_body_result.diagnostics[0].code == "backend_contract_executed"


def test_two_body_propagation_conserves_energy_and_angular_momentum(two_body_result):
    energy_drift = two_body_result.metric("max_relative_specific_energy_drift").value
    angular_momentum_drift = two_body_result.metric(
        "max_relative_specific_angular_momentum_drift"
    ).value

    assert energy_drift < 1e-8
    assert angular_momentum_drift < 1e-8
    assert abs(two_body_result.metric("semi_major_axis_change").value) < 0.1
    assert abs(two_body_result.metric("raan_change").value) < 1e-8
    assert np.ptp(two_body_result.channel("eccentricity").values) < 1e-8


def test_two_body_result_has_normalized_units_frames_and_equations(two_body_result):
    assert two_body_result.channel("position_x").unit == "m"
    assert two_body_result.channel("position_x").frame == "earth_centered_J2000"
    assert two_body_result.channel("raan").unit == "deg"
    assert two_body_result.channel("raan").frame == ("earth_centered_J2000_osculating")
    assert two_body_result.event("propagation_start").time_s == 0.0
    assert two_body_result.event("propagation_end").time_s == pytest.approx(14400.0)
    equation_ids = {
        equation.id for equation in two_body_result.model_manifest.equations
    }
    assert {
        "earth_point_mass_gravity",
        "specific_orbital_energy",
        "osculating_keplerian_elements",
    } <= equation_ids


def test_orbit_outputs_and_request_replay_are_backend_independent(
    two_body_result,
    tmp_path,
):
    artifacts = write_outputs(two_body_result, tmp_path / "initial")
    replayed = run_request(load_request(artifacts["request"]))
    replay_artifacts = write_outputs(replayed, tmp_path / "replay")

    assert {"orbit_profile", "orbital_elements"} <= set(artifacts)
    assert all(
        path.is_file() and path.stat().st_size > 0 for path in artifacts.values()
    )
    assert replayed.request.document() == two_body_result.request.document()
    assert np.array_equal(replayed.time_s, two_body_result.time_s)
    assert np.allclose(
        replayed.channel("position_x").values,
        two_body_result.channel("position_x").values,
        rtol=0.0,
        atol=0.0,
    )
    normalized = json.loads(artifacts["result"].read_text(encoding="utf-8"))
    assert normalized["backend"]["backend_id"] == "tudatpy"
    assert normalized["request"]["contract_schema"] == (
        "wms.aerospace.orbit_propagation.v2"
    )
    assert replay_artifacts["orbit_profile"].is_file()
