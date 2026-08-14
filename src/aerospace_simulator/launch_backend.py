from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from .launch_config import LAUNCH_TASK_KINDS, LaunchToOrbitConfig
from .launch_manifest import build_launch_model_manifest
from .protocol import (
    PROTOCOL_VERSION,
    BackendCapabilities,
    Diagnostic,
    ResultChannel,
    ResultMetric,
    SimulationEvent,
    SimulationRequest,
    UnifiedSimulationResult,
)
from .tudat_runtime import (
    TUDATPY_VERSION,
    TudatRuntimeUnavailableError,
    project_root,
    runtime_paths,
    validate_runtime,
)


class TudatLaunchExecutionError(RuntimeError):
    """Raised when TudatPy cannot complete a physically valid launch."""


def run_launch_request(
    request: SimulationRequest,
    *,
    capabilities: BackendCapabilities,
) -> UnifiedSimulationResult:
    if not isinstance(request.contract, LaunchToOrbitConfig):
        raise TudatLaunchExecutionError(
            "TudatPy launch execution requires a LaunchToOrbitConfig contract"
        )
    if request.task_kind not in LAUNCH_TASK_KINDS:
        raise TudatLaunchExecutionError(
            "request task_kind does not match the launch dynamics contract"
        )
    raw_result = _run_worker(request.contract)
    return _normalize_result(
        request=request,
        capabilities=capabilities,
        config=request.contract,
        raw_result=raw_result,
    )


def _run_worker(config: LaunchToOrbitConfig) -> dict[str, Any]:
    python_executable, runtime_home = runtime_paths()
    try:
        validate_runtime(python_executable, runtime_home)
    except TudatRuntimeUnavailableError as exc:
        raise TudatLaunchExecutionError(str(exc)) from exc
    worker_path = Path(__file__).with_name("tudat_launch_worker.py")
    environment = {
        **os.environ,
        "HOME": str(runtime_home),
        "PYTHONNOUSERSITE": "1",
    }
    payload = {
        "dynamics": config.dynamics,
        "contract": config.protocol_document(),
    }
    try:
        completed = subprocess.run(
            [str(python_executable), str(worker_path)],
            input=json.dumps(payload, allow_nan=False),
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
            cwd=project_root(),
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise TudatLaunchExecutionError(
            "unable to complete the isolated TudatPy launch worker"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "worker exited without an error message"
        raise TudatLaunchExecutionError(f"TudatPy launch worker failed: {detail}")
    try:
        decoded = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TudatLaunchExecutionError(
            "TudatPy launch worker returned invalid JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise TudatLaunchExecutionError(
            "TudatPy launch worker result must be an object"
        )
    return decoded


def _array(
    raw_result: dict[str, Any],
    name: str,
    *,
    dimensions: int,
) -> np.ndarray:
    try:
        values = np.asarray(raw_result[name], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise TudatLaunchExecutionError(
            f"TudatPy launch worker field {name!r} is not a numeric array"
        ) from exc
    if values.ndim != dimensions or values.size == 0:
        raise TudatLaunchExecutionError(
            f"TudatPy launch worker field {name!r} has the wrong dimensions"
        )
    if not np.all(np.isfinite(values)):
        raise TudatLaunchExecutionError(
            f"TudatPy launch worker field {name!r} contains non-finite values"
        )
    return values


def _validate_worker_result(
    raw_result: dict[str, Any],
    config: LaunchToOrbitConfig,
) -> dict[str, np.ndarray]:
    expected_fields = {
        "backend_version",
        "dynamics",
        "frame",
        "time_s",
        "states",
        "mass_kg",
        "stage_index",
        "pitch_command_deg",
        "thrust_n",
        "insertion_state",
        "insertion_keplerian",
        "final_keplerian",
        "stage_burnout_mass_kg",
        "stage_post_separation_mass_kg",
    }
    if set(raw_result) != expected_fields:
        raise TudatLaunchExecutionError(
            "TudatPy launch worker fields do not match the adapter contract"
        )
    if raw_result["backend_version"] != TUDATPY_VERSION:
        raise TudatLaunchExecutionError(
            "TudatPy launch worker version does not match the adapter version"
        )
    if raw_result["dynamics"] != config.dynamics:
        raise TudatLaunchExecutionError(
            "TudatPy launch worker changed the requested dynamics"
        )
    if raw_result["frame"] != config.frame:
        raise TudatLaunchExecutionError(
            "TudatPy launch worker changed the requested frame"
        )
    arrays = {
        "time_s": _array(raw_result, "time_s", dimensions=1),
        "states": _array(raw_result, "states", dimensions=2),
        "mass_kg": _array(raw_result, "mass_kg", dimensions=1),
        "stage_index": _array(raw_result, "stage_index", dimensions=1),
        "pitch_command_deg": _array(
            raw_result,
            "pitch_command_deg",
            dimensions=1,
        ),
        "thrust_n": _array(raw_result, "thrust_n", dimensions=1),
        "insertion_state": _array(
            raw_result,
            "insertion_state",
            dimensions=1,
        ),
        "insertion_keplerian": _array(
            raw_result,
            "insertion_keplerian",
            dimensions=1,
        ),
        "final_keplerian": _array(
            raw_result,
            "final_keplerian",
            dimensions=1,
        ),
        "stage_burnout_mass_kg": _array(
            raw_result,
            "stage_burnout_mass_kg",
            dimensions=1,
        ),
        "stage_post_separation_mass_kg": _array(
            raw_result,
            "stage_post_separation_mass_kg",
            dimensions=1,
        ),
    }
    expected_samples = (
        round(config.duration_s / config.propagation.output_interval_s) + 1
    )
    if len(arrays["time_s"]) != expected_samples:
        raise TudatLaunchExecutionError(
            "TudatPy launch worker returned an unexpected sample count"
        )
    if arrays["states"].shape != (expected_samples, 6):
        raise TudatLaunchExecutionError(
            "TudatPy launch Cartesian history must have shape (N, 6)"
        )
    for name in (
        "mass_kg",
        "stage_index",
        "pitch_command_deg",
        "thrust_n",
    ):
        if len(arrays[name]) != expected_samples:
            raise TudatLaunchExecutionError(
                f"TudatPy launch worker field {name!r} has the wrong length"
            )
    for name in ("insertion_state", "insertion_keplerian", "final_keplerian"):
        if arrays[name].shape != (6,):
            raise TudatLaunchExecutionError(
                f"TudatPy launch worker field {name!r} must contain six values"
            )
    if arrays["stage_burnout_mass_kg"].shape != (2,) or arrays[
        "stage_post_separation_mass_kg"
    ].shape != (2,):
        raise TudatLaunchExecutionError(
            "TudatPy launch worker must report both stage mass transitions"
        )
    expected_time = np.arange(expected_samples, dtype=float)
    expected_time *= config.propagation.output_interval_s
    if not np.allclose(
        arrays["time_s"],
        expected_time,
        rtol=0.0,
        atol=1e-8,
    ):
        raise TudatLaunchExecutionError(
            "TudatPy launch time axis does not match requested sampling"
        )
    if np.any(np.diff(arrays["mass_kg"]) > 1e-6):
        raise TudatLaunchExecutionError(
            "TudatPy launch mass history increased during propagation"
        )
    return arrays


def _orbit_altitudes(
    keplerian: np.ndarray,
    equatorial_radius_m: float,
) -> tuple[float, float]:
    semi_major_axis = float(keplerian[0])
    eccentricity = float(keplerian[1])
    if semi_major_axis <= 0.0 or not 0.0 <= eccentricity < 1.0:
        raise TudatLaunchExecutionError(
            "second-stage cutoff did not produce a bound elliptic orbit"
        )
    return (
        semi_major_axis * (1.0 - eccentricity) - equatorial_radius_m,
        semi_major_axis * (1.0 + eccentricity) - equatorial_radius_m,
    )


def _assert_orbit_acceptance(
    config: LaunchToOrbitConfig,
    insertion_keplerian: np.ndarray,
    final_keplerian: np.ndarray,
) -> tuple[float, float, float, float]:
    radius = config.central_body.equatorial_radius_m
    insertion_periapsis, insertion_apoapsis = _orbit_altitudes(
        insertion_keplerian,
        radius,
    )
    final_periapsis, final_apoapsis = _orbit_altitudes(final_keplerian, radius)
    target = config.target_orbit.altitude_m
    tolerance = config.target_orbit.altitude_tolerance_m
    lower = target - tolerance
    upper = target + tolerance
    if not (
        lower <= insertion_periapsis <= upper and lower <= insertion_apoapsis <= upper
    ):
        raise TudatLaunchExecutionError(
            "launch missed target orbit: insertion periapsis/apoapsis "
            f"{insertion_periapsis:.1f}/{insertion_apoapsis:.1f} m are outside "
            f"[{lower:.1f}, {upper:.1f}] m"
        )
    if float(insertion_keplerian[1]) > config.target_orbit.maximum_eccentricity:
        raise TudatLaunchExecutionError(
            "launch missed target orbit: insertion eccentricity "
            f"{float(insertion_keplerian[1]):.6f} exceeds "
            f"{config.target_orbit.maximum_eccentricity:.6f}"
        )
    if final_periapsis <= 0.0 or final_apoapsis <= 0.0:
        raise TudatLaunchExecutionError(
            "post-insertion TudatPy propagation produced an Earth-intersecting orbit"
        )
    return (
        insertion_periapsis,
        insertion_apoapsis,
        final_periapsis,
        final_apoapsis,
    )


def _normalize_result(
    *,
    request: SimulationRequest,
    capabilities: BackendCapabilities,
    config: LaunchToOrbitConfig,
    raw_result: dict[str, Any],
) -> UnifiedSimulationResult:
    arrays = _validate_worker_result(raw_result, config)
    time_s = arrays["time_s"]
    states = arrays["states"]
    position = states[:, :3]
    velocity = states[:, 3:]
    radius = np.linalg.norm(position, axis=1)
    speed = np.linalg.norm(velocity, axis=1)
    altitude = radius - config.central_body.equatorial_radius_m
    angular_velocity = np.asarray([0.0, 0.0, config.central_body.rotation_rate_rad_s])
    atmosphere_velocity = np.cross(
        np.broadcast_to(angular_velocity, position.shape),
        position,
    )
    air_relative_velocity = velocity - atmosphere_velocity
    airspeed = np.linalg.norm(air_relative_velocity, axis=1)
    density = 1.225 * np.exp(-np.maximum(altitude, 0.0) / 7_200.0)
    dynamic_pressure = 0.5 * density * airspeed**2
    initial_radial = position[0] / np.linalg.norm(position[0])
    central_angle = np.arccos(np.clip(position @ initial_radial / radius, -1.0, 1.0))
    downrange = config.central_body.equatorial_radius_m * central_angle
    specific_energy = (
        0.5 * speed**2 - config.central_body.gravitational_parameter_m3_s2 / radius
    )

    (
        insertion_periapsis,
        insertion_apoapsis,
        final_periapsis,
        final_apoapsis,
    ) = _assert_orbit_acceptance(
        config,
        arrays["insertion_keplerian"],
        arrays["final_keplerian"],
    )
    expected_final_mass = config.vehicle.payload_mass_kg + config.stages[-1].dry_mass_kg
    mass_balance_error = float(arrays["mass_kg"][-1] - expected_final_mass)
    if abs(mass_balance_error) > 1e-3:
        raise TudatLaunchExecutionError(
            "launch mass balance failed: final mass differs from payload plus "
            "upper-stage dry mass"
        )
    if np.min(altitude) < config.launch_site.elevation_m - 1.0:
        raise TudatLaunchExecutionError(
            "launch trajectory crossed below the launch-site elevation"
        )

    channels = (
        ResultChannel(
            "position_x", "position", "m", "earth_centered_J2000", position[:, 0]
        ),
        ResultChannel(
            "position_y", "position", "m", "earth_centered_J2000", position[:, 1]
        ),
        ResultChannel(
            "position_z", "position", "m", "earth_centered_J2000", position[:, 2]
        ),
        ResultChannel(
            "velocity_x", "velocity", "m/s", "earth_centered_J2000", velocity[:, 0]
        ),
        ResultChannel(
            "velocity_y", "velocity", "m/s", "earth_centered_J2000", velocity[:, 1]
        ),
        ResultChannel(
            "velocity_z", "velocity", "m/s", "earth_centered_J2000", velocity[:, 2]
        ),
        ResultChannel("orbital_radius", "distance", "m", "earth_center", radius),
        ResultChannel(
            "altitude",
            "radial_altitude",
            "m",
            "earth_equatorial_reference_sphere",
            altitude,
        ),
        ResultChannel(
            "downrange",
            "surface_distance",
            "m",
            "launch_site_great_circle",
            downrange,
        ),
        ResultChannel("speed", "speed", "m/s", "earth_centered_J2000", speed),
        ResultChannel(
            "air_relative_speed",
            "speed",
            "m/s",
            "rotating_atmosphere",
            airspeed,
        ),
        ResultChannel(
            "dynamic_pressure",
            "pressure",
            "Pa",
            "rotating_atmosphere",
            dynamic_pressure,
        ),
        ResultChannel(
            "vehicle_mass",
            "mass",
            "kg",
            "launch_vehicle",
            arrays["mass_kg"],
        ),
        ResultChannel(
            "active_stage",
            "stage_index",
            "1",
            "launch_vehicle",
            arrays["stage_index"],
        ),
        ResultChannel(
            "pitch_command",
            "guidance_pitch",
            "deg",
            "local_horizontal",
            arrays["pitch_command_deg"],
        ),
        ResultChannel(
            "thrust",
            "force",
            "N",
            "launch_vehicle",
            arrays["thrust_n"],
        ),
        ResultChannel(
            "specific_orbital_energy",
            "specific_mechanical_energy",
            "J/kg",
            "earth_centered_J2000",
            specific_energy,
        ),
    )
    stage_1_burnout = config.stages[0].burn_time_s
    insertion_time = config.insertion_time_s
    events = (
        SimulationEvent(
            "liftoff",
            0.0,
            {
                "mass_kg": config.lift_off_mass_kg,
                "frame": config.frame,
            },
        ),
        SimulationEvent(
            "stage_1_burnout",
            stage_1_burnout,
            {
                "mass_before_separation_kg": float(arrays["stage_burnout_mass_kg"][0]),
            },
        ),
        SimulationEvent(
            "stage_1_separation",
            stage_1_burnout,
            {
                "jettisoned_dry_mass_kg": config.stages[0].dry_mass_kg,
                "mass_after_separation_kg": float(
                    arrays["stage_post_separation_mass_kg"][0]
                ),
            },
        ),
        SimulationEvent(
            "stage_2_ignition",
            stage_1_burnout,
            {"stage_id": config.stages[1].stage_id},
        ),
        SimulationEvent(
            "stage_2_burnout",
            insertion_time,
            {
                "mass_kg": float(arrays["stage_burnout_mass_kg"][1]),
                "altitude_m": float(
                    np.linalg.norm(arrays["insertion_state"][:3])
                    - config.central_body.equatorial_radius_m
                ),
            },
        ),
        SimulationEvent(
            "orbital_insertion",
            insertion_time,
            {
                "periapsis_altitude_m": insertion_periapsis,
                "apoapsis_altitude_m": insertion_apoapsis,
                "eccentricity": float(arrays["insertion_keplerian"][1]),
            },
        ),
        SimulationEvent(
            "orbit_verification_end",
            config.duration_s,
            {
                "periapsis_altitude_m": final_periapsis,
                "apoapsis_altitude_m": final_apoapsis,
            },
        ),
    )
    insertion_state = arrays["insertion_state"]
    insertion_radius = np.linalg.norm(insertion_state[:3])
    insertion_speed = np.linalg.norm(insertion_state[3:])
    metrics = (
        ResultMetric("propagation_duration", config.duration_s, "s"),
        ResultMetric("sample_count", float(len(time_s)), "1"),
        ResultMetric("lift_off_mass", config.lift_off_mass_kg, "kg"),
        ResultMetric("payload_delivered", config.vehicle.payload_mass_kg, "kg"),
        ResultMetric("final_vehicle_mass", float(arrays["mass_kg"][-1]), "kg"),
        ResultMetric("mass_balance_error", mass_balance_error, "kg"),
        ResultMetric(
            "propellant_consumed",
            sum(stage.propellant_mass_kg for stage in config.stages),
            "kg",
        ),
        ResultMetric(
            "jettisoned_dry_mass",
            config.stages[0].dry_mass_kg,
            "kg",
        ),
        ResultMetric(
            "maximum_dynamic_pressure",
            float(np.max(dynamic_pressure)),
            "Pa",
        ),
        ResultMetric("maximum_speed", float(np.max(speed)), "m/s"),
        ResultMetric(
            "insertion_altitude",
            insertion_radius - config.central_body.equatorial_radius_m,
            "m",
        ),
        ResultMetric("insertion_speed", insertion_speed, "m/s"),
        ResultMetric(
            "insertion_periapsis_altitude",
            insertion_periapsis,
            "m",
        ),
        ResultMetric(
            "insertion_apoapsis_altitude",
            insertion_apoapsis,
            "m",
        ),
        ResultMetric(
            "insertion_eccentricity",
            float(arrays["insertion_keplerian"][1]),
            "1",
        ),
        ResultMetric(
            "insertion_specific_orbital_energy",
            (
                0.5 * insertion_speed**2
                - config.central_body.gravitational_parameter_m3_s2 / insertion_radius
            ),
            "J/kg",
        ),
        ResultMetric("final_periapsis_altitude", final_periapsis, "m"),
        ResultMetric("final_apoapsis_altitude", final_apoapsis, "m"),
    )
    manifest = build_launch_model_manifest(
        config,
        initial_cartesian_state=states[0],
        backend_version=capabilities.backend_version,
    )
    return UnifiedSimulationResult(
        protocol_version=PROTOCOL_VERSION,
        request=request,
        backend=capabilities,
        time_s=time_s,
        channels=channels,
        events=events,
        metrics=metrics,
        model_manifest=manifest,
        diagnostics=(
            Diagnostic(
                level="info",
                code="backend_contract_executed",
                message=(
                    "TudatPy executed coupled translational and mass propagation "
                    "for both launch stages without fallback."
                ),
            ),
            Diagnostic(
                level="info",
                code="target_orbit_verified",
                message=(
                    "Insertion periapsis, apoapsis, eccentricity, mass balance, "
                    "and post-insertion coast passed physical acceptance."
                ),
            ),
        ),
    )
