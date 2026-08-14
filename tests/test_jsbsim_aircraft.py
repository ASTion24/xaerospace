import json
from pathlib import Path

import numpy as np
import pytest

from aerospace_simulator.outputs import write_outputs
from aerospace_simulator.request_io import load_request
from aerospace_simulator.simulation import create_default_registry, run_request

SCENARIO_DIR = Path(__file__).parents[1] / "scenarios"
PULSE_SCENARIO_PATH = SCENARIO_DIR / "c172p_aileron_pulse_demo.json"
TRIM_SCENARIO_PATH = SCENARIO_DIR / "c172p_trimmed_cruise_demo.json"
EXPANDED_TRIM_SCENARIOS = (
    SCENARIO_DIR / "c172r_trimmed_cruise_demo.json",
    SCENARIO_DIR / "c182_trimmed_cruise_demo.json",
    SCENARIO_DIR / "c310_trimmed_cruise_demo.json",
    SCENARIO_DIR / "j3cub_trimmed_cruise_demo.json",
)
TRIM_ACCEPTANCE = {
    "c172r": (0.1, 0.01),
    "c182": (0.1, 0.01),
    "c310": (0.25, 0.025),
    "J3Cub": (0.1, 0.01),
}


@pytest.fixture(scope="module")
def pulse_result():
    return run_request(load_request(PULSE_SCENARIO_PATH))


@pytest.fixture(scope="module")
def trim_result():
    return run_request(load_request(TRIM_SCENARIO_PATH))


@pytest.fixture(scope="module", params=EXPANDED_TRIM_SCENARIOS)
def expanded_trim_result(request):
    return run_request(load_request(request.param))


def test_default_registry_routes_aircraft_contract_to_jsbsim(pulse_result):
    registry = create_default_registry()

    assert {capability.backend_id for capability in registry.capabilities()} == {
        "basilisk",
        "jsbsim",
        "rocketpy",
        "tudatpy",
    }
    assert pulse_result.backend.backend_id == "jsbsim"
    assert pulse_result.backend.backend_version == "1.3.1"
    assert pulse_result.request.task_kind == "fixed_wing_trimmed_6dof"
    assert pulse_result.diagnostics[0].code == "backend_contract_executed"


def test_trimmed_c172p_is_a_stable_six_dof_reference(trim_result):
    assert abs(trim_result.metric("altitude_change").value) < 0.1
    assert abs(trim_result.metric("calibrated_airspeed_change").value) < 0.01
    assert trim_result.metric("maximum_roll").value < 0.05
    assert trim_result.metric("maximum_body_angular_rate").value < 1e-3
    assert trim_result.metric("final_ground_distance").value > 1400.0
    assert trim_result.metric("trim_throttle").value == pytest.approx(
        0.694,
        abs=0.01,
    )


def test_expanded_aircraft_models_trim_and_propagate_stably(
    expanded_trim_result,
):
    model_id = expanded_trim_result.request.contract.aircraft.model_id
    altitude_limit, airspeed_limit = TRIM_ACCEPTANCE[model_id]

    assert abs(expanded_trim_result.metric("altitude_change").value) < altitude_limit
    assert (
        abs(expanded_trim_result.metric("calibrated_airspeed_change").value)
        < airspeed_limit
    )
    assert expanded_trim_result.metric("maximum_roll").value < 0.25
    assert expanded_trim_result.metric("maximum_body_angular_rate").value < 1e-3
    assert any(
        reference.endswith(f"{model_id}/{model_id}.xml")
        for reference in expanded_trim_result.model_manifest.implementation_references
    )


def test_aileron_pulse_produces_real_lateral_dynamics(
    pulse_result,
    trim_result,
):
    aileron_command = pulse_result.channel("aileron_command").values
    left_aileron = pulse_result.channel("left_aileron_position").values
    right_aileron = pulse_result.channel("right_aileron_position").values

    assert np.ptp(aileron_command) == pytest.approx(0.08, abs=1e-9)
    assert np.ptp(left_aileron) > 0.05
    assert np.ptp(right_aileron) > 0.05
    assert pulse_result.metric("maximum_roll").value > 5.0
    assert pulse_result.metric("heading_change").value > 20.0
    assert pulse_result.metric("maximum_body_angular_rate").value > 0.05
    assert pulse_result.metric("maximum_sideslip").value > 0.5
    assert (
        pulse_result.metric("maximum_roll").value
        > 100.0 * trim_result.metric("maximum_roll").value
    )


def test_aircraft_result_exposes_units_frames_events_and_equations(pulse_result):
    assert pulse_result.channel("velocity_north").unit == "m/s"
    assert pulse_result.channel("velocity_north").frame == ("local_north_east_down")
    assert pulse_result.channel("body_u").frame == "body_forward_right_down"
    assert pulse_result.channel("aircraft_mass").unit == "kg"
    assert tuple(event.name for event in pulse_result.events) == (
        "trim_complete",
        "control_aileron_pulse_start",
        "control_aileron_pulse_end",
        "propagation_end",
    )
    assert pulse_result.event("control_aileron_pulse_start").time_s == 5.0
    assert pulse_result.event("control_aileron_pulse_end").time_s == 7.0

    manifest = pulse_result.model_manifest
    equation_ids = {equation.id for equation in manifest.equations}
    parameters = {
        parameter.symbol: parameter.value for parameter in manifest.parameters
    }
    assert len(manifest.state_vector) == 13
    assert {
        "body_translation",
        "rigid_body_rotation",
        "quaternion_kinematics",
        "aerodynamic_resultants",
        "piston_propeller_propulsion",
        "control_schedule",
    } <= equation_ids
    assert parameters["S"] == pytest.approx(16.17, rel=0.01)
    assert parameters["m_0"] > 700.0
    assert any(
        reference.endswith("c172p/c172p.xml")
        for reference in manifest.implementation_references
    )


def test_aircraft_outputs_and_request_replay_are_deterministic(
    pulse_result,
    tmp_path,
):
    artifacts = write_outputs(pulse_result, tmp_path / "initial")
    replayed = run_request(load_request(artifacts["request"]))
    replay_artifacts = write_outputs(replayed, tmp_path / "replay")

    assert {"aircraft_path", "aircraft_response"} <= set(artifacts)
    assert all(
        path.is_file() and path.stat().st_size > 0 for path in artifacts.values()
    )
    assert replayed.request.document() == pulse_result.request.document()
    assert np.array_equal(replayed.time_s, pulse_result.time_s)
    assert np.allclose(
        replayed.channel("roll").values,
        pulse_result.channel("roll").values,
        rtol=0.0,
        atol=0.0,
    )
    normalized = json.loads(artifacts["result"].read_text(encoding="utf-8"))
    assert normalized["backend"]["backend_id"] == "jsbsim"
    assert normalized["request"]["contract_schema"] == (
        "wms.aerospace.aircraft_flight.v1"
    )
    assert replay_artifacts["aircraft_response"].is_file()
