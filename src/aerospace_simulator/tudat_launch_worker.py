from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import tudatpy
from tudatpy.kernel.astro import element_conversion
from tudatpy.kernel.dynamics import environment_setup, propagation_setup, simulator

STANDARD_GRAVITY_M_S2 = 9.80665
SUPPORTED_DYNAMICS = {"two_stage_launch_to_orbit"}


class WorkerInputError(ValueError):
    """Raised when the parent process supplies an invalid launch payload."""


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkerInputError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise WorkerInputError(f"{path} must be an array")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkerInputError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise WorkerInputError(f"{path} must be finite")
    return number


def _build_body_system(
    contract: Mapping[str, Any],
    *,
    mass_kg: float,
) -> object:
    central = _mapping(contract["central_body"], "central_body")
    vehicle = _mapping(contract["vehicle"], "vehicle")
    launch_site = _mapping(contract["launch_site"], "launch_site")
    central_name = str(central["name"])
    frame = str(contract["frame"])
    start_epoch = _number(
        launch_site["start_epoch_s_since_j2000"],
        "launch_site.start_epoch_s_since_j2000",
    )
    gravitational_parameter = _number(
        central["gravitational_parameter_m3_s2"],
        "central_body.gravitational_parameter_m3_s2",
    )
    equatorial_radius = _number(
        central["equatorial_radius_m"],
        "central_body.equatorial_radius_m",
    )

    settings = environment_setup.BodyListSettings("SSB", frame)
    settings.add_empty_settings(central_name)
    settings.add_empty_settings("LaunchVehicle")
    central_settings = settings.get(central_name)
    vehicle_settings = settings.get("LaunchVehicle")
    central_settings.ephemeris_settings = environment_setup.ephemeris.constant(
        np.zeros(6),
        "SSB",
        frame,
    )
    cosine_coefficients = np.zeros((3, 3))
    cosine_coefficients[0, 0] = 1.0
    cosine_coefficients[2, 0] = -_number(
        central["j2"],
        "central_body.j2",
    ) / np.sqrt(5.0)
    central_settings.gravity_field_settings = (
        environment_setup.gravity_field.spherical_harmonic(
            gravitational_parameter,
            equatorial_radius,
            cosine_coefficients,
            np.zeros((3, 3)),
            "Earth_fixed",
        )
    )
    central_settings.rotation_model_settings = environment_setup.rotation_model.simple(
        frame,
        "Earth_fixed",
        np.eye(3),
        start_epoch,
        _number(
            central["rotation_rate_rad_s"],
            "central_body.rotation_rate_rad_s",
        ),
    )
    central_settings.shape_settings = environment_setup.shape.spherical(
        equatorial_radius
    )
    central_settings.atmosphere_settings = environment_setup.atmosphere.exponential(
        7_200.0,
        1.225,
    )
    vehicle_settings.constant_mass = mass_kg
    vehicle_settings.aerodynamic_coefficient_settings = (
        environment_setup.aerodynamic_coefficients.constant(
            _number(vehicle["reference_area_m2"], "vehicle.reference_area_m2"),
            [
                _number(vehicle["drag_coefficient"], "vehicle.drag_coefficient"),
                0.0,
                0.0,
            ],
        )
    )
    return environment_setup.create_system_of_bodies(settings)


def _initial_state(contract: Mapping[str, Any]) -> np.ndarray:
    central = _mapping(contract["central_body"], "central_body")
    launch_site = _mapping(contract["launch_site"], "launch_site")
    radius = _number(
        central["equatorial_radius_m"],
        "central_body.equatorial_radius_m",
    ) + _number(launch_site["elevation_m"], "launch_site.elevation_m")
    latitude = np.deg2rad(
        _number(launch_site["latitude_deg"], "launch_site.latitude_deg")
    )
    longitude = np.deg2rad(
        _number(launch_site["longitude_deg"], "launch_site.longitude_deg")
    )
    radial = np.asarray(
        [
            np.cos(latitude) * np.cos(longitude),
            np.cos(latitude) * np.sin(longitude),
            np.sin(latitude),
        ],
        dtype=float,
    )
    position = radius * radial
    angular_velocity = np.asarray(
        [
            0.0,
            0.0,
            _number(
                central["rotation_rate_rad_s"],
                "central_body.rotation_rate_rad_s",
            ),
        ]
    )
    velocity = np.cross(angular_velocity, position)
    velocity += (
        _number(
            launch_site["initial_vertical_speed_m_s"],
            "launch_site.initial_vertical_speed_m_s",
        )
        * radial
    )
    return np.concatenate((position, velocity))


def _pitch_at(stage: Mapping[str, Any], elapsed_time_s: float) -> float:
    points = _sequence(
        stage["guidance_pitch_program"],
        "stage.guidance_pitch_program",
    )
    times = []
    pitches = []
    for index, point in enumerate(points):
        pair = _sequence(point, f"stage.guidance_pitch_program[{index}]")
        if len(pair) != 2:
            raise WorkerInputError(
                f"stage.guidance_pitch_program[{index}] must contain two values"
            )
        times.append(_number(pair[0], f"stage.guidance_pitch_program[{index}][0]"))
        pitches.append(_number(pair[1], f"stage.guidance_pitch_program[{index}][1]"))
    return float(np.interp(elapsed_time_s, times, pitches))


def _powered_segment(
    contract: Mapping[str, Any],
    *,
    initial_state: np.ndarray,
    initial_mass_kg: float,
    start_time_s: float,
    stage: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    central = _mapping(contract["central_body"], "central_body")
    propagation = _mapping(contract["propagation"], "propagation")
    launch_site = _mapping(contract["launch_site"], "launch_site")
    central_name = str(central["name"])
    epoch = _number(
        launch_site["start_epoch_s_since_j2000"],
        "launch_site.start_epoch_s_since_j2000",
    )
    burn_time = _number(stage["burn_time_s"], "stage.burn_time_s")
    thrust_n = _number(stage["thrust_n"], "stage.thrust_n")
    mass_flow_rate = thrust_n / (
        _number(stage["specific_impulse_s"], "stage.specific_impulse_s")
        * STANDARD_GRAVITY_M_S2
    )
    bodies = _build_body_system(contract, mass_kg=initial_mass_kg)
    vehicle = bodies.get_body("LaunchVehicle")

    def thrust_acceleration(time: float) -> np.ndarray:
        state = np.asarray(vehicle.state, dtype=float)
        if not np.all(np.isfinite(state)):
            return np.zeros(3)
        position = state[:3]
        radial = position / np.linalg.norm(position)
        east = np.cross(np.asarray([0.0, 0.0, 1.0]), radial)
        east /= np.linalg.norm(east)
        elapsed = float(time) - epoch - start_time_s
        pitch_rad = np.deg2rad(_pitch_at(stage, elapsed))
        direction = np.sin(pitch_rad) * radial + np.cos(pitch_rad) * east
        return thrust_n / float(vehicle.mass) * direction

    acceleration_settings = {
        "LaunchVehicle": {
            central_name: [
                propagation_setup.acceleration.spherical_harmonic_gravity(2, 0),
                propagation_setup.acceleration.aerodynamic(),
            ],
            "LaunchVehicle": [
                propagation_setup.acceleration.custom_acceleration(thrust_acceleration)
            ],
        }
    }
    acceleration_models = propagation_setup.create_acceleration_models(
        bodies,
        acceleration_settings,
        ["LaunchVehicle"],
        [central_name],
    )
    mass_rate_models = propagation_setup.create_mass_rate_models(
        bodies,
        {
            "LaunchVehicle": [
                propagation_setup.mass_rate.custom_mass_rate(
                    lambda _time: -mass_flow_rate
                )
            ]
        },
        acceleration_models,
    )
    absolute_start = epoch + start_time_s
    termination = propagation_setup.propagator.time_termination(
        absolute_start + burn_time,
        terminate_exactly_on_final_condition=True,
    )
    translational = propagation_setup.propagator.translational(
        [central_name],
        acceleration_models,
        ["LaunchVehicle"],
        initial_state,
        termination,
    )
    mass = propagation_setup.propagator.mass(
        ["LaunchVehicle"],
        mass_rate_models,
        [initial_mass_kg],
        termination,
    )
    integrator = propagation_setup.integrator.runge_kutta_fixed_step(
        _number(propagation["step_size_s"], "propagation.step_size_s"),
        propagation_setup.integrator.CoefficientSets.rk_4,
    )
    multitype = propagation_setup.propagator.multitype(
        [translational, mass],
        integrator,
        absolute_start,
        termination,
    )
    dynamics_simulator = simulator.create_dynamics_simulator(bodies, multitype)
    history = sorted(dynamics_simulator.propagation_results.state_history.items())
    times = np.asarray([float(time) - epoch for time, _ in history], dtype=float)
    states = np.vstack([np.asarray(state, dtype=float) for _, state in history])
    return times, states


def _coast_segment(
    contract: Mapping[str, Any],
    *,
    initial_state: np.ndarray,
    mass_kg: float,
    start_time_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    central = _mapping(contract["central_body"], "central_body")
    propagation = _mapping(contract["propagation"], "propagation")
    launch_site = _mapping(contract["launch_site"], "launch_site")
    central_name = str(central["name"])
    epoch = _number(
        launch_site["start_epoch_s_since_j2000"],
        "launch_site.start_epoch_s_since_j2000",
    )
    duration = _number(
        propagation["post_insertion_coast_duration_s"],
        "propagation.post_insertion_coast_duration_s",
    )
    bodies = _build_body_system(contract, mass_kg=mass_kg)
    acceleration_models = propagation_setup.create_acceleration_models(
        bodies,
        {
            "LaunchVehicle": {
                central_name: [
                    propagation_setup.acceleration.spherical_harmonic_gravity(2, 0),
                    propagation_setup.acceleration.aerodynamic(),
                ]
            }
        },
        ["LaunchVehicle"],
        [central_name],
    )
    absolute_start = epoch + start_time_s
    termination = propagation_setup.propagator.time_termination(
        absolute_start + duration,
        terminate_exactly_on_final_condition=True,
    )
    integrator = propagation_setup.integrator.runge_kutta_fixed_step(
        _number(propagation["step_size_s"], "propagation.step_size_s"),
        propagation_setup.integrator.CoefficientSets.rk_4,
    )
    propagator = propagation_setup.propagator.translational(
        [central_name],
        acceleration_models,
        ["LaunchVehicle"],
        initial_state,
        absolute_start,
        integrator,
        termination,
    )
    dynamics_simulator = simulator.create_dynamics_simulator(bodies, propagator)
    history = sorted(dynamics_simulator.propagation_results.state_history.items())
    times = np.asarray([float(time) - epoch for time, _ in history], dtype=float)
    states = np.vstack(
        [
            np.concatenate((np.asarray(state, dtype=float), [mass_kg]))
            for _, state in history
        ]
    )
    return times, states


def _replace_boundary(
    chunks: list[np.ndarray],
    values: np.ndarray,
) -> None:
    if chunks:
        chunks[-1] = chunks[-1][:-1]
    chunks.append(values)


def _propagate(payload: Mapping[str, Any]) -> dict[str, object]:
    dynamics = payload.get("dynamics")
    if dynamics not in SUPPORTED_DYNAMICS:
        raise WorkerInputError(f"unsupported dynamics: {dynamics!r}")
    contract = _mapping(payload.get("contract"), "contract")
    stages = _sequence(contract["stages"], "stages")
    if len(stages) != 2:
        raise WorkerInputError("stages must contain exactly two stages")
    vehicle = _mapping(contract["vehicle"], "vehicle")
    propagation = _mapping(contract["propagation"], "propagation")
    central = _mapping(contract["central_body"], "central_body")
    gravitational_parameter = _number(
        central["gravitational_parameter_m3_s2"],
        "central_body.gravitational_parameter_m3_s2",
    )

    stage_documents = [
        _mapping(stage, f"stages[{index}]") for index, stage in enumerate(stages)
    ]
    current_mass = _number(vehicle["payload_mass_kg"], "vehicle.payload_mass_kg")
    current_mass += sum(
        _number(stage["dry_mass_kg"], "stage.dry_mass_kg")
        + _number(stage["propellant_mass_kg"], "stage.propellant_mass_kg")
        for stage in stage_documents
    )
    current_state = _initial_state(contract)
    current_time = 0.0
    time_chunks: list[np.ndarray] = []
    state_chunks: list[np.ndarray] = []
    stage_chunks: list[np.ndarray] = []
    pitch_chunks: list[np.ndarray] = []
    thrust_chunks: list[np.ndarray] = []
    burnout_masses: list[float] = []
    post_separation_masses: list[float] = []

    for index, stage in enumerate(stage_documents, start=1):
        times, states = _powered_segment(
            contract,
            initial_state=current_state,
            initial_mass_kg=current_mass,
            start_time_s=current_time,
            stage=stage,
        )
        elapsed = times - current_time
        pitches = np.asarray(
            [_pitch_at(stage, float(value)) for value in elapsed],
            dtype=float,
        )
        _replace_boundary(time_chunks, times)
        _replace_boundary(state_chunks, states)
        _replace_boundary(stage_chunks, np.full(len(times), float(index)))
        _replace_boundary(pitch_chunks, pitches)
        _replace_boundary(
            thrust_chunks,
            np.full(len(times), _number(stage["thrust_n"], "stage.thrust_n")),
        )
        current_state = states[-1, :6]
        current_mass = float(states[-1, 6])
        burnout_masses.append(current_mass)
        current_time += _number(stage["burn_time_s"], "stage.burn_time_s")
        if index < len(stage_documents):
            current_mass -= _number(stage["dry_mass_kg"], "stage.dry_mass_kg")
        post_separation_masses.append(current_mass)

    insertion_state = current_state.copy()
    insertion_keplerian = np.asarray(
        element_conversion.cartesian_to_keplerian(
            insertion_state,
            gravitational_parameter,
        ),
        dtype=float,
    )
    coast_times, coast_states = _coast_segment(
        contract,
        initial_state=current_state,
        mass_kg=current_mass,
        start_time_s=current_time,
    )
    _replace_boundary(time_chunks, coast_times)
    _replace_boundary(state_chunks, coast_states)
    _replace_boundary(stage_chunks, np.zeros(len(coast_times)))
    _replace_boundary(pitch_chunks, np.zeros(len(coast_times)))
    _replace_boundary(thrust_chunks, np.zeros(len(coast_times)))

    all_times = np.concatenate(time_chunks)
    all_states = np.vstack(state_chunks)
    all_stage_indices = np.concatenate(stage_chunks)
    all_pitches = np.concatenate(pitch_chunks)
    all_thrust = np.concatenate(thrust_chunks)
    output_interval = _number(
        propagation["output_interval_s"],
        "propagation.output_interval_s",
    )
    quotient = all_times / output_interval
    selected = np.isclose(quotient, np.round(quotient), rtol=0.0, atol=1e-8)
    times = all_times[selected]
    states = all_states[selected]
    stage_indices = all_stage_indices[selected]
    pitches = all_pitches[selected]
    thrust = all_thrust[selected]
    final_keplerian = np.asarray(
        element_conversion.cartesian_to_keplerian(
            states[-1, :6],
            gravitational_parameter,
        ),
        dtype=float,
    )
    if (
        not np.all(np.isfinite(states))
        or not np.all(np.isfinite(insertion_keplerian))
        or not np.all(np.isfinite(final_keplerian))
    ):
        raise RuntimeError("TudatPy launch propagation produced non-finite states")

    return {
        "backend_version": tudatpy.__version__,
        "dynamics": dynamics,
        "frame": contract["frame"],
        "time_s": times.tolist(),
        "states": states[:, :6].tolist(),
        "mass_kg": states[:, 6].tolist(),
        "stage_index": stage_indices.tolist(),
        "pitch_command_deg": pitches.tolist(),
        "thrust_n": thrust.tolist(),
        "insertion_state": insertion_state.tolist(),
        "insertion_keplerian": insertion_keplerian.tolist(),
        "final_keplerian": final_keplerian.tolist(),
        "stage_burnout_mass_kg": burnout_masses,
        "stage_post_separation_mass_kg": post_separation_masses,
    }


def main() -> int:
    exit_code = 0
    try:
        raw = json.load(sys.stdin)
        payload = _mapping(raw, "payload")
        result = _propagate(payload)
        json.dump(result, sys.stdout, allow_nan=False, separators=(",", ":"))
        sys.stdout.write("\n")
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
    ) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
