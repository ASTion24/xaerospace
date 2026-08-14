from __future__ import annotations

import math
from collections.abc import Mapping

from .attitude_config import SpacecraftAttitudeConfig
from .models import (
    ModelEquation,
    ModelEvent,
    ModelManifest,
    ModelParameter,
    StateVariable,
)

SPACECRAFT_REFERENCE = "Basilisk.simulation.spacecraft.Spacecraft"
RK4_REFERENCE = "Basilisk.simulation.svIntegrators.svIntegratorRK4"
RW_EFFECTOR_REFERENCE = (
    "Basilisk.simulation.reactionWheelStateEffector.ReactionWheelStateEffector"
)
SIMPLE_NAV_REFERENCE = "Basilisk.simulation.simpleNav.SimpleNav"
INERTIAL_GUIDANCE_REFERENCE = "Basilisk.fswAlgorithms.inertial3D.inertial3D"
TRACKING_ERROR_REFERENCE = "Basilisk.fswAlgorithms.attTrackingError.attTrackingError"
MRP_FEEDBACK_REFERENCE = "Basilisk.fswAlgorithms.mrpFeedback.mrpFeedback"
RW_ALLOCATION_REFERENCE = "Basilisk.fswAlgorithms.rwMotorTorque.rwMotorTorque"
RW_FACTORY_REFERENCE = "Basilisk.utilities.simIncludeRW.rwFactory.Honeywell_HR16"


def build_attitude_model_manifest(
    config: SpacecraftAttitudeConfig,
    *,
    wheel_runtime: tuple[Mapping[str, float], ...],
    backend_version: str,
) -> ModelManifest:
    limitations = [
        "Only inertial-fixed MRP pointing is accepted by schema version 1.",
        "Navigation is perfect: no sensor noise, bias, latency, or estimator dynamics.",
        (
            "The MRP feedback controller has integral feedback disabled."
            if config.gnc.controller == "mrp_feedback_pd"
            else "Rate damping sets the MRP proportional gain to zero."
        ),
        "No momentum unloading, magnetic torquers, thrusters, or wheel thermal model.",
        "No gravity, orbit propagation, environmental torque, or flexible dynamics.",
        "Reaction wheels use the balanced-wheel model with friction disabled.",
        "The report is curated against Basilisk 2.11.0, not symbolically extracted.",
    ]
    if not config.gnc.enabled:
        limitations.append(
            "GNC control is disabled; guidance and tracking errors are observed only."
        )
    elif config.gnc.controller == "rate_damping":
        limitations.append(
            "Rate damping regulates angular velocity but does not hold an attitude."
        )

    result = ModelManifest(
        schema_version=1,
        fidelity="version-pinned documentation projection of runtime equations",
        backend_name="Basilisk",
        backend_version=backend_version,
        model_name=config.name,
        dynamics=config.dynamics,
        coordinate_system=(
            "N is the inertial frame.",
            "B is the spacecraft hub body frame.",
            "R is the inertially fixed attitude-reference frame.",
            "sigma_BN is the MRP attitude of B relative to N.",
            "omega_BN_B is the B/N angular velocity resolved in B.",
            "Reaction-wheel spin axes are constant vectors resolved in B.",
        ),
        state_vector=_state_vector(),
        initial_state=_initial_state(config),
        equations=_equations(config),
        parameters=_parameters(config, wheel_runtime),
        input_series=(),
        events=_events(config),
        assumptions=(
            "The spacecraft hub is a rigid body with principal axes aligned to B.",
            "The spacecraft center of mass is fixed at the body-frame origin.",
            "Three reaction wheels form an orthogonal, full-rank actuator set.",
            "Flight software and dynamics execute at the same fixed task rate.",
            "The commanded inertial reference has zero angular rate and acceleration.",
            "All simulated actuator torques are internal to the spacecraft-wheel system.",
        ),
        limitations=tuple(limitations),
        implementation_references=(
            SPACECRAFT_REFERENCE,
            RK4_REFERENCE,
            RW_EFFECTOR_REFERENCE,
            SIMPLE_NAV_REFERENCE,
            INERTIAL_GUIDANCE_REFERENCE,
            TRACKING_ERROR_REFERENCE,
            MRP_FEEDBACK_REFERENCE,
            RW_ALLOCATION_REFERENCE,
            RW_FACTORY_REFERENCE,
            "aerospace_simulator.attitude_backend",
        ),
    )
    return result


def _state_vector() -> tuple[StateVariable, ...]:
    states: list[StateVariable] = []
    for axis in ("1", "2", "3"):
        states.append(
            StateVariable(
                f"r_BN_N,{axis}",
                f"Inertial position component {axis}",
                "m",
                "integrated translation",
            )
        )
    for axis in ("1", "2", "3"):
        states.append(
            StateVariable(
                f"v_BN_N,{axis}",
                f"Inertial velocity component {axis}",
                "m/s",
                "integrated translation",
            )
        )
    for axis in ("1", "2", "3"):
        states.append(
            StateVariable(
                f"sigma_BN,{axis}",
                f"Body attitude MRP component {axis}",
                "1",
                "integrated attitude",
            )
        )
    for axis in ("1", "2", "3"):
        states.append(
            StateVariable(
                f"omega_BN_B,{axis}",
                f"Body angular-rate component {axis}",
                "rad/s",
                "integrated rotation",
            )
        )
    for wheel_index in range(1, 4):
        states.append(
            StateVariable(
                f"Omega_RW{wheel_index}",
                f"Reaction wheel {wheel_index} relative spin rate",
                "rad/s",
                "integrated wheel speed",
            )
        )
    return tuple(states)


def _initial_state(
    config: SpacecraftAttitudeConfig,
) -> tuple[ModelParameter, ...]:
    parameters: list[ModelParameter] = []
    for axis in range(1, 4):
        parameters.extend(
            (
                _parameter(
                    f"r_BN_N,{axis}(0)",
                    f"Initial inertial position component {axis}",
                    0.0,
                    "m",
                    "adapter-defined attitude-only origin",
                ),
                _parameter(
                    f"v_BN_N,{axis}(0)",
                    f"Initial inertial velocity component {axis}",
                    0.0,
                    "m/s",
                    "adapter-defined attitude-only state",
                ),
                _parameter(
                    f"sigma_BN,{axis}(0)",
                    f"Initial body MRP component {axis}",
                    config.initial_state.mrp_sigma_bn[axis - 1],
                    "1",
                    "contract.initial_state",
                ),
                _parameter(
                    f"omega_BN_B,{axis}(0)",
                    f"Initial body angular rate component {axis}",
                    config.initial_state.angular_velocity_bn_body_rad_s[axis - 1],
                    "rad/s",
                    "contract.initial_state",
                ),
                _parameter(
                    f"Omega_RW{axis}(0)",
                    f"Initial reaction wheel {axis} speed",
                    config.reaction_wheels.initial_speed_rpm[axis - 1]
                    * 2.0
                    * math.pi
                    / 60.0,
                    "rad/s",
                    "contract.reaction_wheels.initial_speed_rpm",
                ),
            )
        )
    return tuple(parameters)


def _equations(
    config: SpacecraftAttitudeConfig,
) -> tuple[ModelEquation, ...]:
    control_phase = "all" if config.gnc.enabled else "disconnected"
    if config.gnc.controller == "rate_damping":
        control_equation = ModelEquation(
            id="angular_rate_damping_control",
            name="Angular-rate damping feedback",
            phase=control_phase,
            expression="L_B,cmd = -P omega_BR_B + wheel compensation",
            latex=(
                r"\mathbf L_{\mathrm{cmd}}^B=-P\boldsymbol\omega_{BR}^B"
                r"+\mathbf L_{\mathrm{RW,comp}}"
            ),
            explanation=(
                "The MRP gain is zero, so the reaction wheels damp body rate "
                "without regulating the resulting attitude."
            ),
            implementation_reference=MRP_FEEDBACK_REFERENCE,
        )
    else:
        control_equation = ModelEquation(
            id="mrp_feedback_control",
            name="MRP proportional-derivative feedback",
            phase=control_phase,
            expression="L_B,cmd = -K sigma_BR - P omega_BR_B + wheel compensation",
            latex=(
                r"\mathbf L_{\mathrm{cmd}}^B=-K\boldsymbol\sigma_{BR}"
                r"-P\boldsymbol\omega_{BR}^B+\mathbf L_{\mathrm{RW,comp}}"
            ),
            explanation=(
                "Integral feedback is disabled. Basilisk uses wheel-speed and "
                "configuration messages for reaction-wheel momentum compensation."
            ),
            implementation_reference=MRP_FEEDBACK_REFERENCE,
        )
    return (
        ModelEquation(
            id="free_translation",
            name="Force-free translational dynamics",
            phase="all",
            expression="r_dot_BN_N = v_BN_N; m_total v_dot_BN_N = 0",
            latex=(
                r"\dot{\mathbf r}_{BN}^N=\mathbf v_{BN}^N,\qquad "
                r"m_{\mathrm{tot}}\dot{\mathbf v}_{BN}^N=\mathbf 0"
            ),
            explanation=(
                "The attitude-only contract applies no gravity or external force, "
                "so the initialized zero translational state remains zero."
            ),
            implementation_reference=SPACECRAFT_REFERENCE,
        ),
        ModelEquation(
            id="mrp_kinematics",
            name="Modified Rodrigues parameter kinematics",
            phase="all",
            expression=(
                "sigma_dot_BN = 1/4 [(1-sigma^T sigma) I + "
                "2 [sigma x] + 2 sigma sigma^T] omega_BN_B"
            ),
            latex=(
                r"\dot{\boldsymbol\sigma}_{BN}=\frac14"
                r"\left[(1-\boldsymbol\sigma^T\boldsymbol\sigma)I"
                r"+2[\boldsymbol\sigma\times]+2\boldsymbol\sigma"
                r"\boldsymbol\sigma^T\right]\boldsymbol\omega_{BN}^B"
            ),
            explanation=(
                "Basilisk propagates hub attitude with the MRP shadow-set switch "
                "available to keep the representation nonsingular."
            ),
            implementation_reference=SPACECRAFT_REFERENCE,
        ),
        ModelEquation(
            id="spacecraft_wheel_angular_momentum",
            name="Coupled spacecraft and reaction-wheel rotation",
            phase="all",
            expression=("H_B = I_system omega_BN_B + sum(g_s,i h_s,i); d(H_N)/dt = 0"),
            latex=(
                r"\mathbf H^B=I_{\mathrm{sys}}\boldsymbol\omega_{BN}^B"
                r"+\sum_i\hat{\mathbf g}_{s,i}^B h_{s,i},\qquad "
                r"{}^N\frac{d\mathbf H}{dt}=\mathbf 0"
            ),
            explanation=(
                "The balanced-wheel state effector couples wheel momentum to the "
                "rigid hub while conserving total angular momentum in the absence "
                "of external torque."
            ),
            implementation_reference=RW_EFFECTOR_REFERENCE,
        ),
        ModelEquation(
            id="reaction_wheel_spin_dynamics",
            name="Reaction-wheel spin dynamics and motor saturation",
            phase="all",
            expression=("h_s,i = J_s,i Omega_i; u_i = clip(u_i,cmd, -u_max, u_max)"),
            latex=(
                r"h_{s,i}=J_{s,i}\Omega_i,\qquad "
                r"u_i=\operatorname{clip}(u_{i,\mathrm{cmd}},-u_{\max},u_{\max})"
            ),
            explanation=(
                "Motor torque changes wheel spin momentum and applies the equal "
                "and opposite internal torque to the spacecraft."
            ),
            implementation_reference=RW_EFFECTOR_REFERENCE,
        ),
        ModelEquation(
            id="perfect_attitude_navigation",
            name="Perfect attitude navigation",
            phase="all",
            expression="sigma_hat_BN = sigma_BN; omega_hat_BN_B = omega_BN_B",
            latex=(
                r"\hat{\boldsymbol\sigma}_{BN}=\boldsymbol\sigma_{BN},\qquad "
                r"\hat{\boldsymbol\omega}_{BN}^B=\boldsymbol\omega_{BN}^B"
            ),
            explanation=(
                "SimpleNav republishes the truth attitude and angular rate without "
                "configured noise for this deterministic contract."
            ),
            implementation_reference=SIMPLE_NAV_REFERENCE,
        ),
        ModelEquation(
            id="inertial_fixed_guidance",
            name="Inertially fixed attitude reference",
            phase="all",
            expression=("sigma_RN = constant; omega_RN_N = 0; omega_dot_RN_N = 0"),
            latex=(
                r"\boldsymbol\sigma_{RN}=\mathrm{constant},\qquad "
                r"\boldsymbol\omega_{RN}^N=\dot{\boldsymbol\omega}_{RN}^N=\mathbf 0"
            ),
            explanation="inertial3D publishes the configured fixed MRP reference.",
            implementation_reference=INERTIAL_GUIDANCE_REFERENCE,
        ),
        ModelEquation(
            id="mrp_tracking_error",
            name="MRP attitude and rate tracking error",
            phase="all",
            expression=(
                "sigma_BR = MRP(B/N) compose inverse(MRP(R/N)); "
                "omega_BR_B = omega_BN_B - C_BR omega_RN_R"
            ),
            latex=(
                r"\boldsymbol\sigma_{BR}=\boldsymbol\sigma_{BN}\ominus"
                r"\boldsymbol\sigma_{RN},\qquad "
                r"\boldsymbol\omega_{BR}^B=\boldsymbol\omega_{BN}^B"
                r"-C_R^B\boldsymbol\omega_{RN}^R"
            ),
            explanation=(
                "attTrackingError computes the shortest-set MRP error and the "
                "corresponding body-resolved angular-rate error."
            ),
            implementation_reference=TRACKING_ERROR_REFERENCE,
        ),
        control_equation,
        ModelEquation(
            id="minimum_norm_wheel_allocation",
            name="Minimum-norm reaction-wheel torque allocation",
            phase=control_phase,
            expression=("u_rw,cmd = -G_s^T (G_s G_s^T)^-1 L_B,cmd"),
            latex=(
                r"\mathbf u_{\mathrm{RW,cmd}}=-G_s^T"
                r"(G_sG_s^T)^{-1}\mathbf L_{\mathrm{cmd}}^B"
            ),
            explanation=(
                "rwMotorTorque maps the requested body torque onto the full-rank "
                "three-wheel spin-axis matrix."
            ),
            implementation_reference=RW_ALLOCATION_REFERENCE,
        ),
        ModelEquation(
            id="fixed_step_rk4",
            name="Fixed-step fourth-order Runge-Kutta integration",
            phase="all",
            expression="x_(n+1) = RK4(f, x_n, dt)",
            latex=r"\mathbf x_{n+1}=\operatorname{RK4}(f,\mathbf x_n,\Delta t)",
            explanation=(
                "The Basilisk spacecraft dynamic object is explicitly assigned its "
                "fourth-order fixed-step state-vector integrator."
            ),
            implementation_reference=RK4_REFERENCE,
        ),
    )


def _parameters(
    config: SpacecraftAttitudeConfig,
    wheel_runtime: tuple[Mapping[str, float], ...],
) -> tuple[ModelParameter, ...]:
    parameters: list[ModelParameter] = [
        _parameter(
            "m_hub",
            "Spacecraft hub mass",
            config.spacecraft.mass_kg,
            "kg",
            "contract.spacecraft.mass_kg",
        ),
        _parameter(
            "K",
            "MRP proportional gain",
            config.gnc.mrp_gain_n_m,
            "N m",
            "contract.gnc.mrp_gain_n_m",
        ),
        _parameter(
            "P",
            "Angular-rate feedback gain",
            config.gnc.rate_gain_n_m_s,
            "N m s",
            "contract.gnc.rate_gain_n_m_s",
        ),
        _parameter(
            "GNC_enabled",
            "Closed-loop GNC connection state",
            str(config.gnc.enabled).lower(),
            "1",
            "contract.gnc.enabled",
        ),
        _parameter(
            "dt",
            "Fixed RK4 task step",
            config.propagation.step_size_s,
            "s",
            "contract.propagation",
        ),
        _parameter(
            "dt_out",
            "Output sampling interval",
            config.propagation.output_interval_s,
            "s",
            "contract.propagation",
        ),
        _parameter(
            "T",
            "Propagation duration",
            config.propagation.duration_s,
            "s",
            "contract.propagation",
        ),
    ]
    for index, inertia in enumerate(
        config.spacecraft.principal_inertia_kg_m2,
        start=1,
    ):
        parameters.append(
            _parameter(
                f"I_{index}{index}",
                f"Hub principal moment {index}",
                inertia,
                "kg m^2",
                "contract.spacecraft.principal_inertia_kg_m2",
            )
        )
    for wheel_index, (axis, runtime) in enumerate(
        zip(
            config.reaction_wheels.spin_axes_body,
            wheel_runtime,
            strict=True,
        ),
        start=1,
    ):
        parameters.extend(
            (
                _parameter(
                    f"g_s,{wheel_index}",
                    f"Reaction wheel {wheel_index} spin axis",
                    "[" + ", ".join(f"{value:.9g}" for value in axis) + "]",
                    "1",
                    "contract.reaction_wheels.spin_axes_body",
                ),
                _parameter(
                    f"J_s,{wheel_index}",
                    f"Reaction wheel {wheel_index} spin inertia",
                    float(runtime["spin_inertia_kg_m2"]),
                    "kg m^2",
                    RW_FACTORY_REFERENCE,
                ),
                _parameter(
                    f"m_RW,{wheel_index}",
                    f"Reaction wheel {wheel_index} rotor mass",
                    float(runtime["mass_kg"]),
                    "kg",
                    RW_FACTORY_REFERENCE,
                ),
            )
        )
    parameters.extend(
        (
            _parameter(
                "h_s,max",
                "Per-wheel momentum capacity",
                config.reaction_wheels.max_momentum_n_m_s,
                "N m s",
                "contract.reaction_wheels.max_momentum_n_m_s",
            ),
            _parameter(
                "u_max",
                "Per-wheel motor torque limit",
                config.reaction_wheels.max_motor_torque_n_m,
                "N m",
                "contract.reaction_wheels.max_motor_torque_n_m",
            ),
            _parameter(
                "Omega_max",
                "Per-wheel speed limit",
                config.reaction_wheels.max_speed_rpm,
                "rpm",
                "contract.reaction_wheels.max_speed_rpm",
            ),
        )
    )
    return tuple(parameters)


def _events(config: SpacecraftAttitudeConfig) -> tuple[ModelEvent, ...]:
    mode = (
        "connect MRP feedback and wheel torque allocation"
        if config.gnc.enabled
        else ("leave MRP feedback and wheel torque allocation disconnected")
    )
    return (
        ModelEvent(
            id="simulation_start",
            condition="t = 0 s",
            direction="initialization",
            action=mode,
            implementation_reference="aerospace_simulator.attitude_backend",
        ),
        ModelEvent(
            id="propagation_end",
            condition=f"t = {config.propagation.duration_s:.9g} s",
            direction="increasing time",
            action="terminate fixed-step propagation",
            implementation_reference=SPACECRAFT_REFERENCE,
        ),
    )


def _parameter(
    symbol: str,
    name: str,
    value: float | str,
    unit: str,
    source: str,
) -> ModelParameter:
    return ModelParameter(symbol, name, value, unit, source)
