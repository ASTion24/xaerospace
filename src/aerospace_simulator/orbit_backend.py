from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from .launch_backend import run_launch_request
from .launch_config import (
    LAUNCH_CONTRACT_SCHEMA,
    LAUNCH_TASK_KINDS,
    LaunchToOrbitConfig,
)
from .orbit_config import (
    ORBIT_CONTRACT_SCHEMA,
    ORBIT_TASK_KINDS,
    OrbitPropagationConfig,
)
from .orbit_manifest import build_orbit_model_manifest
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


class TudatPyBackendUnavailableError(RuntimeError):
    """Raised when the isolated TudatPy runtime is unavailable."""


class TudatPyExecutionError(RuntimeError):
    """Raised when TudatPy does not produce a valid orbital propagation."""


class TudatPyBackend:
    def __init__(self) -> None:
        self._capabilities = BackendCapabilities(
            backend_id="tudatpy",
            backend_name="TudatPy",
            backend_version=TUDATPY_VERSION,
            supported_task_kinds=ORBIT_TASK_KINDS + LAUNCH_TASK_KINDS,
            supported_contract_schemas=(
                ORBIT_CONTRACT_SCHEMA,
                LAUNCH_CONTRACT_SCHEMA,
            ),
            supported_family_ids=("launch_to_orbit", "orbit_propagation"),
            supported_component_ids=(
                "launch.environment.rotating_exponential_earth",
                "launch.gravity.spherical_harmonic_j2",
                "launch.guidance.pitch_program",
                "launch.propagator.coupled_translation_mass",
                "launch.staging.two_stage",
                "orbit.gravity.point_mass",
                "orbit.gravity.spherical_harmonic_j2",
                "orbit.environment.exponential_atmosphere",
                "orbit.force.aerodynamic_drag",
                "orbit.propagator.rk4_fixed",
            ),
        )

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def run(self, request: SimulationRequest) -> UnifiedSimulationResult:
        if isinstance(request.contract, LaunchToOrbitConfig):
            return run_launch_request(request, capabilities=self.capabilities)
        if not isinstance(request.contract, OrbitPropagationConfig):
            raise TudatPyExecutionError(
                "TudatPy requires an OrbitPropagationConfig or "
                "LaunchToOrbitConfig contract"
            )
        if request.task_kind != request.contract.dynamics:
            raise TudatPyExecutionError(
                "request task_kind does not match the orbit dynamics contract"
            )
        raw_result = _run_worker(request.contract)
        result = _normalize_result(
            request=request,
            capabilities=self.capabilities,
            config=request.contract,
            raw_result=raw_result,
        )
        return result


def _run_worker(config: OrbitPropagationConfig) -> dict[str, Any]:
    python_executable, runtime_home = runtime_paths()
    try:
        validate_runtime(python_executable, runtime_home)
    except TudatRuntimeUnavailableError as exc:
        raise TudatPyBackendUnavailableError(str(exc)) from exc
    worker_path = Path(__file__).with_name("tudat_worker.py")
    payload = {
        "dynamics": config.dynamics,
        "contract": config.protocol_document(),
    }
    environment = {
        **os.environ,
        "HOME": str(runtime_home),
        "PYTHONNOUSERSITE": "1",
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
    except FileNotFoundError as exc:
        raise TudatPyBackendUnavailableError(
            f"unable to launch TudatPy runtime: {python_executable}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TudatPyExecutionError(
            "TudatPy worker exceeded the 180 second execution limit"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "worker exited without an error message"
        raise TudatPyExecutionError(f"TudatPy worker failed: {detail}")
    try:
        decoded = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TudatPyExecutionError("TudatPy worker returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise TudatPyExecutionError("TudatPy worker result must be an object")
    result = decoded
    return result


def _array(
    raw_result: dict[str, Any],
    name: str,
    *,
    dimensions: int,
) -> np.ndarray:
    try:
        values = np.asarray(raw_result[name], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise TudatPyExecutionError(
            f"TudatPy worker field {name!r} is not a numeric array"
        ) from exc
    if values.ndim != dimensions or values.size == 0:
        raise TudatPyExecutionError(
            f"TudatPy worker field {name!r} has the wrong dimensions"
        )
    if not np.all(np.isfinite(values)):
        raise TudatPyExecutionError(
            f"TudatPy worker field {name!r} contains non-finite values"
        )
    return values


def _validate_worker_result(
    raw_result: dict[str, Any],
    config: OrbitPropagationConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    expected_fields = {
        "backend_version",
        "dynamics",
        "aerodynamic_drag_enabled",
        "frame",
        "time_s",
        "states",
        "keplerian",
    }
    if set(raw_result) != expected_fields:
        raise TudatPyExecutionError(
            "TudatPy worker result fields do not match the adapter contract"
        )
    if raw_result["backend_version"] != TUDATPY_VERSION:
        raise TudatPyExecutionError(
            "TudatPy worker version does not match the adapter version"
        )
    if raw_result["dynamics"] != config.dynamics:
        raise TudatPyExecutionError(
            "TudatPy worker changed the requested dynamics contract"
        )
    if raw_result["aerodynamic_drag_enabled"] is not config.aerodynamics.enabled:
        raise TudatPyExecutionError(
            "TudatPy worker changed the requested aerodynamic-drag setting"
        )
    if raw_result["frame"] != config.frame:
        raise TudatPyExecutionError(
            "TudatPy worker changed the requested coordinate frame"
        )
    time_s = _array(raw_result, "time_s", dimensions=1)
    states = _array(raw_result, "states", dimensions=2)
    keplerian = _array(raw_result, "keplerian", dimensions=2)
    expected_samples = (
        round(config.propagation.duration_s / config.propagation.output_interval_s) + 1
    )
    if len(time_s) != expected_samples:
        raise TudatPyExecutionError(
            "TudatPy worker returned an unexpected output sample count"
        )
    if states.shape != (expected_samples, 6):
        raise TudatPyExecutionError(
            "TudatPy worker Cartesian state history must have shape (N, 6)"
        )
    if keplerian.shape != (expected_samples, 6):
        raise TudatPyExecutionError(
            "TudatPy worker Keplerian history must have shape (N, 6)"
        )
    expected_time = np.arange(expected_samples, dtype=float)
    expected_time *= config.propagation.output_interval_s
    if not np.allclose(time_s, expected_time, rtol=0.0, atol=1e-8):
        raise TudatPyExecutionError(
            "TudatPy worker time axis does not match the requested sampling"
        )
    result = (time_s, states, keplerian)
    return result


def _normalize_result(
    *,
    request: SimulationRequest,
    capabilities: BackendCapabilities,
    config: OrbitPropagationConfig,
    raw_result: dict[str, Any],
) -> UnifiedSimulationResult:
    time_s, states, keplerian = _validate_worker_result(raw_result, config)
    position = states[:, :3]
    velocity = states[:, 3:]
    radius = np.linalg.norm(position, axis=1)
    speed = np.linalg.norm(velocity, axis=1)
    altitude = radius - config.central_body.equatorial_radius_m
    gravitational_potential = config.central_body.gravitational_parameter_m3_s2 / radius
    if config.dynamics == "earth_orbit_j2":
        sin_latitude = position[:, 2] / radius
        legendre_p2 = 0.5 * (3.0 * sin_latitude**2 - 1.0)
        gravitational_potential *= (
            1.0
            - config.central_body.j2
            * (config.central_body.equatorial_radius_m / radius) ** 2
            * legendre_p2
        )
    specific_energy = 0.5 * speed**2 - gravitational_potential
    specific_angular_momentum = np.linalg.norm(
        np.cross(position, velocity),
        axis=1,
    )
    semi_major_axis = keplerian[:, 0]
    eccentricity = keplerian[:, 1]
    inclination_deg = np.rad2deg(keplerian[:, 2])
    argument_of_periapsis_deg = np.rad2deg(keplerian[:, 3])
    raan_deg = np.rad2deg(keplerian[:, 4])
    true_anomaly_deg = np.mod(np.rad2deg(keplerian[:, 5]), 360.0)
    element_frame = "earth_centered_J2000_osculating"

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
        ResultChannel("speed", "speed", "m/s", "earth_centered_J2000", speed),
        ResultChannel(
            "semi_major_axis",
            "osculating_semi_major_axis",
            "m",
            element_frame,
            semi_major_axis,
        ),
        ResultChannel(
            "eccentricity",
            "osculating_eccentricity",
            "1",
            element_frame,
            eccentricity,
        ),
        ResultChannel(
            "inclination",
            "osculating_inclination",
            "deg",
            element_frame,
            inclination_deg,
        ),
        ResultChannel(
            "argument_of_periapsis",
            "osculating_argument_of_periapsis",
            "deg",
            element_frame,
            argument_of_periapsis_deg,
        ),
        ResultChannel(
            "raan",
            "osculating_right_ascension_of_ascending_node",
            "deg",
            element_frame,
            raan_deg,
        ),
        ResultChannel(
            "true_anomaly",
            "osculating_true_anomaly",
            "deg",
            element_frame,
            true_anomaly_deg,
        ),
        ResultChannel(
            "specific_orbital_energy",
            "specific_mechanical_energy",
            "J/kg",
            "earth_centered_J2000",
            specific_energy,
        ),
        ResultChannel(
            "specific_angular_momentum",
            "specific_angular_momentum_magnitude",
            "m^2/s",
            "earth_centered_J2000",
            specific_angular_momentum,
        ),
    )
    final_epoch = (
        config.propagation.start_epoch_s_since_j2000 + config.propagation.duration_s
    )
    events = (
        SimulationEvent(
            name="propagation_start",
            time_s=0.0,
            attributes={
                "epoch_s_since_j2000": (config.propagation.start_epoch_s_since_j2000),
                "frame": config.frame,
            },
        ),
        SimulationEvent(
            name="propagation_end",
            time_s=config.propagation.duration_s,
            attributes={
                "epoch_s_since_j2000": final_epoch,
                "frame": config.frame,
            },
        ),
    )
    energy_drift = np.max(
        np.abs((specific_energy - specific_energy[0]) / specific_energy[0])
    )
    angular_momentum_drift = np.max(
        np.abs(
            (specific_angular_momentum - specific_angular_momentum[0])
            / specific_angular_momentum[0]
        )
    )
    energy_metric_name = (
        "max_relative_specific_energy_variation"
        if config.aerodynamics.enabled
        else "max_relative_specific_energy_drift"
    )
    angular_momentum_metric_name = (
        "max_relative_specific_angular_momentum_variation"
        if config.dynamics == "earth_orbit_j2" or config.aerodynamics.enabled
        else "max_relative_specific_angular_momentum_drift"
    )
    metrics = (
        ResultMetric("propagation_duration", config.propagation.duration_s, "s"),
        ResultMetric("sample_count", float(len(time_s)), "1"),
        ResultMetric("minimum_altitude", float(np.min(altitude)), "m"),
        ResultMetric("maximum_altitude", float(np.max(altitude)), "m"),
        ResultMetric(
            "initial_periapsis_altitude",
            float(semi_major_axis[0] * (1.0 - eccentricity[0]))
            - config.central_body.equatorial_radius_m,
            "m",
        ),
        ResultMetric(
            "initial_apoapsis_altitude",
            float(semi_major_axis[0] * (1.0 + eccentricity[0]))
            - config.central_body.equatorial_radius_m,
            "m",
        ),
        ResultMetric(
            "semi_major_axis_change",
            float(semi_major_axis[-1] - semi_major_axis[0]),
            "m",
        ),
        ResultMetric(
            "eccentricity_change",
            float(eccentricity[-1] - eccentricity[0]),
            "1",
        ),
        ResultMetric(
            "raan_change",
            float(raan_deg[-1] - raan_deg[0]),
            "deg",
        ),
        ResultMetric(
            "nodal_precession_rate",
            float(raan_deg[-1] - raan_deg[0]) / config.propagation.duration_s * 86400.0,
            "deg/day",
        ),
        ResultMetric(
            energy_metric_name,
            float(energy_drift),
            "1",
        ),
        ResultMetric(
            angular_momentum_metric_name,
            float(angular_momentum_drift),
            "1",
        ),
    )
    manifest = build_orbit_model_manifest(
        config,
        initial_cartesian_state=states[0],
        backend_version=capabilities.backend_version,
    )
    result = UnifiedSimulationResult(
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
                    f"TudatPy executed {request.task_kind} without fallback in "
                    "an isolated runtime."
                ),
            ),
        ),
    )
    return result
