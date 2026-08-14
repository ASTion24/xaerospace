from __future__ import annotations

import math
import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

import numpy as np

from .aircraft_config import (
    AIRCRAFT_CONTRACT_SCHEMA,
    AIRCRAFT_TASK_KINDS,
    AircraftFlightConfig,
    ControlSegment,
)
from .aircraft_manifest import build_aircraft_model_manifest
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

JSBSIM_VERSION = "1.3.1"
FOOT_TO_METER = 0.3048
KNOT_TO_METER_PER_SECOND = 0.5144444444444445
LBF_TO_NEWTON = 4.4482216152605
LBF_FOOT_TO_NEWTON_METER = 1.3558179483314
PSF_TO_PASCAL = 47.88025898033584
SLUG_TO_KILOGRAM = 14.593902937206
SLUG_FOOT2_TO_KILOGRAM_METER2 = 1.3558179483314
SLUG_FOOT3_TO_KILOGRAM_METER3 = 515.3788183931961
HORSEPOWER_TO_WATT = 745.6998715822702


class JSBSimBackendUnavailableError(RuntimeError):
    """Raised when the JSBSim Python package is unavailable."""


class JSBSimExecutionError(RuntimeError):
    """Raised when JSBSim cannot execute the requested aircraft contract."""


@dataclass(frozen=True)
class _ChannelSpec:
    name: str
    property_name: str
    quantity: str
    unit: str
    frame: str
    scale: float = 1.0


@dataclass(frozen=True)
class _TrimControls:
    aileron: float
    elevator: float
    rudder: float
    throttle: float
    pitch_trim: float


_CHANNEL_SPECS = (
    _ChannelSpec(
        "north_displacement",
        "position/distance-from-start-lat-mt",
        "position",
        "m",
        "local_north_east_up",
    ),
    _ChannelSpec(
        "east_displacement",
        "position/distance-from-start-lon-mt",
        "position",
        "m",
        "local_north_east_up",
    ),
    _ChannelSpec(
        "altitude_msl",
        "position/h-sl-meters",
        "altitude",
        "m",
        "mean_sea_level",
    ),
    _ChannelSpec(
        "latitude",
        "position/lat-geod-deg",
        "geodetic_latitude",
        "deg",
        "WGS84",
    ),
    _ChannelSpec(
        "longitude",
        "position/long-gc-deg",
        "geocentric_longitude",
        "deg",
        "WGS84",
    ),
    _ChannelSpec(
        "velocity_north",
        "velocities/v-north-fps",
        "velocity",
        "m/s",
        "local_north_east_down",
        FOOT_TO_METER,
    ),
    _ChannelSpec(
        "velocity_east",
        "velocities/v-east-fps",
        "velocity",
        "m/s",
        "local_north_east_down",
        FOOT_TO_METER,
    ),
    _ChannelSpec(
        "velocity_down",
        "velocities/v-down-fps",
        "velocity",
        "m/s",
        "local_north_east_down",
        FOOT_TO_METER,
    ),
    _ChannelSpec(
        "body_u",
        "velocities/u-fps",
        "velocity",
        "m/s",
        "body_forward_right_down",
        FOOT_TO_METER,
    ),
    _ChannelSpec(
        "body_v",
        "velocities/v-fps",
        "velocity",
        "m/s",
        "body_forward_right_down",
        FOOT_TO_METER,
    ),
    _ChannelSpec(
        "body_w",
        "velocities/w-fps",
        "velocity",
        "m/s",
        "body_forward_right_down",
        FOOT_TO_METER,
    ),
    _ChannelSpec(
        "true_airspeed",
        "velocities/vt-fps",
        "airspeed",
        "m/s",
        "air_relative",
        FOOT_TO_METER,
    ),
    _ChannelSpec(
        "calibrated_airspeed",
        "velocities/vc-kts",
        "airspeed",
        "m/s",
        "air_data_calibrated",
        KNOT_TO_METER_PER_SECOND,
    ),
    _ChannelSpec(
        "mach",
        "velocities/mach",
        "mach_number",
        "1",
        "air_relative",
    ),
    _ChannelSpec(
        "roll",
        "attitude/phi-deg",
        "euler_angle",
        "deg",
        "body_to_local_ned",
    ),
    _ChannelSpec(
        "pitch",
        "attitude/theta-deg",
        "euler_angle",
        "deg",
        "body_to_local_ned",
    ),
    _ChannelSpec(
        "heading",
        "attitude/psi-deg",
        "euler_angle",
        "deg",
        "body_to_local_ned",
    ),
    _ChannelSpec(
        "roll_rate",
        "velocities/p-rad_sec",
        "angular_velocity",
        "rad/s",
        "body_forward_right_down",
    ),
    _ChannelSpec(
        "pitch_rate",
        "velocities/q-rad_sec",
        "angular_velocity",
        "rad/s",
        "body_forward_right_down",
    ),
    _ChannelSpec(
        "yaw_rate",
        "velocities/r-rad_sec",
        "angular_velocity",
        "rad/s",
        "body_forward_right_down",
    ),
    _ChannelSpec(
        "angle_of_attack",
        "aero/alpha-deg",
        "aerodynamic_angle",
        "deg",
        "wind_to_body",
    ),
    _ChannelSpec(
        "sideslip",
        "aero/beta-deg",
        "aerodynamic_angle",
        "deg",
        "wind_to_body",
    ),
    _ChannelSpec(
        "load_factor_x",
        "accelerations/Nx",
        "load_factor",
        "1",
        "body_forward_right_down",
    ),
    _ChannelSpec(
        "load_factor_y",
        "accelerations/Ny",
        "load_factor",
        "1",
        "body_forward_right_down",
    ),
    _ChannelSpec(
        "load_factor_z",
        "accelerations/Nz",
        "load_factor",
        "1",
        "body_forward_right_down",
    ),
    _ChannelSpec(
        "force_body_x",
        "forces/fbx-total-lbs",
        "force",
        "N",
        "body_forward_right_down",
        LBF_TO_NEWTON,
    ),
    _ChannelSpec(
        "force_body_y",
        "forces/fby-total-lbs",
        "force",
        "N",
        "body_forward_right_down",
        LBF_TO_NEWTON,
    ),
    _ChannelSpec(
        "force_body_z",
        "forces/fbz-total-lbs",
        "force",
        "N",
        "body_forward_right_down",
        LBF_TO_NEWTON,
    ),
    _ChannelSpec(
        "moment_body_x",
        "moments/l-total-lbsft",
        "moment",
        "N m",
        "body_forward_right_down",
        LBF_FOOT_TO_NEWTON_METER,
    ),
    _ChannelSpec(
        "moment_body_y",
        "moments/m-total-lbsft",
        "moment",
        "N m",
        "body_forward_right_down",
        LBF_FOOT_TO_NEWTON_METER,
    ),
    _ChannelSpec(
        "moment_body_z",
        "moments/n-total-lbsft",
        "moment",
        "N m",
        "body_forward_right_down",
        LBF_FOOT_TO_NEWTON_METER,
    ),
    _ChannelSpec(
        "dynamic_pressure",
        "aero/qbar-psf",
        "dynamic_pressure",
        "Pa",
        "air_relative",
        PSF_TO_PASCAL,
    ),
    _ChannelSpec(
        "air_density",
        "atmosphere/rho-slugs_ft3",
        "density",
        "kg/m^3",
        "local_atmosphere",
        SLUG_FOOT3_TO_KILOGRAM_METER3,
    ),
    _ChannelSpec(
        "aileron_command",
        "fcs/aileron-cmd-norm",
        "control_command",
        "1",
        "trim_relative_schedule",
    ),
    _ChannelSpec(
        "elevator_command",
        "fcs/elevator-cmd-norm",
        "control_command",
        "1",
        "trim_relative_schedule",
    ),
    _ChannelSpec(
        "rudder_command",
        "fcs/rudder-cmd-norm",
        "control_command",
        "1",
        "trim_relative_schedule",
    ),
    _ChannelSpec(
        "throttle_command",
        "fcs/throttle-cmd-norm[0]",
        "control_command",
        "1",
        "engine_0",
    ),
    _ChannelSpec(
        "left_aileron_position",
        "fcs/left-aileron-pos-norm",
        "control_position",
        "1",
        "aircraft_control_surface",
    ),
    _ChannelSpec(
        "right_aileron_position",
        "fcs/right-aileron-pos-norm",
        "control_position",
        "1",
        "aircraft_control_surface",
    ),
    _ChannelSpec(
        "elevator_position",
        "fcs/elevator-pos-norm",
        "control_position",
        "1",
        "aircraft_control_surface",
    ),
    _ChannelSpec(
        "rudder_position",
        "fcs/rudder-pos-norm",
        "control_position",
        "1",
        "aircraft_control_surface",
    ),
    _ChannelSpec(
        "engine_speed",
        "propulsion/engine/engine-rpm",
        "rotational_speed",
        "rpm",
        "engine_0",
    ),
    _ChannelSpec(
        "engine_power",
        "propulsion/engine/power-hp",
        "power",
        "W",
        "engine_0",
        HORSEPOWER_TO_WATT,
    ),
    _ChannelSpec(
        "aircraft_mass",
        "inertia/mass-slugs",
        "mass",
        "kg",
        "aircraft",
        SLUG_TO_KILOGRAM,
    ),
)


class JSBSimBackend:
    def __init__(self) -> None:
        try:
            backend_version = version("jsbsim")
        except PackageNotFoundError:
            backend_version = "unavailable"
        self._capabilities = BackendCapabilities(
            backend_id="jsbsim",
            backend_name="JSBSim",
            backend_version=backend_version,
            supported_task_kinds=AIRCRAFT_TASK_KINDS,
            supported_contract_schemas=(AIRCRAFT_CONTRACT_SCHEMA,),
            supported_family_ids=("aircraft_flight",),
            supported_component_ids=(
                "aircraft.model.c172p",
                "aircraft.model.c172r",
                "aircraft.model.c182",
                "aircraft.model.c310",
                "aircraft.model.j3cub",
                "aircraft.trim.longitudinal",
                "aircraft.control.trim_relative_open_loop",
            ),
        )

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def run(self, request: SimulationRequest) -> UnifiedSimulationResult:
        if not isinstance(request.contract, AircraftFlightConfig):
            raise JSBSimExecutionError(
                "JSBSim requires an AircraftFlightConfig contract"
            )
        if request.task_kind != request.contract.dynamics:
            raise JSBSimExecutionError(
                "request task_kind does not match the aircraft dynamics contract"
            )
        if self.capabilities.backend_version != JSBSIM_VERSION:
            raise JSBSimBackendUnavailableError(
                f"JSBSim {JSBSIM_VERSION} is required; found "
                f"{self.capabilities.backend_version}"
            )
        result = _simulate_jsbsim(
            config=request.contract,
            request=request,
            capabilities=self.capabilities,
        )
        return result


def _simulate_jsbsim(
    *,
    config: AircraftFlightConfig,
    request: SimulationRequest,
    capabilities: BackendCapabilities,
) -> UnifiedSimulationResult:
    os.environ.setdefault("JSBSIM_DEBUG", "0")
    try:
        import jsbsim
    except ImportError as exc:
        raise JSBSimBackendUnavailableError(
            "JSBSim is required. Install this project with its declared dependencies."
        ) from exc

    jsbsim_root = os.path.dirname(os.path.abspath(jsbsim.__file__))
    fdm = jsbsim.FGFDMExec(jsbsim_root)
    fdm.set_debug_level(0)
    if not fdm.load_model(config.aircraft.model_id):
        raise JSBSimExecutionError(
            f"JSBSim could not load aircraft model {config.aircraft.model_id!r}"
        )
    fdm.set_dt(config.propagation.step_size_s)
    _set_initial_conditions(fdm, config)
    if not fdm.run_ic():
        raise JSBSimExecutionError("JSBSim rejected the initial conditions")
    _set_environment(fdm, config)
    fdm["propulsion/set-running"] = -1
    try:
        fdm["simulation/do_simple_trim"] = 1
    except RuntimeError as exc:
        raise JSBSimExecutionError("JSBSim longitudinal trim did not converge") from exc

    trim_controls = _trim_controls(fdm)
    _validate_scheduled_commands(config, trim_controls)
    trim_runtime = _trim_runtime(fdm, config, trim_controls)
    _validate_trim(trim_runtime, config)
    records = {spec.name: [] for spec in _CHANNEL_SPECS}
    time_values: list[float] = []
    total_steps = round(config.propagation.duration_s / config.propagation.step_size_s)
    output_stride = round(
        config.propagation.output_interval_s / config.propagation.step_size_s
    )

    for step in range(total_steps + 1):
        time_s = step * config.propagation.step_size_s
        active_segment = _active_segment(config, time_s)
        _apply_controls(fdm, trim_controls, active_segment)
        if step % output_stride == 0:
            time_values.append(time_s)
            for spec in _CHANNEL_SPECS:
                records[spec.name].append(
                    _property(fdm, spec.property_name) * spec.scale
                )
        if step < total_steps and not fdm.run():
            raise JSBSimExecutionError(
                f"JSBSim stopped before the requested final time at {time_s:.9g} s"
            )

    time_s = np.asarray(time_values, dtype=float)
    expected_samples = (
        round(config.propagation.duration_s / config.propagation.output_interval_s) + 1
    )
    if len(time_s) != expected_samples:
        raise JSBSimExecutionError(
            "JSBSim adapter produced an unexpected output sample count"
        )
    arrays = {name: np.asarray(values, dtype=float) for name, values in records.items()}
    arrays["heading"] = np.rad2deg(np.unwrap(np.deg2rad(arrays["heading"])))
    channels = tuple(
        ResultChannel(
            spec.name,
            spec.quantity,
            spec.unit,
            spec.frame,
            arrays[spec.name],
        )
        for spec in _CHANNEL_SPECS
    )
    events = _events(config, trim_runtime)
    metrics = _metrics(config, arrays, trim_runtime)
    manifest_runtime = {
        **trim_runtime,
        "initial_altitude_msl_m": float(arrays["altitude_msl"][0]),
        "initial_roll_deg": float(arrays["roll"][0]),
        "initial_pitch_deg": float(arrays["pitch"][0]),
        "initial_heading_deg": float(arrays["heading"][0]),
        "initial_u_m_s": float(arrays["body_u"][0]),
        "initial_v_m_s": float(arrays["body_v"][0]),
        "initial_w_m_s": float(arrays["body_w"][0]),
        "mass_kg": float(arrays["aircraft_mass"][0]),
        "ixx_kg_m2": _property(fdm, "inertia/ixx-slugs_ft2")
        * SLUG_FOOT2_TO_KILOGRAM_METER2,
        "iyy_kg_m2": _property(fdm, "inertia/iyy-slugs_ft2")
        * SLUG_FOOT2_TO_KILOGRAM_METER2,
        "izz_kg_m2": _property(fdm, "inertia/izz-slugs_ft2")
        * SLUG_FOOT2_TO_KILOGRAM_METER2,
        "wing_area_m2": _property(fdm, "metrics/Sw-sqft") * FOOT_TO_METER**2,
        "wing_span_m": _property(fdm, "metrics/bw-ft") * FOOT_TO_METER,
        "mean_chord_m": _property(fdm, "metrics/cbarw-ft") * FOOT_TO_METER,
    }
    manifest = build_aircraft_model_manifest(
        config,
        runtime=manifest_runtime,
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
                    f"JSBSim executed {request.task_kind} with the bundled "
                    f"{config.aircraft.model_id} model and no fallback."
                ),
            ),
        ),
    )
    return result


def _set_initial_conditions(fdm: object, config: AircraftFlightConfig) -> None:
    initial = config.initial_condition
    # JSBSim keeps initial-condition properties kinematically consistent, so
    # position and attitude must be assigned before the final airspeed target.
    fdm["ic/lat-geod-deg"] = initial.latitude_deg
    fdm["ic/long-gc-deg"] = initial.longitude_deg
    fdm["ic/h-sl-ft"] = initial.altitude_msl_m / FOOT_TO_METER
    fdm["ic/psi-true-deg"] = initial.heading_deg
    fdm["ic/gamma-deg"] = initial.flight_path_angle_deg
    fdm["ic/vc-kts"] = initial.calibrated_airspeed_m_s / KNOT_TO_METER_PER_SECOND


def _set_environment(fdm: object, config: AircraftFlightConfig) -> None:
    environment = config.environment
    fdm["atmosphere/wind-north-fps"] = environment.wind_north_m_s / FOOT_TO_METER
    fdm["atmosphere/wind-east-fps"] = environment.wind_east_m_s / FOOT_TO_METER
    fdm["atmosphere/wind-down-fps"] = environment.wind_down_m_s / FOOT_TO_METER


def _trim_controls(fdm: object) -> _TrimControls:
    result = _TrimControls(
        aileron=_property(fdm, "fcs/aileron-cmd-norm"),
        elevator=_property(fdm, "fcs/elevator-cmd-norm"),
        rudder=_property(fdm, "fcs/rudder-cmd-norm"),
        throttle=_property(fdm, "fcs/throttle-cmd-norm[0]"),
        pitch_trim=_property(fdm, "fcs/pitch-trim-cmd-norm"),
    )
    return result


def _trim_runtime(
    fdm: object,
    config: AircraftFlightConfig,
    controls: _TrimControls,
) -> dict[str, float | str]:
    result: dict[str, float | str] = {
        "aircraft_model": config.aircraft.model_id,
        "trim_calibrated_airspeed_m_s": (
            _property(fdm, "velocities/vc-kts") * KNOT_TO_METER_PER_SECOND
        ),
        "trim_true_airspeed_m_s": (_property(fdm, "velocities/vt-fps") * FOOT_TO_METER),
        "trim_alpha_deg": _property(fdm, "aero/alpha-deg"),
        "trim_roll_deg": _property(fdm, "attitude/phi-deg"),
        "trim_pitch_deg": _property(fdm, "attitude/theta-deg"),
        "trim_heading_deg": _property(fdm, "attitude/psi-deg"),
        "trim_throttle_norm": controls.throttle,
        "trim_pitch_trim_norm": controls.pitch_trim,
    }
    return result


def _validate_trim(
    runtime: dict[str, float | str],
    config: AircraftFlightConfig,
) -> None:
    values = [value for value in runtime.values() if isinstance(value, (int, float))]
    if not all(math.isfinite(float(value)) for value in values):
        raise JSBSimExecutionError("JSBSim trim produced non-finite values")
    airspeed_error = abs(
        float(runtime["trim_calibrated_airspeed_m_s"])
        - config.initial_condition.calibrated_airspeed_m_s
    )
    if airspeed_error > 0.5:
        raise JSBSimExecutionError(
            "JSBSim trim changed calibrated airspeed by more than 0.5 m/s"
        )
    if not 0.0 <= float(runtime["trim_throttle_norm"]) <= 1.0:
        raise JSBSimExecutionError("JSBSim trim produced an invalid throttle command")


def _validate_scheduled_commands(
    config: AircraftFlightConfig,
    trim: _TrimControls,
) -> None:
    for segment in config.controls.segments:
        commands = {
            "aileron": trim.aileron + segment.aileron_delta_norm,
            "elevator": trim.elevator + segment.elevator_delta_norm,
            "rudder": trim.rudder + segment.rudder_delta_norm,
        }
        for name, command in commands.items():
            if not -1.0 <= command <= 1.0:
                raise JSBSimExecutionError(
                    f"control segment {segment.id!r} produces an invalid "
                    f"{name} command: {command}"
                )
        throttle = trim.throttle + segment.throttle_delta_norm
        if not 0.0 <= throttle <= 1.0:
            raise JSBSimExecutionError(
                f"control segment {segment.id!r} produces an invalid "
                f"throttle command: {throttle}"
            )


def _active_segment(
    config: AircraftFlightConfig,
    time_s: float,
) -> ControlSegment | None:
    result = None
    for segment in config.controls.segments:
        if segment.start_time_s <= time_s < segment.end_time_s:
            result = segment
            break
    return result


def _apply_controls(
    fdm: object,
    trim: _TrimControls,
    segment: ControlSegment | None,
) -> None:
    if segment is None:
        aileron_delta = 0.0
        elevator_delta = 0.0
        rudder_delta = 0.0
        throttle_delta = 0.0
    else:
        aileron_delta = segment.aileron_delta_norm
        elevator_delta = segment.elevator_delta_norm
        rudder_delta = segment.rudder_delta_norm
        throttle_delta = segment.throttle_delta_norm
    fdm["fcs/aileron-cmd-norm"] = trim.aileron + aileron_delta
    fdm["fcs/elevator-cmd-norm"] = trim.elevator + elevator_delta
    fdm["fcs/rudder-cmd-norm"] = trim.rudder + rudder_delta
    fdm["fcs/throttle-cmd-norm[0]"] = trim.throttle + throttle_delta


def _events(
    config: AircraftFlightConfig,
    trim_runtime: dict[str, float | str],
) -> tuple[SimulationEvent, ...]:
    events = [
        SimulationEvent(
            name="trim_complete",
            time_s=0.0,
            attributes={
                "aircraft_model": str(trim_runtime["aircraft_model"]),
                "calibrated_airspeed_m_s": float(
                    trim_runtime["trim_calibrated_airspeed_m_s"]
                ),
                "true_airspeed_m_s": float(trim_runtime["trim_true_airspeed_m_s"]),
                "alpha_deg": float(trim_runtime["trim_alpha_deg"]),
                "throttle_norm": float(trim_runtime["trim_throttle_norm"]),
                "pitch_trim_norm": float(trim_runtime["trim_pitch_trim_norm"]),
            },
        )
    ]
    for segment in config.controls.segments:
        attributes = {
            "segment_id": segment.id,
            "aileron_delta_norm": segment.aileron_delta_norm,
            "elevator_delta_norm": segment.elevator_delta_norm,
            "rudder_delta_norm": segment.rudder_delta_norm,
            "throttle_delta_norm": segment.throttle_delta_norm,
        }
        events.extend(
            (
                SimulationEvent(
                    name=f"control_{segment.id}_start",
                    time_s=segment.start_time_s,
                    attributes=attributes,
                ),
                SimulationEvent(
                    name=f"control_{segment.id}_end",
                    time_s=segment.end_time_s,
                    attributes=attributes,
                ),
            )
        )
    events.append(
        SimulationEvent(
            name="propagation_end",
            time_s=config.propagation.duration_s,
            attributes={"aircraft_model": config.aircraft.model_id},
        )
    )
    result = tuple(sorted(events, key=lambda event: event.time_s))
    return result


def _metrics(
    config: AircraftFlightConfig,
    arrays: dict[str, np.ndarray],
    trim_runtime: dict[str, float | str],
) -> tuple[ResultMetric, ...]:
    angular_rate = np.sqrt(
        arrays["roll_rate"] ** 2 + arrays["pitch_rate"] ** 2 + arrays["yaw_rate"] ** 2
    )
    load_factor = np.sqrt(
        arrays["load_factor_x"] ** 2
        + arrays["load_factor_y"] ** 2
        + arrays["load_factor_z"] ** 2
    )
    ground_distance = np.hypot(
        arrays["north_displacement"],
        arrays["east_displacement"],
    )
    result = (
        ResultMetric("propagation_duration", config.propagation.duration_s, "s"),
        ResultMetric("sample_count", float(len(arrays["roll"])), "1"),
        ResultMetric(
            "trim_calibrated_airspeed",
            float(trim_runtime["trim_calibrated_airspeed_m_s"]),
            "m/s",
        ),
        ResultMetric(
            "trim_true_airspeed",
            float(trim_runtime["trim_true_airspeed_m_s"]),
            "m/s",
        ),
        ResultMetric(
            "trim_angle_of_attack",
            float(trim_runtime["trim_alpha_deg"]),
            "deg",
        ),
        ResultMetric(
            "trim_throttle",
            float(trim_runtime["trim_throttle_norm"]),
            "1",
        ),
        ResultMetric(
            "trim_pitch_trim",
            float(trim_runtime["trim_pitch_trim_norm"]),
            "1",
        ),
        ResultMetric(
            "altitude_change",
            float(arrays["altitude_msl"][-1] - arrays["altitude_msl"][0]),
            "m",
        ),
        ResultMetric(
            "calibrated_airspeed_change",
            float(arrays["calibrated_airspeed"][-1] - arrays["calibrated_airspeed"][0]),
            "m/s",
        ),
        ResultMetric(
            "heading_change",
            float(arrays["heading"][-1] - arrays["heading"][0]),
            "deg",
        ),
        ResultMetric("maximum_roll", float(np.max(np.abs(arrays["roll"]))), "deg"),
        ResultMetric(
            "maximum_pitch",
            float(np.max(np.abs(arrays["pitch"]))),
            "deg",
        ),
        ResultMetric(
            "maximum_angle_of_attack",
            float(np.max(np.abs(arrays["angle_of_attack"]))),
            "deg",
        ),
        ResultMetric(
            "maximum_sideslip",
            float(np.max(np.abs(arrays["sideslip"]))),
            "deg",
        ),
        ResultMetric(
            "maximum_body_angular_rate",
            float(np.max(angular_rate)),
            "rad/s",
        ),
        ResultMetric(
            "maximum_load_factor_magnitude",
            float(np.max(load_factor)),
            "1",
        ),
        ResultMetric(
            "final_ground_distance",
            float(ground_distance[-1]),
            "m",
        ),
    )
    return result


def _property(fdm: object, name: str) -> float:
    try:
        value = float(fdm[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise JSBSimExecutionError(
            f"JSBSim property is unavailable or non-numeric: {name}"
        ) from exc
    if not math.isfinite(value):
        raise JSBSimExecutionError(
            f"JSBSim property contains a non-finite value: {name}"
        )
    return value
