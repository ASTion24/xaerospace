from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping
from typing import Any

import numpy as np
import tudatpy
from tudatpy.kernel.astro import element_conversion
from tudatpy.kernel.dynamics import environment_setup, propagation_setup, simulator

SUPPORTED_DYNAMICS = {"earth_orbit_two_body", "earth_orbit_j2"}


class WorkerInputError(ValueError):
    """Raised when the parent process supplies an invalid worker payload."""


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkerInputError(f"{path} must be an object")
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
    dynamics: str,
) -> object:
    central = _mapping(contract["central_body"], "central_body")
    spacecraft = _mapping(contract["spacecraft"], "spacecraft")
    aerodynamics = _mapping(contract["aerodynamics"], "aerodynamics")
    propagation = _mapping(contract["propagation"], "propagation")
    central_name = str(central["name"])
    spacecraft_name = str(spacecraft["name"])
    frame = str(contract["frame"])
    start_epoch = _number(
        propagation["start_epoch_s_since_j2000"],
        "propagation.start_epoch_s_since_j2000",
    )
    gravitational_parameter = _number(
        central["gravitational_parameter_m3_s2"],
        "central_body.gravitational_parameter_m3_s2",
    )

    settings = environment_setup.BodyListSettings("SSB", frame)
    settings.add_empty_settings(central_name)
    settings.add_empty_settings(spacecraft_name)
    central_settings = settings.get(central_name)
    spacecraft_settings = settings.get(spacecraft_name)
    central_settings.ephemeris_settings = environment_setup.ephemeris.constant(
        np.zeros(6),
        "SSB",
        frame,
    )
    if dynamics == "earth_orbit_j2":
        cosine_coefficients = np.zeros((3, 3))
        cosine_coefficients[0, 0] = 1.0
        cosine_coefficients[2, 0] = -_number(
            central["j2"],
            "central_body.j2",
        ) / np.sqrt(5.0)
        central_settings.gravity_field_settings = (
            environment_setup.gravity_field.spherical_harmonic(
                gravitational_parameter,
                _number(
                    central["equatorial_radius_m"],
                    "central_body.equatorial_radius_m",
                ),
                cosine_coefficients,
                np.zeros((3, 3)),
                "Earth_fixed",
            )
        )
        central_settings.rotation_model_settings = (
            environment_setup.rotation_model.simple(
                frame,
                "Earth_fixed",
                np.eye(3),
                start_epoch,
                _number(
                    central["rotation_rate_rad_s"],
                    "central_body.rotation_rate_rad_s",
                ),
            )
        )
    else:
        central_settings.gravity_field_settings = (
            environment_setup.gravity_field.central(gravitational_parameter)
        )
    spacecraft_settings.constant_mass = _number(
        spacecraft["mass_kg"],
        "spacecraft.mass_kg",
    )
    if aerodynamics["enabled"] is True:
        central_settings.shape_settings = environment_setup.shape.spherical(
            _number(
                central["equatorial_radius_m"],
                "central_body.equatorial_radius_m",
            )
        )
        central_settings.atmosphere_settings = environment_setup.atmosphere.exponential(
            _number(
                aerodynamics["atmosphere_scale_height_m"],
                "aerodynamics.atmosphere_scale_height_m",
            ),
            _number(
                aerodynamics["atmosphere_surface_density_kg_m3"],
                "aerodynamics.atmosphere_surface_density_kg_m3",
            ),
        )
        spacecraft_settings.aerodynamic_coefficient_settings = (
            environment_setup.aerodynamic_coefficients.constant(
                _number(
                    aerodynamics["reference_area_m2"],
                    "aerodynamics.reference_area_m2",
                ),
                [
                    _number(
                        aerodynamics["drag_coefficient"],
                        "aerodynamics.drag_coefficient",
                    ),
                    0.0,
                    0.0,
                ],
            )
        )
    result = environment_setup.create_system_of_bodies(settings)
    return result


def _initial_cartesian_state(contract: Mapping[str, Any]) -> np.ndarray:
    initial = _mapping(contract["initial_state"], "initial_state")
    central = _mapping(contract["central_body"], "central_body")
    keplerian = np.asarray(
        [
            _number(initial["semi_major_axis_m"], "initial_state.semi_major_axis_m"),
            _number(initial["eccentricity"], "initial_state.eccentricity"),
            np.deg2rad(
                _number(initial["inclination_deg"], "initial_state.inclination_deg")
            ),
            np.deg2rad(
                _number(
                    initial["argument_of_periapsis_deg"],
                    "initial_state.argument_of_periapsis_deg",
                )
            ),
            np.deg2rad(_number(initial["raan_deg"], "initial_state.raan_deg")),
            np.deg2rad(
                _number(initial["true_anomaly_deg"], "initial_state.true_anomaly_deg")
            ),
        ],
        dtype=float,
    )
    result = element_conversion.keplerian_to_cartesian(
        keplerian,
        _number(
            central["gravitational_parameter_m3_s2"],
            "central_body.gravitational_parameter_m3_s2",
        ),
    )
    return np.asarray(result, dtype=float)


def _propagate(payload: Mapping[str, Any]) -> dict[str, object]:
    dynamics = payload.get("dynamics")
    if dynamics not in SUPPORTED_DYNAMICS:
        raise WorkerInputError(f"unsupported dynamics: {dynamics!r}")
    contract = _mapping(payload.get("contract"), "contract")
    central = _mapping(contract["central_body"], "central_body")
    spacecraft = _mapping(contract["spacecraft"], "spacecraft")
    propagation = _mapping(contract["propagation"], "propagation")
    aerodynamics = _mapping(contract["aerodynamics"], "aerodynamics")
    central_name = str(central["name"])
    spacecraft_name = str(spacecraft["name"])
    start_epoch = _number(
        propagation["start_epoch_s_since_j2000"],
        "propagation.start_epoch_s_since_j2000",
    )
    duration = _number(propagation["duration_s"], "propagation.duration_s")
    step_size = _number(propagation["step_size_s"], "propagation.step_size_s")
    output_interval = _number(
        propagation["output_interval_s"],
        "propagation.output_interval_s",
    )
    gravitational_parameter = _number(
        central["gravitational_parameter_m3_s2"],
        "central_body.gravitational_parameter_m3_s2",
    )

    bodies = _build_body_system(contract, str(dynamics))
    gravity_acceleration = (
        propagation_setup.acceleration.spherical_harmonic_gravity(2, 0)
        if dynamics == "earth_orbit_j2"
        else propagation_setup.acceleration.point_mass_gravity()
    )
    accelerations = [gravity_acceleration]
    if aerodynamics["enabled"] is True:
        accelerations.append(propagation_setup.acceleration.aerodynamic())
    acceleration_models = propagation_setup.create_acceleration_models(
        bodies,
        {spacecraft_name: {central_name: accelerations}},
        [spacecraft_name],
        [central_name],
    )
    integrator_settings = propagation_setup.integrator.runge_kutta_fixed_step(
        step_size,
        propagation_setup.integrator.CoefficientSets.rk_4,
    )
    termination_settings = propagation_setup.propagator.time_termination(
        start_epoch + duration,
        terminate_exactly_on_final_condition=True,
    )
    propagator_settings = propagation_setup.propagator.translational(
        [central_name],
        acceleration_models,
        [spacecraft_name],
        _initial_cartesian_state(contract),
        start_epoch,
        integrator_settings,
        termination_settings,
    )
    dynamics_simulator = simulator.create_dynamics_simulator(
        bodies,
        propagator_settings,
    )
    history = sorted(dynamics_simulator.propagation_results.state_history.items())
    all_times = np.asarray([float(epoch) for epoch, _ in history], dtype=float)
    all_states = np.vstack([np.asarray(state, dtype=float) for _, state in history])
    expected_step_samples = round(duration / step_size) + 1
    if len(all_times) != expected_step_samples:
        raise RuntimeError(
            "TudatPy returned an unexpected integration sample count: "
            f"{len(all_times)} != {expected_step_samples}"
        )
    stride = round(output_interval / step_size)
    selected_indices = np.arange(0, len(all_times), stride, dtype=int)
    if selected_indices[-1] != len(all_times) - 1:
        selected_indices = np.append(selected_indices, len(all_times) - 1)
    times = all_times[selected_indices] - start_epoch
    states = all_states[selected_indices]
    keplerian = np.vstack(
        [
            element_conversion.cartesian_to_keplerian(
                state,
                gravitational_parameter,
            )
            for state in states
        ]
    )
    keplerian[:, 4] = np.unwrap(keplerian[:, 4])

    result = {
        "backend_version": tudatpy.__version__,
        "dynamics": dynamics,
        "aerodynamic_drag_enabled": aerodynamics["enabled"],
        "frame": contract["frame"],
        "time_s": times.tolist(),
        "states": states.tolist(),
        "keplerian": keplerian.tolist(),
    }
    return result


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
