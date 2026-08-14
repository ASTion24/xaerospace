from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import numpy as np

from .aircraft_backend import JSBSimBackend
from .attitude_backend import BasiliskBackend
from .config import POINT_MASS_DYNAMICS, ParachuteConfig, ScenarioConfig
from .model_manifest import build_model_manifest
from .models import FlightEvent, FlightSeries, FlightSummary, ModelManifest
from .orbit_backend import TudatPyBackend
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
from .registry import BackendRegistry
from .request_io import AEROSPACE_CONTRACT_SCHEMA, request_from_scenario

ROCKETPY_TASK_KINDS = (
    "single_stage_point_mass_3dof",
    "single_stage_point_mass_3dof_recovery",
    "single_stage_rigid_body_6dof",
    "single_stage_rigid_body_6dof_recovery",
)


class BackendUnavailableError(RuntimeError):
    """Raised when the selected open-source backend is not installed."""


class SimulationExecutionError(RuntimeError):
    """Raised when a requested flight cannot be completed faithfully."""


class RocketPyBackend:
    def __init__(self) -> None:
        try:
            backend_version = version("rocketpy")
        except PackageNotFoundError:
            backend_version = "source-checkout"
        self._capabilities = BackendCapabilities(
            backend_id="rocketpy",
            backend_name="RocketPy",
            backend_version=backend_version,
            supported_task_kinds=ROCKETPY_TASK_KINDS,
            supported_contract_schemas=(AEROSPACE_CONTRACT_SCHEMA,),
            supported_family_ids=("rocket_flight",),
            supported_component_ids=(
                "rocket.fidelity.point_mass_3dof",
                "rocket.fidelity.rigid_body_6dof",
                "rocket.recovery.none",
                "rocket.recovery.parachute",
                "rocket.environment.standard_atmosphere",
                "rocket.propulsion.thrust_curve",
            ),
        )

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def run(self, request: SimulationRequest) -> UnifiedSimulationResult:
        if not isinstance(request.contract, ScenarioConfig):
            raise SimulationExecutionError(
                "RocketPy requires a ScenarioConfig contract"
            )
        if request.task_kind != request.contract.dynamics:
            raise SimulationExecutionError(
                "request task_kind does not match the scenario dynamics contract"
            )
        return _simulate_rocketpy(request.contract, request, self.capabilities)


def create_default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(BasiliskBackend())
    registry.register(JSBSimBackend())
    registry.register(RocketPyBackend())
    registry.register(TudatPyBackend())
    return registry


def run_request(
    request: SimulationRequest,
    *,
    registry: BackendRegistry | None = None,
) -> UnifiedSimulationResult:
    active_registry = registry or create_default_registry()
    return active_registry.run(request)


def simulate(config: ScenarioConfig) -> UnifiedSimulationResult:
    return run_request(request_from_scenario(config))


def _simulate_rocketpy(
    config: ScenarioConfig,
    request: SimulationRequest,
    capabilities: BackendCapabilities,
) -> UnifiedSimulationResult:
    try:
        from rocketpy import (
            Environment,
            Flight,
            GenericMotor,
            PointMassMotor,
            PointMassRocket,
            Rocket,
        )
    except ImportError as exc:
        raise BackendUnavailableError(
            "RocketPy is required. Install this project with its declared dependencies."
        ) from exc

    environment = Environment(
        latitude=config.environment.latitude_deg,
        longitude=config.environment.longitude_deg,
        elevation=config.environment.elevation_m,
    )
    environment.set_atmospheric_model(type=config.environment.atmospheric_model)

    thrust_curve = np.asarray(config.motor.thrust_curve, dtype=float)
    if config.dynamics in POINT_MASS_DYNAMICS:
        motor = PointMassMotor(
            thrust_source=thrust_curve,
            dry_mass=config.motor.dry_mass_kg,
            propellant_initial_mass=config.motor.propellant_initial_mass_kg,
            burn_time=config.motor.burn_time_s,
        )
        rocket = PointMassRocket(
            radius=config.vehicle.radius_m,
            mass=config.vehicle.dry_mass_without_motor_kg,
            center_of_mass_without_motor=(
                config.vehicle.center_of_mass_without_motor_m
            ),
            power_off_drag=config.vehicle.drag_coefficient_power_off,
            power_on_drag=config.vehicle.drag_coefficient_power_on,
            weathercock_coeff=config.vehicle.weathercock_coefficient,
        )
        rocket.add_motor(motor, position=0)
        simulation_mode = "3 DOF"
    else:
        rigid_body = config.rigid_body
        if rigid_body is None:
            raise SimulationExecutionError(
                "rigid-body configuration is required for 6DOF"
            )
        motor_config = rigid_body.motor
        motor = GenericMotor(
            thrust_source=thrust_curve,
            burn_time=config.motor.burn_time_s,
            chamber_radius=motor_config.chamber_radius_m,
            chamber_height=motor_config.chamber_height_m,
            chamber_position=motor_config.chamber_position_m,
            propellant_initial_mass=config.motor.propellant_initial_mass_kg,
            nozzle_radius=motor_config.nozzle_radius_m,
            dry_mass=config.motor.dry_mass_kg,
            center_of_dry_mass_position=(motor_config.center_of_dry_mass_position_m),
            dry_inertia=motor_config.dry_inertia_kg_m2,
            nozzle_position=motor_config.nozzle_position_m,
            interpolation_method="linear",
            coordinate_system_orientation=(motor_config.coordinate_system_orientation),
        )
        rocket = Rocket(
            radius=config.vehicle.radius_m,
            mass=config.vehicle.dry_mass_without_motor_kg,
            inertia=rigid_body.vehicle_dry_inertia_kg_m2,
            power_off_drag=config.vehicle.drag_coefficient_power_off,
            power_on_drag=config.vehicle.drag_coefficient_power_on,
            center_of_mass_without_motor=(
                config.vehicle.center_of_mass_without_motor_m
            ),
            coordinate_system_orientation=(rigid_body.coordinate_system_orientation),
        )
        rocket.add_motor(motor, position=rigid_body.motor_position_m)
        rocket.set_rail_buttons(
            upper_button_position=rigid_body.rail_buttons.upper_position_m,
            lower_button_position=rigid_body.rail_buttons.lower_position_m,
            angular_position=rigid_body.rail_buttons.angular_position_deg,
        )
        rocket.add_nose(
            length=rigid_body.nose.length_m,
            kind=rigid_body.nose.kind,
            position=rigid_body.nose.position_m,
        )
        rocket.add_trapezoidal_fins(
            n=rigid_body.fins.count,
            root_chord=rigid_body.fins.root_chord_m,
            tip_chord=rigid_body.fins.tip_chord_m,
            span=rigid_body.fins.span_m,
            position=rigid_body.fins.position_m,
            cant_angle=rigid_body.fins.cant_angle_deg,
        )
        if rigid_body.tail is not None:
            rocket.add_tail(
                top_radius=rigid_body.tail.top_radius_m,
                bottom_radius=rigid_body.tail.bottom_radius_m,
                length=rigid_body.tail.length_m,
                position=rigid_body.tail.position_m,
            )
        simulation_mode = "6 DOF"

    if config.recovery is not None:
        for parachute in config.recovery.parachutes:
            rocket.add_parachute(
                name=parachute.id,
                cd_s=parachute.cd_s_m2,
                trigger=_rocketpy_parachute_trigger(parachute),
                sampling_rate=parachute.sampling_rate_hz,
                lag=parachute.lag_s,
                noise=(0, 0, 0),
            )

    flight = Flight(
        rocket=rocket,
        environment=environment,
        rail_length=config.launch.rail_length_m,
        inclination=config.launch.inclination_deg,
        heading=config.launch.heading_deg,
        max_time=config.launch.max_time_s,
        max_time_step=config.launch.max_time_step_s,
        simulation_mode=simulation_mode,
        verbose=False,
    )
    if flight.simulation_mode != simulation_mode:
        raise SimulationExecutionError(
            f"RocketPy selected {flight.simulation_mode}, expected {simulation_mode}"
        )
    _assert_complete_flight(flight, config)
    _assert_recovery_complete(flight, config)

    times = _sample_times(
        float(flight.t_initial),
        float(flight.t_final),
        config.output.sample_interval_s,
    )
    x = _values(flight.x, times)
    y = _values(flight.y, times)
    altitude = _values(flight.altitude, times)
    horizontal_range = np.hypot(x, y)
    rail_time = float(flight.out_of_rail_time)
    burnout_time = float(motor.burn_out_time)
    apogee_time = float(flight.apogee_time)
    omega1 = _values(flight.w1, times)
    omega2 = _values(flight.w2, times)
    omega3 = _values(flight.w3, times)
    angular_rate = np.sqrt(omega1**2 + omega2**2 + omega3**2)
    angle_of_attack = _values(flight.angle_of_attack, times)

    series = FlightSeries(
        time_s=times,
        x_east_m=x,
        y_north_m=y,
        altitude_agl_m=altitude,
        horizontal_range_m=horizontal_range,
        vx_m_s=_values(flight.vx, times),
        vy_m_s=_values(flight.vy, times),
        vz_m_s=_values(flight.vz, times),
        speed_m_s=_values(flight.speed, times),
        acceleration_m_s2=_values(flight.acceleration, times),
        mach=_values(flight.mach_number, times),
        quaternion_e0=_values(flight.e0, times),
        quaternion_e1=_values(flight.e1, times),
        quaternion_e2=_values(flight.e2, times),
        quaternion_e3=_values(flight.e3, times),
        omega1_rad_s=omega1,
        omega2_rad_s=omega2,
        omega3_rad_s=omega3,
        angular_rate_rad_s=angular_rate,
        attitude_angle_deg=_values(flight.attitude_angle, times),
        angle_of_attack_deg=angle_of_attack,
        phase=tuple(
            _phase_at(time_s, rail_time, burnout_time, apogee_time) for time_s in times
        ),
    )

    events = tuple(
        _event(flight, name, event_time)
        for name, event_time in (
            ("rail_departure", rail_time),
            ("burnout", burnout_time),
            ("apogee", apogee_time),
            ("impact", float(flight.t_final)),
        )
    )
    recovery_events = _parachute_events(flight)
    summary = FlightSummary(
        lift_off_mass_kg=(
            config.vehicle.dry_mass_without_motor_kg
            + config.motor.dry_mass_kg
            + config.motor.propellant_initial_mass_kg
        ),
        rail_departure_time_s=rail_time,
        rail_departure_speed_m_s=float(flight.out_of_rail_velocity),
        burnout_time_s=burnout_time,
        apogee_time_s=apogee_time,
        apogee_agl_m=float(flight.altitude(apogee_time)),
        max_speed_m_s=float(flight.max_speed),
        max_mach=float(flight.max_mach_number),
        max_acceleration_m_s2=float(flight.max_acceleration),
        max_dynamic_pressure_pa=float(flight.max_dynamic_pressure),
        max_angle_of_attack_deg=_finite_max_abs(angle_of_attack),
        max_angular_rate_rad_s=_finite_max_abs(angular_rate),
        flight_time_s=float(flight.t_final),
        impact_speed_m_s=float(flight.speed(flight.t_final)),
        impact_horizontal_range_m=float(np.hypot(flight.x_impact, flight.y_impact)),
    )
    model_manifest = build_model_manifest(
        config,
        flight=flight,
        rocket=rocket,
        motor=motor,
        environment=environment,
        backend_version=capabilities.backend_version,
    )
    return _normalize_rocketpy_result(
        request=request,
        capabilities=capabilities,
        series=series,
        events=events,
        recovery_events=recovery_events,
        summary=summary,
        model_manifest=model_manifest,
    )


def _normalize_rocketpy_result(
    *,
    request: SimulationRequest,
    capabilities: BackendCapabilities,
    series: FlightSeries,
    events: tuple[FlightEvent, ...],
    recovery_events: tuple[SimulationEvent, ...],
    summary: FlightSummary,
    model_manifest: ModelManifest,
) -> UnifiedSimulationResult:
    channels = (
        _channel("x_east", "position", "m", "local_enu", series.x_east_m),
        _channel("y_north", "position", "m", "local_enu", series.y_north_m),
        _channel(
            "altitude_agl",
            "altitude",
            "m",
            "above_launch_site",
            series.altitude_agl_m,
        ),
        _channel(
            "horizontal_range",
            "distance",
            "m",
            "launch_site",
            series.horizontal_range_m,
        ),
        _channel("vx", "velocity", "m/s", "local_enu", series.vx_m_s),
        _channel("vy", "velocity", "m/s", "local_enu", series.vy_m_s),
        _channel("vz", "velocity", "m/s", "local_enu", series.vz_m_s),
        _channel("speed", "speed", "m/s", "local_enu", series.speed_m_s),
        _channel(
            "acceleration",
            "acceleration_magnitude",
            "m/s^2",
            "local_enu",
            series.acceleration_m_s2,
        ),
        _channel("mach", "mach_number", "1", "air_relative", series.mach),
        _channel(
            "quaternion_e0",
            "attitude_quaternion",
            "1",
            "body_to_local_enu",
            series.quaternion_e0,
        ),
        _channel(
            "quaternion_e1",
            "attitude_quaternion",
            "1",
            "body_to_local_enu",
            series.quaternion_e1,
        ),
        _channel(
            "quaternion_e2",
            "attitude_quaternion",
            "1",
            "body_to_local_enu",
            series.quaternion_e2,
        ),
        _channel(
            "quaternion_e3",
            "attitude_quaternion",
            "1",
            "body_to_local_enu",
            series.quaternion_e3,
        ),
        _channel(
            "omega1",
            "angular_velocity",
            "rad/s",
            "body",
            series.omega1_rad_s,
        ),
        _channel(
            "omega2",
            "angular_velocity",
            "rad/s",
            "body",
            series.omega2_rad_s,
        ),
        _channel(
            "omega3",
            "angular_velocity",
            "rad/s",
            "body",
            series.omega3_rad_s,
        ),
        _channel(
            "angular_rate",
            "angular_speed",
            "rad/s",
            "body",
            series.angular_rate_rad_s,
        ),
        _channel(
            "attitude_angle",
            "attitude_angle",
            "deg",
            "local_enu",
            series.attitude_angle_deg,
        ),
        _channel(
            "angle_of_attack",
            "angle_of_attack",
            "deg",
            "air_relative",
            series.angle_of_attack_deg,
        ),
    )
    core_events = tuple(
        SimulationEvent(
            name=event.name,
            time_s=event.time_s,
            attributes={
                "altitude_agl_m": event.altitude_agl_m,
                "horizontal_range_m": event.horizontal_range_m,
            },
        )
        for event in events
    )
    normalized_events = tuple(
        sorted((*core_events, *recovery_events), key=lambda event: event.time_s)
    )
    metrics = [
        _metric("lift_off_mass", summary.lift_off_mass_kg, "kg"),
        _metric("rail_departure_time", summary.rail_departure_time_s, "s"),
        _metric("rail_departure_speed", summary.rail_departure_speed_m_s, "m/s"),
        _metric("burnout_time", summary.burnout_time_s, "s"),
        _metric("apogee_time", summary.apogee_time_s, "s"),
        _metric("apogee_agl", summary.apogee_agl_m, "m"),
        _metric("max_speed", summary.max_speed_m_s, "m/s"),
        _metric("max_mach", summary.max_mach, "1"),
        _metric("max_acceleration", summary.max_acceleration_m_s2, "m/s^2"),
        _metric(
            "max_dynamic_pressure",
            summary.max_dynamic_pressure_pa,
            "Pa",
        ),
        _metric(
            "max_angle_of_attack",
            summary.max_angle_of_attack_deg,
            "deg",
        ),
        _metric(
            "max_angular_rate",
            summary.max_angular_rate_rad_s,
            "rad/s",
        ),
        _metric("flight_time", summary.flight_time_s, "s"),
        _metric("impact_speed", summary.impact_speed_m_s, "m/s"),
        _metric(
            "impact_horizontal_range",
            summary.impact_horizontal_range_m,
            "m",
        ),
    ]
    deployment_events = tuple(
        event for event in recovery_events if event.name.endswith("_deployment")
    )
    if deployment_events:
        first_deployment_time = min(event.time_s for event in deployment_events)
        metrics.extend(
            (
                _metric(
                    "recovery_deployment_count",
                    float(len(deployment_events)),
                    "1",
                ),
                _metric(
                    "first_recovery_deployment_time",
                    first_deployment_time,
                    "s",
                ),
                _metric(
                    "recovery_descent_duration",
                    summary.flight_time_s - first_deployment_time,
                    "s",
                ),
                _metric(
                    "impact_vertical_speed",
                    abs(float(series.vz_m_s[-1])),
                    "m/s",
                ),
            )
        )
    return UnifiedSimulationResult(
        protocol_version=PROTOCOL_VERSION,
        request=request,
        backend=capabilities,
        time_s=series.time_s,
        channels=channels,
        events=normalized_events,
        metrics=tuple(metrics),
        model_manifest=model_manifest,
        diagnostics=(
            Diagnostic(
                level="info",
                code="backend_contract_executed",
                message=(f"RocketPy executed {request.task_kind} without fallback."),
            ),
        ),
    )


def _channel(
    name: str,
    quantity: str,
    unit: str,
    frame: str,
    values: np.ndarray,
) -> ResultChannel:
    return ResultChannel(name, quantity, unit, frame, values)


def _metric(name: str, value: float, unit: str) -> ResultMetric:
    return ResultMetric(name, value, unit)


def _rocketpy_parachute_trigger(parachute: ParachuteConfig) -> str | float:
    if parachute.trigger.kind == "apogee":
        return "apogee"
    if parachute.trigger.altitude_agl_m is None:
        raise SimulationExecutionError(
            f"parachute {parachute.id!r} is missing its trigger altitude"
        )
    return parachute.trigger.altitude_agl_m


def _parachute_events(flight: object) -> tuple[SimulationEvent, ...]:
    events: list[SimulationEvent] = []
    for trigger_time, parachute in flight.parachute_events:
        trigger_time = float(trigger_time)
        deployment_time = trigger_time + float(parachute.lag)
        if deployment_time > float(flight.t_final):
            raise SimulationExecutionError(
                f"parachute {parachute.name!r} did not deploy before impact"
            )
        common = {
            "parachute_id": str(parachute.name),
            "cd_s_m2": float(parachute.cd_s),
            "lag_s": float(parachute.lag),
        }
        events.append(
            SimulationEvent(
                name=f"parachute_{parachute.name}_trigger",
                time_s=trigger_time,
                attributes={
                    **common,
                    **_flight_attributes_at(flight, trigger_time),
                },
            )
        )
        events.append(
            SimulationEvent(
                name=f"parachute_{parachute.name}_deployment",
                time_s=deployment_time,
                attributes={
                    **common,
                    **_flight_attributes_at(flight, deployment_time),
                },
            )
        )
    return tuple(events)


def _flight_attributes_at(flight: object, time_s: float) -> dict[str, float]:
    x = float(flight.x(time_s))
    y = float(flight.y(time_s))
    return {
        "altitude_agl_m": float(flight.altitude(time_s)),
        "horizontal_range_m": float(np.hypot(x, y)),
    }


def _assert_complete_flight(flight: object, config: ScenarioConfig) -> None:
    if float(flight.out_of_rail_time) <= 0:
        raise SimulationExecutionError(
            "the rocket did not depart the rail; check thrust-to-weight ratio"
        )
    if float(flight.apogee_time) <= float(flight.out_of_rail_time):
        raise SimulationExecutionError("RocketPy did not detect a valid apogee")
    impact_state = np.asarray(flight.impact_state)
    if impact_state.size <= 1:
        raise SimulationExecutionError(
            "the rocket did not impact before launch.max_time_s="
            f"{config.launch.max_time_s}; increase the simulation horizon"
        )


def _assert_recovery_complete(flight: object, config: ScenarioConfig) -> None:
    if config.recovery is None:
        if flight.parachute_events:
            raise SimulationExecutionError(
                "RocketPy reported parachute events for a non-recovery contract"
            )
        return
    expected_ids = {parachute.id for parachute in config.recovery.parachutes}
    triggered_ids = {str(parachute.name) for _, parachute in flight.parachute_events}
    if triggered_ids != expected_ids:
        missing = ", ".join(sorted(expected_ids - triggered_ids)) or "none"
        raise SimulationExecutionError(
            f"not all configured parachutes triggered; missing: {missing}"
        )


def _sample_times(start_s: float, end_s: float, interval_s: float) -> np.ndarray:
    times = np.arange(start_s, end_s, interval_s, dtype=float)
    if times.size == 0 or not np.isclose(times[-1], end_s):
        times = np.append(times, end_s)
    return times


def _values(function: object, times: np.ndarray) -> np.ndarray:
    return np.asarray(function(times), dtype=float)


def _finite_max_abs(values: np.ndarray) -> float:
    finite = np.asarray(values)[np.isfinite(values)]
    if finite.size == 0:
        raise SimulationExecutionError(
            "derived flight series contains no finite values"
        )
    return float(np.max(np.abs(finite)))


def _event(flight: object, name: str, time_s: float) -> FlightEvent:
    x = float(flight.x(time_s))
    y = float(flight.y(time_s))
    return FlightEvent(
        name=name,
        time_s=time_s,
        altitude_agl_m=float(flight.altitude(time_s)),
        horizontal_range_m=float(np.hypot(x, y)),
    )


def _phase_at(
    time_s: float,
    rail_departure_time_s: float,
    burnout_time_s: float,
    apogee_time_s: float,
) -> str:
    if time_s < rail_departure_time_s:
        return "rail"
    if time_s < burnout_time_s:
        return "powered_ascent"
    if time_s < apogee_time_s:
        return "coast_ascent"
    return "descent"
