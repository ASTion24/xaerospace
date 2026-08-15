import json
from pathlib import Path

import numpy as np
import pytest

from aerospace_simulator.outputs import write_outputs
from aerospace_simulator.request_io import load_request
from aerospace_simulator.simulation import run_request

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "two_stage_220km_launch_demo.json"
)


@pytest.fixture(scope="module")
def launch_result():
    return run_request(load_request(SCENARIO_PATH))


def test_two_stage_launch_reaches_verified_near_circular_orbit(launch_result):
    assert launch_result.backend.backend_id == "tudatpy"
    assert launch_result.request.task_kind == "two_stage_launch_to_orbit"
    assert (
        190_000.0
        < launch_result.metric("insertion_periapsis_altitude").value
        < 250_000.0
    )
    assert (
        190_000.0
        < launch_result.metric("insertion_apoapsis_altitude").value
        < 250_000.0
    )
    assert launch_result.metric("insertion_eccentricity").value < 0.005
    assert launch_result.metric("insertion_specific_orbital_energy").value < 0.0
    assert launch_result.metric("final_periapsis_altitude").value > 180_000.0
    assert launch_result.metric("final_apoapsis_altitude").value > 180_000.0
    assert {diagnostic.code for diagnostic in launch_result.diagnostics} >= {
        "backend_contract_executed",
        "target_orbit_verified",
    }


def test_launch_mass_balance_and_stage_events_are_physical(launch_result):
    assert launch_result.metric("lift_off_mass").value == pytest.approx(505_000.0)
    assert launch_result.metric("payload_delivered").value == pytest.approx(15_000.0)
    assert launch_result.metric("final_vehicle_mass").value == pytest.approx(23_000.0)
    assert abs(launch_result.metric("mass_balance_error").value) < 1e-6
    mass = launch_result.channel("vehicle_mass").values
    assert np.all(np.diff(mass) <= 1e-6)
    event_names = [event.name for event in launch_result.events]
    assert event_names == [
        "liftoff",
        "stage_1_burnout",
        "stage_1_separation",
        "stage_2_ignition",
        "stage_2_burnout",
        "orbital_insertion",
        "orbit_verification_end",
    ]
    assert launch_result.event("stage_1_burnout").time_s == pytest.approx(155.0)
    assert launch_result.event("orbital_insertion").time_s == pytest.approx(405.0)
    assert launch_result.event("orbit_verification_end").time_s == pytest.approx(1605.0)
    separation = launch_result.event("stage_1_separation")
    assert separation.attributes["jettisoned_dry_mass_kg"] == pytest.approx(25_000.0)


def test_launch_result_exposes_tudat_states_equations_and_outputs(
    launch_result,
    tmp_path,
):
    assert launch_result.channel("position_x").frame == "earth_centered_J2000"
    assert launch_result.channel("vehicle_mass").unit == "kg"
    assert launch_result.channel("pitch_command").unit == "deg"
    assert launch_result.channel("dynamic_pressure").unit == "Pa"
    equation_ids = {equation.id for equation in launch_result.model_manifest.equations}
    assert {
        "launch_j2_gravity",
        "launch_aerodynamic_drag",
        "guided_stage_thrust",
        "launch_mass_depletion",
        "stage_separation_mass_jump",
        "insertion_orbital_elements",
    } <= equation_ids

    artifacts = write_outputs(launch_result, tmp_path / "launch")

    assert {"launch_profile", "orbit_profile", "request", "result"} <= set(artifacts)
    assert all(
        path.is_file() and path.stat().st_size > 0 for path in artifacts.values()
    )
    normalized = json.loads(artifacts["result"].read_text(encoding="utf-8"))
    assert normalized["request"]["contract_schema"] == ("xaerospace.launch_to_orbit.v1")
