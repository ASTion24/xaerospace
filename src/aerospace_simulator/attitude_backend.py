from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import numpy as np

from .attitude_config import (
    ATTITUDE_CONTRACT_SCHEMA,
    ATTITUDE_TASK_KINDS,
    SpacecraftAttitudeConfig,
)
from .attitude_manifest import build_attitude_model_manifest
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

BASILISK_VERSION = "2.11.0"
_WHEEL_COUNT = 3


class BasiliskBackendUnavailableError(RuntimeError):
    """Raised when the version-pinned Basilisk runtime is unavailable."""


class BasiliskExecutionError(RuntimeError):
    """Raised when Basilisk cannot execute the requested attitude contract."""


class BasiliskBackend:
    def __init__(self) -> None:
        try:
            backend_version = version("bsk")
        except PackageNotFoundError:
            backend_version = "unavailable"
        self._capabilities = BackendCapabilities(
            backend_id="basilisk",
            backend_name="Basilisk",
            backend_version=backend_version,
            supported_task_kinds=ATTITUDE_TASK_KINDS,
            supported_contract_schemas=(ATTITUDE_CONTRACT_SCHEMA,),
            supported_family_ids=("spacecraft_gnc",),
            supported_component_ids=(
                "spacecraft.navigation.perfect",
                "spacecraft.guidance.inertial_fixed_mrp",
                "spacecraft.control.mrp_feedback_pd",
                "spacecraft.control.rate_damping",
                "spacecraft.control.none",
                "spacecraft.actuator.reaction_wheels_hr16",
            ),
        )

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def run(self, request: SimulationRequest) -> UnifiedSimulationResult:
        if not isinstance(request.contract, SpacecraftAttitudeConfig):
            raise BasiliskExecutionError(
                "Basilisk requires a SpacecraftAttitudeConfig contract"
            )
        if request.task_kind != request.contract.dynamics:
            raise BasiliskExecutionError(
                "request task_kind does not match the attitude dynamics contract"
            )
        if self.capabilities.backend_version != BASILISK_VERSION:
            raise BasiliskBackendUnavailableError(
                f"Basilisk {BASILISK_VERSION} is required; found "
                f"{self.capabilities.backend_version}"
            )
        return _simulate_basilisk(
            config=request.contract,
            request=request,
            capabilities=self.capabilities,
        )


def _simulate_basilisk(
    *,
    config: SpacecraftAttitudeConfig,
    request: SimulationRequest,
    capabilities: BackendCapabilities,
) -> UnifiedSimulationResult:
    try:
        from Basilisk.architecture import messaging
        from Basilisk.fswAlgorithms import (
            attTrackingError,
            inertial3D,
            mrpFeedback,
            rwMotorTorque,
        )
        from Basilisk.simulation import (
            reactionWheelStateEffector,
            simpleNav,
            spacecraft,
            svIntegrators,
        )
        from Basilisk.utilities import (
            SimulationBaseClass,
            macros,
            simHelpers,
            simIncludeRW,
        )
    except (ImportError, OSError) as exc:
        raise BasiliskBackendUnavailableError(
            "Basilisk is required. Install bsk==2.11.0 through the declared "
            "project dependencies."
        ) from exc

    task_name = "attitudeTask"
    simulation = SimulationBaseClass.SimBaseClass()
    process = simulation.CreateNewProcess("attitudeProcess")
    task_step_ns = macros.sec2nano(config.propagation.step_size_s)
    process.addTask(simulation.CreateNewTask(task_name, task_step_ns))

    spacecraft_object = spacecraft.Spacecraft()
    spacecraft_object.ModelTag = "spacecraft"
    principal_inertia = config.spacecraft.principal_inertia_kg_m2
    inertia_matrix = [
        principal_inertia[0],
        0.0,
        0.0,
        0.0,
        principal_inertia[1],
        0.0,
        0.0,
        0.0,
        principal_inertia[2],
    ]
    spacecraft_object.hub.mHub = config.spacecraft.mass_kg
    spacecraft_object.hub.r_BcB_B = [[0.0], [0.0], [0.0]]
    spacecraft_object.hub.IHubPntBc_B = simHelpers.np2EigenMatrix3d(inertia_matrix)
    spacecraft_object.hub.r_CN_NInit = [[0.0], [0.0], [0.0]]
    spacecraft_object.hub.v_CN_NInit = [[0.0], [0.0], [0.0]]
    spacecraft_object.hub.sigma_BNInit = [
        [component] for component in config.initial_state.mrp_sigma_bn
    ]
    spacecraft_object.hub.omega_BN_BInit = [
        [component] for component in config.initial_state.angular_velocity_bn_body_rad_s
    ]
    spacecraft_object.setIntegrator(svIntegrators.svIntegratorRK4(spacecraft_object))
    simulation.AddModelToTask(task_name, spacecraft_object, 1)

    wheel_factory = simIncludeRW.rwFactory()
    for spin_axis, initial_speed in zip(
        config.reaction_wheels.spin_axes_body,
        config.reaction_wheels.initial_speed_rpm,
        strict=True,
    ):
        wheel_factory.create(
            config.reaction_wheels.model_id,
            list(spin_axis),
            maxMomentum=float(config.reaction_wheels.max_momentum_n_m_s),
            Omega=float(initial_speed),
            Omega_max=float(config.reaction_wheels.max_speed_rpm),
            u_max=float(config.reaction_wheels.max_motor_torque_n_m),
            RWModel=messaging.BalancedWheels,
            useRWfriction=False,
            useMaxTorque=True,
        )
    if wheel_factory.getNumOfDevices() != _WHEEL_COUNT:
        raise BasiliskExecutionError(
            "Basilisk reaction-wheel factory did not create exactly three wheels"
        )
    wheel_runtime = tuple(
        {
            "spin_inertia_kg_m2": float(wheel.Js),
            "mass_kg": float(wheel.mass),
        }
        for wheel in wheel_factory.rwList.values()
    )

    wheel_effector = reactionWheelStateEffector.ReactionWheelStateEffector()
    wheel_effector.ModelTag = "reactionWheelArray"
    wheel_factory.addToSpacecraft(
        wheel_effector.ModelTag,
        wheel_effector,
        spacecraft_object,
    )
    simulation.AddModelToTask(task_name, wheel_effector, 2)

    navigation = simpleNav.SimpleNav()
    navigation.ModelTag = "simpleNav"
    simulation.AddModelToTask(task_name, navigation)

    guidance = inertial3D.inertial3D()
    guidance.ModelTag = "inertial3D"
    guidance.sigma_R0N = list(config.gnc.reference_mrp_sigma_rn)
    simulation.AddModelToTask(task_name, guidance)

    tracking_error = attTrackingError.attTrackingError()
    tracking_error.ModelTag = "attTrackingError"
    simulation.AddModelToTask(task_name, tracking_error)

    navigation.scStateInMsg.subscribeTo(spacecraft_object.scStateOutMsg)
    tracking_error.attNavInMsg.subscribeTo(navigation.attOutMsg)
    tracking_error.attRefInMsg.subscribeTo(guidance.attRefOutMsg)

    controller = None
    allocator = None
    if config.gnc.enabled:
        controller = mrpFeedback.mrpFeedback()
        controller.ModelTag = config.gnc.controller
        controller.K = config.gnc.mrp_gain_n_m
        controller.P = config.gnc.rate_gain_n_m_s
        controller.Ki = -1.0
        simulation.AddModelToTask(task_name, controller)

        allocator = rwMotorTorque.rwMotorTorque()
        allocator.ModelTag = "rwMotorTorque"
        # Basilisk 2.11.0's SWIG fixed-array setter requires integer literals
        # here; a float list shifts the final identity-matrix entry.
        control_axes = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        allocator.controlAxes_B = control_axes
        if list(allocator.controlAxes_B) != [float(value) for value in control_axes]:
            raise BasiliskExecutionError(
                "Basilisk corrupted the reaction-wheel control-axis matrix"
            )
        simulation.AddModelToTask(task_name, allocator)

        vehicle_configuration = messaging.VehicleConfigMsg().write(
            messaging.VehicleConfigMsgPayload(ISCPntB_B=inertia_matrix)
        )
        wheel_configuration = wheel_factory.getConfigMessage()
        controller.guidInMsg.subscribeTo(tracking_error.attGuidOutMsg)
        controller.vehConfigInMsg.subscribeTo(vehicle_configuration)
        controller.rwParamsInMsg.subscribeTo(wheel_configuration)
        controller.rwSpeedsInMsg.subscribeTo(wheel_effector.rwSpeedOutMsg)
        allocator.rwParamsInMsg.subscribeTo(wheel_configuration)
        allocator.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
        wheel_effector.rwMotorCmdInMsg.subscribeTo(allocator.rwMotorTorqueOutMsg)

    sample_ns = macros.sec2nano(config.propagation.output_interval_s)
    attitude_log = navigation.attOutMsg.recorder(sample_ns)
    translation_log = navigation.transOutMsg.recorder(sample_ns)
    error_log = tracking_error.attGuidOutMsg.recorder(sample_ns)
    wheel_speed_log = wheel_effector.rwSpeedOutMsg.recorder(sample_ns)
    simulation.AddModelToTask(task_name, attitude_log)
    simulation.AddModelToTask(task_name, translation_log)
    simulation.AddModelToTask(task_name, error_log)
    simulation.AddModelToTask(task_name, wheel_speed_log)

    wheel_logs = []
    for output_message in wheel_effector.rwOutMsgs:
        wheel_log = output_message.recorder(sample_ns)
        wheel_logs.append(wheel_log)
        simulation.AddModelToTask(task_name, wheel_log)

    body_torque_log = None
    wheel_command_log = None
    if controller is not None and allocator is not None:
        body_torque_log = controller.cmdTorqueOutMsg.recorder(sample_ns)
        wheel_command_log = allocator.rwMotorTorqueOutMsg.recorder(sample_ns)
        simulation.AddModelToTask(task_name, body_torque_log)
        simulation.AddModelToTask(task_name, wheel_command_log)

    simulation.InitializeSimulation()
    simulation.ConfigureStopTime(macros.sec2nano(config.propagation.duration_s))
    simulation.ExecuteSimulation()

    time_s = np.asarray(error_log.times(), dtype=float) * macros.NANO2SEC
    expected_samples = (
        round(config.propagation.duration_s / config.propagation.output_interval_s) + 1
    )
    expected_time = (
        np.arange(expected_samples, dtype=float) * config.propagation.output_interval_s
    )
    if len(time_s) != expected_samples or not np.allclose(
        time_s,
        expected_time,
        rtol=0.0,
        atol=1e-9,
    ):
        raise BasiliskExecutionError(
            "Basilisk returned an unexpected attitude output time axis"
        )

    recorder_times = (
        attitude_log.times(),
        translation_log.times(),
        wheel_speed_log.times(),
        *(wheel_log.times() for wheel_log in wheel_logs),
    )
    if any(
        not np.array_equal(np.asarray(times), np.asarray(error_log.times()))
        for times in recorder_times
    ):
        raise BasiliskExecutionError(
            "Basilisk recorders did not share a common output time axis"
        )

    position = _matrix(translation_log.r_BN_N, "position", columns=3)
    velocity = _matrix(translation_log.v_BN_N, "velocity", columns=3)
    attitude_mrp = _matrix(attitude_log.sigma_BN, "attitude MRP", columns=3)
    body_rate = _matrix(
        attitude_log.omega_BN_B,
        "body angular rate",
        columns=3,
    )
    attitude_error = _matrix(
        error_log.sigma_BR,
        "attitude error MRP",
        columns=3,
    )
    rate_error = _matrix(
        error_log.omega_BR_B,
        "angular-rate error",
        columns=3,
    )
    wheel_speed = _matrix(
        wheel_speed_log.wheelSpeeds,
        "reaction-wheel speed",
        minimum_columns=_WHEEL_COUNT,
    )[:, :_WHEEL_COUNT]
    applied_wheel_torque = np.column_stack(
        [
            _vector(wheel_log.u_current, f"reaction-wheel {index} torque")
            for index, wheel_log in enumerate(wheel_logs, start=1)
        ]
    )
    if body_torque_log is None or wheel_command_log is None:
        requested_body_torque = np.zeros((expected_samples, 3), dtype=float)
        requested_wheel_torque = np.zeros((expected_samples, 3), dtype=float)
    else:
        requested_body_torque = _matrix(
            body_torque_log.torqueRequestBody,
            "requested body control torque",
            columns=3,
        )
        requested_wheel_torque = _matrix(
            wheel_command_log.motorTorque,
            "requested reaction-wheel torque",
            minimum_columns=_WHEEL_COUNT,
        )[:, :_WHEEL_COUNT]

    arrays = (
        position,
        velocity,
        attitude_mrp,
        body_rate,
        attitude_error,
        rate_error,
        wheel_speed,
        applied_wheel_torque,
        requested_body_torque,
        requested_wheel_torque,
    )
    if any(len(values) != expected_samples for values in arrays):
        raise BasiliskExecutionError(
            "Basilisk output channels do not match the shared time axis"
        )
    if any(not np.all(np.isfinite(values)) for values in arrays):
        raise BasiliskExecutionError(
            "Basilisk attitude propagation produced non-finite values"
        )

    torque_limit = config.reaction_wheels.max_motor_torque_n_m
    if np.max(np.abs(applied_wheel_torque)) > torque_limit + 1e-12:
        raise BasiliskExecutionError(
            "Basilisk exceeded the configured reaction-wheel torque limit"
        )
    initial_tracking_error = np.linalg.norm(attitude_error[0]) + np.linalg.norm(
        rate_error[0]
    )
    if (
        config.gnc.enabled
        and initial_tracking_error > 1e-12
        and np.max(np.abs(applied_wheel_torque)) <= 1e-15
    ):
        raise BasiliskExecutionError(
            "Basilisk GNC was enabled but produced no reaction-wheel torque"
        )
    speed_limit_rad_s = config.reaction_wheels.max_speed_rpm * 2.0 * np.pi / 60.0
    if np.max(np.abs(wheel_speed)) > speed_limit_rad_s + 1e-9:
        raise BasiliskExecutionError(
            "Basilisk exceeded the configured reaction-wheel speed limit"
        )

    wheel_inertia = np.asarray(
        [runtime["spin_inertia_kg_m2"] for runtime in wheel_runtime],
        dtype=float,
    )
    wheel_momentum = wheel_speed * wheel_inertia
    channels = _channels(
        position=position,
        velocity=velocity,
        attitude_mrp=attitude_mrp,
        body_rate=body_rate,
        attitude_error=attitude_error,
        rate_error=rate_error,
        requested_body_torque=requested_body_torque,
        requested_wheel_torque=requested_wheel_torque,
        applied_wheel_torque=applied_wheel_torque,
        wheel_speed=wheel_speed,
        wheel_momentum=wheel_momentum,
    )
    events = _events(config)
    metrics = _metrics(
        config=config,
        attitude_error=attitude_error,
        rate_error=rate_error,
        requested_body_torque=requested_body_torque,
        requested_wheel_torque=requested_wheel_torque,
        applied_wheel_torque=applied_wheel_torque,
        wheel_speed=wheel_speed,
        wheel_momentum=wheel_momentum,
    )
    manifest = build_attitude_model_manifest(
        config,
        wheel_runtime=wheel_runtime,
        backend_version=capabilities.backend_version,
    )
    if not config.gnc.enabled:
        mode = "uncontrolled attitude observation"
    elif config.gnc.controller == "rate_damping":
        mode = "closed-loop angular-rate damping"
    else:
        mode = "closed-loop MRP feedback"
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
                    f"Basilisk executed {request.task_kind} as {mode} with "
                    "three balanced Honeywell_HR16 reaction wheels and no fallback."
                ),
            ),
        ),
    )
    return result


def _vector(values: object, name: str) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.ndim != 1 or result.size == 0:
        raise BasiliskExecutionError(f"Basilisk {name} has the wrong dimensions")
    return result


def _matrix(
    values: object,
    name: str,
    *,
    columns: int | None = None,
    minimum_columns: int | None = None,
) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.ndim != 2 or result.size == 0:
        raise BasiliskExecutionError(f"Basilisk {name} has the wrong dimensions")
    if columns is not None and result.shape[1] != columns:
        raise BasiliskExecutionError(
            f"Basilisk {name} must have exactly {columns} columns"
        )
    if minimum_columns is not None and result.shape[1] < minimum_columns:
        raise BasiliskExecutionError(
            f"Basilisk {name} must have at least {minimum_columns} columns"
        )
    return result


def _channels(
    *,
    position: np.ndarray,
    velocity: np.ndarray,
    attitude_mrp: np.ndarray,
    body_rate: np.ndarray,
    attitude_error: np.ndarray,
    rate_error: np.ndarray,
    requested_body_torque: np.ndarray,
    requested_wheel_torque: np.ndarray,
    applied_wheel_torque: np.ndarray,
    wheel_speed: np.ndarray,
    wheel_momentum: np.ndarray,
) -> tuple[ResultChannel, ...]:
    channels: list[ResultChannel] = []
    channels.extend(
        _vector_channels(
            "position",
            "position",
            "m",
            "inertial_N",
            position,
        )
    )
    channels.extend(
        _vector_channels(
            "velocity",
            "velocity",
            "m/s",
            "inertial_N",
            velocity,
        )
    )
    channels.extend(
        _vector_channels(
            "attitude_mrp",
            "modified_rodrigues_parameter",
            "1",
            "body_B_relative_inertial_N",
            attitude_mrp,
        )
    )
    channels.append(
        ResultChannel(
            "attitude_mrp_norm",
            "modified_rodrigues_parameter_norm",
            "1",
            "body_B_relative_inertial_N",
            np.linalg.norm(attitude_mrp, axis=1),
        )
    )
    channels.extend(
        _vector_channels(
            "body_angular_rate",
            "angular_velocity",
            "rad/s",
            "body_B",
            body_rate,
        )
    )
    channels.append(
        ResultChannel(
            "body_angular_rate_norm",
            "angular_velocity_norm",
            "rad/s",
            "body_B",
            np.linalg.norm(body_rate, axis=1),
        )
    )
    channels.extend(
        _vector_channels(
            "attitude_error_mrp",
            "attitude_error_modified_rodrigues_parameter",
            "1",
            "body_B_relative_reference_R",
            attitude_error,
        )
    )
    channels.append(
        ResultChannel(
            "attitude_error_norm",
            "attitude_error_modified_rodrigues_parameter_norm",
            "1",
            "body_B_relative_reference_R",
            np.linalg.norm(attitude_error, axis=1),
        )
    )
    channels.extend(
        _vector_channels(
            "angular_rate_error",
            "angular_velocity_error",
            "rad/s",
            "body_B",
            rate_error,
        )
    )
    channels.append(
        ResultChannel(
            "angular_rate_error_norm",
            "angular_velocity_error_norm",
            "rad/s",
            "body_B",
            np.linalg.norm(rate_error, axis=1),
        )
    )
    channels.extend(
        _vector_channels(
            "requested_body_control_torque",
            "requested_control_torque",
            "N m",
            "body_B",
            requested_body_torque,
        )
    )
    for wheel_index in range(_WHEEL_COUNT):
        number = wheel_index + 1
        wheel_frame = f"reaction_wheel_{number}_spin_axis_body_B"
        channels.extend(
            (
                ResultChannel(
                    f"requested_wheel_motor_torque_{number}",
                    "requested_motor_torque",
                    "N m",
                    wheel_frame,
                    requested_wheel_torque[:, wheel_index],
                ),
                ResultChannel(
                    f"applied_wheel_motor_torque_{number}",
                    "applied_motor_torque",
                    "N m",
                    wheel_frame,
                    applied_wheel_torque[:, wheel_index],
                ),
                ResultChannel(
                    f"reaction_wheel_speed_{number}",
                    "rotational_speed",
                    "rad/s",
                    wheel_frame,
                    wheel_speed[:, wheel_index],
                ),
                ResultChannel(
                    f"reaction_wheel_angular_momentum_{number}",
                    "spin_angular_momentum",
                    "N m s",
                    wheel_frame,
                    wheel_momentum[:, wheel_index],
                ),
            )
        )
    return tuple(channels)


def _vector_channels(
    prefix: str,
    quantity: str,
    unit: str,
    frame: str,
    values: np.ndarray,
) -> tuple[ResultChannel, ...]:
    return tuple(
        ResultChannel(
            f"{prefix}_{axis}",
            quantity,
            unit,
            frame,
            values[:, index],
        )
        for index, axis in enumerate(("x", "y", "z"))
    )


def _events(
    config: SpacecraftAttitudeConfig,
) -> tuple[SimulationEvent, ...]:
    reference = config.gnc.reference_mrp_sigma_rn
    return (
        SimulationEvent(
            name="simulation_start",
            time_s=0.0,
            attributes={
                "gnc_enabled": config.gnc.enabled,
                "controller": config.gnc.controller,
                "reference_mrp_1": reference[0],
                "reference_mrp_2": reference[1],
                "reference_mrp_3": reference[2],
                "reaction_wheel_model": config.reaction_wheels.model_id,
            },
        ),
        SimulationEvent(
            name="propagation_end",
            time_s=config.propagation.duration_s,
            attributes={
                "gnc_enabled": config.gnc.enabled,
                "controller": config.gnc.controller,
                "integrator": config.propagation.integrator,
            },
        ),
    )


def _metrics(
    *,
    config: SpacecraftAttitudeConfig,
    attitude_error: np.ndarray,
    rate_error: np.ndarray,
    requested_body_torque: np.ndarray,
    requested_wheel_torque: np.ndarray,
    applied_wheel_torque: np.ndarray,
    wheel_speed: np.ndarray,
    wheel_momentum: np.ndarray,
) -> tuple[ResultMetric, ...]:
    attitude_error_norm = np.linalg.norm(attitude_error, axis=1)
    rate_error_norm = np.linalg.norm(rate_error, axis=1)
    initial_attitude_error = float(attitude_error_norm[0])
    final_attitude_error = float(attitude_error_norm[-1])
    initial_rate_error = float(rate_error_norm[0])
    final_rate_error = float(rate_error_norm[-1])
    return (
        ResultMetric("propagation_duration", config.propagation.duration_s, "s"),
        ResultMetric("sample_count", float(len(attitude_error)), "1"),
        ResultMetric("gnc_enabled", float(config.gnc.enabled), "1"),
        ResultMetric(
            "initial_attitude_error_norm",
            initial_attitude_error,
            "1",
        ),
        ResultMetric(
            "final_attitude_error_norm",
            final_attitude_error,
            "1",
        ),
        ResultMetric(
            "attitude_error_reduction_factor",
            final_attitude_error / max(initial_attitude_error, 1e-15),
            "1",
        ),
        ResultMetric(
            "initial_angular_rate_error_norm",
            initial_rate_error,
            "rad/s",
        ),
        ResultMetric(
            "final_angular_rate_error_norm",
            final_rate_error,
            "rad/s",
        ),
        ResultMetric(
            "angular_rate_error_reduction_factor",
            final_rate_error / max(initial_rate_error, 1e-15),
            "1",
        ),
        ResultMetric(
            "maximum_requested_body_control_torque",
            float(np.max(np.linalg.norm(requested_body_torque, axis=1))),
            "N m",
        ),
        ResultMetric(
            "maximum_requested_wheel_motor_torque",
            float(np.max(np.abs(requested_wheel_torque))),
            "N m",
        ),
        ResultMetric(
            "maximum_applied_wheel_motor_torque",
            float(np.max(np.abs(applied_wheel_torque))),
            "N m",
        ),
        ResultMetric(
            "maximum_reaction_wheel_speed",
            float(np.max(np.abs(wheel_speed))),
            "rad/s",
        ),
        ResultMetric(
            "maximum_reaction_wheel_speed_change",
            float(np.max(np.abs(wheel_speed[-1] - wheel_speed[0]))),
            "rad/s",
        ),
        ResultMetric(
            "maximum_reaction_wheel_momentum",
            float(np.max(np.abs(wheel_momentum))),
            "N m s",
        ),
        ResultMetric(
            "maximum_reaction_wheel_momentum_change",
            float(np.max(np.abs(wheel_momentum[-1] - wheel_momentum[0]))),
            "N m s",
        ),
    )
