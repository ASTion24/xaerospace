from __future__ import annotations

from .config import POINT_MASS_DYNAMICS, ScenarioConfig
from .models import (
    ModelEquation,
    ModelEvent,
    ModelInputSeries,
    ModelManifest,
    ModelParameter,
    StateVariable,
)

RAIL_REFERENCE = "rocketpy.simulation.flight.Flight.udot_rail1"
FREE_FLIGHT_REFERENCE = "rocketpy.simulation.flight.Flight.u_dot_generalized_3dof"
RIGID_FLIGHT_REFERENCE = "rocketpy.simulation.flight.Flight.u_dot_generalized"
MASS_FLOW_REFERENCE = (
    "rocketpy.motors.point_mass_motor.PointMassMotor.total_mass_flow_rate"
)
GENERIC_MASS_FLOW_REFERENCE = "rocketpy.motors.motor.Motor.total_mass_flow_rate"
TOTAL_MASS_REFERENCE = "rocketpy.rocket.rocket.Rocket.evaluate_total_mass"
EVENT_REFERENCE = "rocketpy.simulation.flight.Flight._Flight__check_simulation_events"
PARACHUTE_REFERENCE = "rocketpy.simulation.flight.Flight.u_dot_parachute"


def build_model_manifest(
    config: ScenarioConfig,
    *,
    flight: object,
    rocket: object,
    motor: object,
    environment: object,
    backend_version: str,
) -> ModelManifest:
    if config.dynamics not in POINT_MASS_DYNAMICS:
        return _build_rigid_body_manifest(
            config,
            flight=flight,
            rocket=rocket,
            motor=motor,
            environment=environment,
            backend_version=backend_version,
        )

    launch_altitude = config.environment.elevation_m
    total_impulse = float(motor.total_impulse)
    effective_exhaust_velocity = total_impulse / config.motor.propellant_initial_mass_kg
    reference_area = float(rocket.area)
    dry_mass = config.vehicle.dry_mass_without_motor_kg + config.motor.dry_mass_kg
    initial_mass = dry_mass + config.motor.propellant_initial_mass_kg

    equations = [
        ModelEquation(
            id="kinematics",
            name="Translational kinematics",
            phase="all",
            expression="r_dot = v",
            latex=r"\dot{\mathbf r}=\mathbf v",
            explanation=(
                "Position is integrated in the local East-North-Up inertial frame."
            ),
            implementation_reference=FREE_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="relative_wind",
            name="Atmosphere-relative velocity",
            phase="all",
            expression=(
                "v_f = w(z) - v; V_f = ||v_f||; "
                "Mach = V_f / a(z); Re = rho(z) V_f (2 r) / mu(z)"
            ),
            latex=(
                r"\mathbf v_f=\mathbf w(z)-\mathbf v,\quad "
                r"V_f=\lVert\mathbf v_f\rVert,\quad "
                r"M=\frac{V_f}{a(z)},\quad "
                r"Re=\frac{\rho(z)V_f(2r)}{\mu(z)}"
            ),
            explanation=(
                "Density, wind, speed of sound, and viscosity are queried from "
                "RocketPy's standard-atmosphere Environment."
            ),
            implementation_reference=FREE_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="mass_depletion",
            name="Thrust-coupled propellant depletion",
            phase="powered",
            expression=(
                "I_total = integral(T(t), 0, t_b); "
                "v_e = I_total / m_p0; m_p_dot = -T(t) / v_e; "
                "m(t) = m_dry + m_p0 + integral(m_p_dot, 0, t)"
            ),
            latex=(
                r"I_{\mathrm{tot}}=\int_0^{t_b}T(t)\,dt,\quad "
                r"v_e=\frac{I_{\mathrm{tot}}}{m_{p,0}},\quad "
                r"\dot m_p=-\frac{T(t)}{v_e},\quad "
                r"m(t)=m_{\mathrm{dry}}+m_{p,0}+\int_0^t\dot m_p(\tau)\,d\tau"
            ),
            explanation=(
                "PointMassMotor assumes constant effective exhaust velocity. "
                "Mass flow becomes zero outside the thrust-curve domain."
            ),
            implementation_reference=MASS_FLOW_REFERENCE,
        ),
        ModelEquation(
            id="axial_drag",
            name="Axial aerodynamic drag",
            phase="all",
            expression=(
                "R_b = [0, 0, -0.5 rho(z) V_f^2 A_ref C_D]^T; "
                "C_D = C_D,on before burnout, else C_D,off"
            ),
            latex=(
                r"\mathbf R_b=\begin{bmatrix}0\\0\\"
                r"-\frac12\rho(z)V_f^2A_{\mathrm{ref}}C_D\end{bmatrix},\quad "
                r"C_D=\begin{cases}C_{D,\mathrm{on}}&t<t_b\\"
                r"C_{D,\mathrm{off}}&t\ge t_b\end{cases}"
            ),
            explanation=(
                "The point-mass scenario has no aerodynamic surfaces or lift. "
                "Drag acts on the rocket body z-axis used by RocketPy."
            ),
            implementation_reference=FREE_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="body_forces",
            name="Thrust and weight in the body frame",
            phase="all",
            expression=(
                "F_T,b = [0, 0, T_net]^T; "
                "F_g,b = K(q)^T [0, 0, -m g(z)]^T; "
                "T_net = T(t) for 0 < t < t_b, else 0"
            ),
            latex=(
                r"\mathbf F_{T,b}=\begin{bmatrix}0\\0\\T_{\mathrm{net}}\end{bmatrix},"
                r"\quad \mathbf F_{g,b}=K(\mathbf q)^T"
                r"\begin{bmatrix}0\\0\\-m g(z)\end{bmatrix}"
            ),
            explanation=(
                "PointMassMotor sets nozzle area to zero, so pressure-thrust "
                "correction is exactly zero in this model."
            ),
            implementation_reference=FREE_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="free_flight_translation",
            name="Post-rail translational dynamics",
            phase="free_flight",
            expression="v_dot = K(q) (F_T,b + F_g,b + R_b) / m(t)",
            latex=(
                r"\dot{\mathbf v}=\frac{1}{m(t)}K(\mathbf q)"
                r"\left(\mathbf F_{T,b}+\mathbf F_{g,b}+\mathbf R_b\right)"
            ),
            explanation=("K(q) maps body-frame forces into the local inertial frame."),
            implementation_reference=FREE_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="rail_constraint",
            name="One-degree-of-freedom rail dynamics",
            phase="rail",
            expression=(
                "b_z = K(q) [0,0,1]^T; "
                "a_parallel = max(0, (T_net + R_b,z)/m - b_z,z g(z)); "
                "v_dot = b_z a_parallel"
            ),
            latex=(
                r"\mathbf b_z=K(\mathbf q)\mathbf e_3,\quad "
                r"a_\parallel=\max\left(0,\frac{T_{\mathrm{net}}+R_{b,z}}{m}"
                r"-b_{z,z}g(z)\right),\quad "
                r"\dot{\mathbf v}=\mathbf b_z a_\parallel"
            ),
            explanation=(
                "Negative rail acceleration is clamped to zero, preventing the "
                "vehicle from sliding backward before lift-off."
            ),
            implementation_reference=RAIL_REFERENCE,
        ),
        _attitude_equation(config),
    ]

    parameters = [
        _parameter(
            "m_vehicle,dry",
            "Vehicle dry mass without motor",
            config.vehicle.dry_mass_without_motor_kg,
            "kg",
            "scenario.vehicle.dry_mass_without_motor_kg",
        ),
        _parameter(
            "m_motor,dry",
            "Motor dry mass",
            config.motor.dry_mass_kg,
            "kg",
            "scenario.motor.dry_mass_kg",
        ),
        _parameter(
            "m_dry",
            "Combined dry mass",
            dry_mass,
            "kg",
            "derived",
        ),
        _parameter(
            "m_p0",
            "Initial propellant mass",
            config.motor.propellant_initial_mass_kg,
            "kg",
            "scenario.motor.propellant_initial_mass_kg",
        ),
        _parameter("m_0", "Lift-off mass", initial_mass, "kg", "derived"),
        _parameter(
            "r",
            "Rocket radius",
            config.vehicle.radius_m,
            "m",
            "scenario.vehicle.radius_m",
        ),
        _parameter(
            "A_ref",
            "Reference area",
            reference_area,
            "m^2",
            "RocketPy rocket.area = pi r^2",
        ),
        _parameter(
            "C_D,on",
            "Powered drag coefficient",
            config.vehicle.drag_coefficient_power_on,
            "1",
            "scenario.vehicle.drag_coefficient_power_on",
        ),
        _parameter(
            "C_D,off",
            "Unpowered drag coefficient",
            config.vehicle.drag_coefficient_power_off,
            "1",
            "scenario.vehicle.drag_coefficient_power_off",
        ),
        _parameter(
            "t_b",
            "Motor burnout time",
            config.motor.burn_time_s,
            "s",
            "scenario.motor.burn_time_s",
        ),
        _parameter(
            "I_total",
            "Total impulse",
            total_impulse,
            "N s",
            "RocketPy motor.total_impulse",
        ),
        _parameter(
            "v_e",
            "Effective exhaust velocity",
            effective_exhaust_velocity,
            "m/s",
            "RocketPy PointMassMotor exhaust_velocity",
        ),
        _parameter(
            "L_rail",
            "Effective launch rail length",
            float(flight.effective_1rl),
            "m",
            "RocketPy flight.effective_1rl",
        ),
        _parameter(
            "inclination",
            "Launch inclination from horizontal",
            config.launch.inclination_deg,
            "deg",
            "scenario.launch.inclination_deg",
        ),
        _parameter(
            "heading",
            "Launch heading clockwise from north",
            config.launch.heading_deg,
            "deg",
            "scenario.launch.heading_deg",
        ),
        _parameter(
            "k_wc",
            "Weathercock alignment coefficient",
            config.vehicle.weathercock_coefficient,
            "1/s",
            "scenario.vehicle.weathercock_coefficient",
        ),
        _parameter(
            "rho_0",
            "Atmospheric density at launch altitude",
            float(environment.density.get_value_opt(launch_altitude)),
            "kg/m^3",
            "RocketPy Environment",
        ),
        _parameter(
            "g_0",
            "Gravity at launch altitude",
            float(environment.gravity.get_value_opt(launch_altitude)),
            "m/s^2",
            "RocketPy Environment",
        ),
        _parameter(
            "solver",
            "ODE solver",
            str(flight.ode_solver),
            "",
            "RocketPy Flight",
        ),
        _parameter(
            "rtol",
            "Relative integration tolerance",
            float(flight.rtol),
            "1",
            "RocketPy Flight",
        ),
        _parameter(
            "dt_max",
            "Maximum integration step",
            float(flight.max_time_step),
            "s",
            "scenario.launch.max_time_step_s",
        ),
    ]

    events = list(_events())
    assumptions = [
        "Single-stage point-mass vehicle.",
        "No aerodynamic lift, fins, air brakes, or active control.",
        "Powered and unpowered drag coefficients are scalar constants.",
        "Thrust values are linearly interpolated between scenario points.",
        (
            "The standard atmosphere supplies altitude-dependent density, "
            "pressure, speed of sound, viscosity, and gravity."
        ),
        "Standard-atmosphere wind is zero for this scenario.",
        "Earth curvature, Coriolis acceleration, and orbital dynamics are omitted.",
        (
            "The pressure-thrust correction is zero because PointMassMotor has "
            "zero nozzle exit area."
        ),
    ]
    limitations = [
        "This is not a 6-DOF torque or stability simulation.",
        "The internal quaternion is auxiliary in 3-DOF mode.",
        "With k_wc=0 the attitude is frozen at the launch-rail orientation.",
        (
            "Axial drag follows the body z-axis, not an independently solved "
            "aerodynamic attitude."
        ),
        (
            "The report is curated against the pinned RocketPy implementation; "
            "it is not generated from symbolic source code."
        ),
    ]
    implementation_references = [
        RAIL_REFERENCE,
        FREE_FLIGHT_REFERENCE,
        MASS_FLOW_REFERENCE,
        TOTAL_MASS_REFERENCE,
        EVENT_REFERENCE,
    ]
    if config.recovery is None:
        assumptions.append("No parachutes are present.")
    else:
        _extend_recovery_manifest(
            config,
            rocket=rocket,
            equations=equations,
            events=events,
            parameters=parameters,
            assumptions=assumptions,
            limitations=limitations,
            implementation_references=implementation_references,
            transition_assumption=(
                "After deployment RocketPy continues with its three-degree-of-"
                "freedom dry-mass parachute descent model."
            ),
            frozen_attitude=False,
        )

    return ModelManifest(
        schema_version=1,
        fidelity="version-pinned documentation projection of runtime equations",
        backend_name="RocketPy",
        backend_version=backend_version,
        model_name=config.name,
        dynamics=config.dynamics,
        coordinate_system=(
            "Local inertial X axis points East.",
            "Local inertial Y axis points North.",
            "Local inertial Z axis points Up; altitude AGL is z minus site elevation.",
            "K(q) maps body-frame vectors to the local inertial frame.",
            "The rocket thrust axis is the body positive z-axis.",
        ),
        state_vector=_state_vector(),
        initial_state=_initial_state(flight),
        equations=tuple(equations),
        parameters=tuple(parameters),
        input_series=(_thrust_input_series(config),),
        events=tuple(events),
        assumptions=tuple(assumptions),
        limitations=tuple(limitations),
        implementation_references=tuple(implementation_references),
    )


def _build_rigid_body_manifest(
    config: ScenarioConfig,
    *,
    flight: object,
    rocket: object,
    motor: object,
    environment: object,
    backend_version: str,
) -> ModelManifest:
    rigid_body = config.rigid_body
    if rigid_body is None:
        raise ValueError("rigid-body manifest requires rigid-body configuration")

    total_impulse = float(motor.total_impulse)
    dry_mass = config.vehicle.dry_mass_without_motor_kg + config.motor.dry_mass_kg
    initial_mass = dry_mass + config.motor.propellant_initial_mass_kg
    effective_exhaust_velocity = total_impulse / config.motor.propellant_initial_mass_kg
    parameters = [
        _parameter(
            "m_vehicle,dry",
            "Vehicle dry mass without motor",
            config.vehicle.dry_mass_without_motor_kg,
            "kg",
            "scenario.vehicle.dry_mass_without_motor_kg",
        ),
        _parameter(
            "m_motor,dry",
            "Motor dry mass",
            config.motor.dry_mass_kg,
            "kg",
            "scenario.motor.dry_mass_kg",
        ),
        _parameter("m_dry", "Combined dry mass", dry_mass, "kg", "derived"),
        _parameter(
            "m_p0",
            "Initial propellant mass",
            config.motor.propellant_initial_mass_kg,
            "kg",
            "scenario.motor.propellant_initial_mass_kg",
        ),
        _parameter("m_0", "Lift-off mass", initial_mass, "kg", "derived"),
        _parameter(
            "A_ref",
            "Reference area",
            float(rocket.area),
            "m^2",
            "RocketPy rocket.area",
        ),
        _parameter(
            "C_D,on",
            "Powered axial drag coefficient",
            config.vehicle.drag_coefficient_power_on,
            "1",
            "scenario.vehicle.drag_coefficient_power_on",
        ),
        _parameter(
            "C_D,off",
            "Unpowered axial drag coefficient",
            config.vehicle.drag_coefficient_power_off,
            "1",
            "scenario.vehicle.drag_coefficient_power_off",
        ),
        _parameter(
            "I_vehicle,11",
            "Vehicle dry transverse inertia 11",
            rigid_body.vehicle_dry_inertia_kg_m2[0],
            "kg m^2",
            "scenario.rigid_body.vehicle_dry_inertia_kg_m2",
        ),
        _parameter(
            "I_vehicle,22",
            "Vehicle dry transverse inertia 22",
            rigid_body.vehicle_dry_inertia_kg_m2[1],
            "kg m^2",
            "scenario.rigid_body.vehicle_dry_inertia_kg_m2",
        ),
        _parameter(
            "I_vehicle,33",
            "Vehicle dry axial inertia 33",
            rigid_body.vehicle_dry_inertia_kg_m2[2],
            "kg m^2",
            "scenario.rigid_body.vehicle_dry_inertia_kg_m2",
        ),
        _parameter(
            "I_motor,11",
            "Motor dry inertia 11",
            rigid_body.motor.dry_inertia_kg_m2[0],
            "kg m^2",
            "scenario.rigid_body.motor.dry_inertia_kg_m2",
        ),
        _parameter(
            "I_motor,22",
            "Motor dry inertia 22",
            rigid_body.motor.dry_inertia_kg_m2[1],
            "kg m^2",
            "scenario.rigid_body.motor.dry_inertia_kg_m2",
        ),
        _parameter(
            "I_motor,33",
            "Motor dry inertia 33",
            rigid_body.motor.dry_inertia_kg_m2[2],
            "kg m^2",
            "scenario.rigid_body.motor.dry_inertia_kg_m2",
        ),
        _parameter(
            "z_motor",
            "Motor position in vehicle coordinates",
            rigid_body.motor_position_m,
            "m",
            "scenario.rigid_body.motor_position_m",
        ),
        _parameter(
            "L_nose",
            "Nose length",
            rigid_body.nose.length_m,
            "m",
            "scenario.rigid_body.nose.length_m",
        ),
        _parameter(
            "N_fins",
            "Fin count",
            float(rigid_body.fins.count),
            "1",
            "scenario.rigid_body.fins.count",
        ),
        _parameter(
            "span_fin",
            "Fin span",
            rigid_body.fins.span_m,
            "m",
            "scenario.rigid_body.fins.span_m",
        ),
        _parameter(
            "cant_fin",
            "Fin cant angle",
            rigid_body.fins.cant_angle_deg,
            "deg",
            "scenario.rigid_body.fins.cant_angle_deg",
        ),
        _parameter(
            "t_b",
            "Motor burnout time",
            config.motor.burn_time_s,
            "s",
            "scenario.motor.burn_time_s",
        ),
        _parameter(
            "I_total",
            "Total impulse",
            total_impulse,
            "N s",
            "RocketPy motor.total_impulse",
        ),
        _parameter(
            "v_e",
            "Effective exhaust velocity",
            effective_exhaust_velocity,
            "m/s",
            "RocketPy GenericMotor exhaust_velocity",
        ),
        _parameter(
            "L_rail",
            "Effective launch rail length",
            float(flight.effective_1rl),
            "m",
            "RocketPy flight.effective_1rl",
        ),
        _parameter(
            "rho_0",
            "Atmospheric density at launch altitude",
            float(environment.density.get_value_opt(config.environment.elevation_m)),
            "kg/m^3",
            "RocketPy Environment",
        ),
        _parameter(
            "g_0",
            "Gravity at launch altitude",
            float(environment.gravity.get_value_opt(config.environment.elevation_m)),
            "m/s^2",
            "RocketPy Environment",
        ),
        _parameter(
            "solver",
            "ODE solver",
            str(flight.ode_solver),
            "",
            "RocketPy Flight",
        ),
        _parameter(
            "rtol",
            "Relative integration tolerance",
            float(flight.rtol),
            "1",
            "RocketPy Flight",
        ),
        _parameter(
            "dt_max",
            "Maximum integration step",
            float(flight.max_time_step),
            "s",
            "scenario.launch.max_time_step_s",
        ),
    ]
    if rigid_body.tail is not None:
        parameters.append(
            _parameter(
                "L_tail",
                "Tail length",
                rigid_body.tail.length_m,
                "m",
                "scenario.rigid_body.tail.length_m",
            )
        )
    equations = list(_rigid_body_equations())
    events = list(_events(RIGID_FLIGHT_REFERENCE))
    assumptions = [
        "Single-stage rigid vehicle with six-degree-of-freedom motion.",
        (
            "GenericMotor represents propellant as a cylindrical chamber and "
            "derives mass depletion from the thrust curve."
        ),
        (
            "Nose, trapezoidal fins, and tail use RocketPy analytical "
            "aerodynamic-surface models."
        ),
        "Powered and unpowered body drag coefficients are scalar constants.",
        "Thrust values are linearly interpolated between scenario points.",
        "The standard-atmosphere wind is zero for this scenario.",
        "No air brakes, sensors, or active controllers are present.",
    ]
    limitations = [
        "The motor is a GenericMotor approximation, not a grain-resolved motor.",
        "The atmosphere is deterministic and has no measured wind profile.",
        "Earth curvature and orbital dynamics are outside this contract.",
        "Structural flexibility, slosh, and aeroelasticity are not modeled.",
        (
            "The report is curated against the pinned RocketPy implementation; "
            "it is not generated from symbolic source code."
        ),
    ]
    implementation_references = [
        RAIL_REFERENCE,
        RIGID_FLIGHT_REFERENCE,
        GENERIC_MASS_FLOW_REFERENCE,
        TOTAL_MASS_REFERENCE,
        "rocketpy.rocket.aero_surface.nose_cone.NoseCone",
        "rocketpy.rocket.aero_surface.fins.trapezoidal_fins.TrapezoidalFins",
        "rocketpy.rocket.aero_surface.tail.Tail",
        EVENT_REFERENCE,
    ]
    if config.recovery is None:
        assumptions.append("No parachutes are present.")
    else:
        _extend_recovery_manifest(
            config,
            rocket=rocket,
            equations=equations,
            events=events,
            parameters=parameters,
            assumptions=assumptions,
            limitations=limitations,
            implementation_references=implementation_references,
            transition_assumption=(
                "After deployment RocketPy switches to a three-degree-of-"
                "freedom dry-mass parachute descent model."
            ),
            frozen_attitude=True,
        )

    return ModelManifest(
        schema_version=1,
        fidelity="version-pinned documentation projection of runtime equations",
        backend_name="RocketPy",
        backend_version=backend_version,
        model_name=config.name,
        dynamics=config.dynamics,
        coordinate_system=(
            "Local inertial X axis points East.",
            "Local inertial Y axis points North.",
            "Local inertial Z axis points Up; altitude AGL is z minus site elevation.",
            "K(q) maps body-frame vectors to the local inertial frame.",
            "The rocket thrust axis is the body positive z-axis.",
            (
                "Vehicle coordinates use the configured tail-to-nose or "
                "nose-to-tail orientation."
            ),
        ),
        state_vector=_rigid_body_state_vector(),
        initial_state=_initial_state(flight),
        equations=tuple(equations),
        parameters=tuple(parameters),
        input_series=(_thrust_input_series(config),),
        events=tuple(events),
        assumptions=tuple(assumptions),
        limitations=tuple(limitations),
        implementation_references=tuple(implementation_references),
    )


def _extend_recovery_manifest(
    config: ScenarioConfig,
    *,
    rocket: object,
    equations: list[ModelEquation],
    events: list[ModelEvent],
    parameters: list[ModelParameter],
    assumptions: list[str],
    limitations: list[str],
    implementation_references: list[str],
    transition_assumption: str,
    frozen_attitude: bool,
) -> None:
    recovery = config.recovery
    if recovery is None:
        raise ValueError("recovery manifest extension requires recovery configuration")

    equations.append(_parachute_descent_equation())
    events.extend(_recovery_model_events(config))
    assumptions.extend(
        (
            transition_assumption,
            "Only the most recently deployed parachute is active.",
            "Parachute pressure noise is disabled for deterministic execution.",
        )
    )
    if frozen_attitude:
        limitations.append(
            "Quaternion attitude and angular rates are frozen after deployment."
        )
    implementation_references.extend(
        (
            PARACHUTE_REFERENCE,
            "rocketpy.rocket.parachute.Parachute",
        )
    )
    backend_parachutes = {
        str(parachute.name): parachute for parachute in rocket.parachutes
    }
    for parachute in recovery.parachutes:
        backend_parachute = backend_parachutes[parachute.id]
        parameters.extend(
            (
                _parameter(
                    f"cd_s,{parachute.id}",
                    f"{parachute.id} drag area",
                    parachute.cd_s_m2,
                    "m^2",
                    f"scenario.recovery.parachutes.{parachute.id}.cd_s_m2",
                ),
                _parameter(
                    f"lag,{parachute.id}",
                    f"{parachute.id} deployment lag",
                    parachute.lag_s,
                    "s",
                    f"scenario.recovery.parachutes.{parachute.id}.lag_s",
                ),
                _parameter(
                    f"f_s,{parachute.id}",
                    f"{parachute.id} trigger sampling rate",
                    parachute.sampling_rate_hz,
                    "Hz",
                    (f"scenario.recovery.parachutes.{parachute.id}.sampling_rate_hz"),
                ),
                _parameter(
                    f"R,{parachute.id}",
                    f"{parachute.id} derived canopy radius",
                    float(backend_parachute.radius),
                    "m",
                    "RocketPy Parachute",
                ),
                _parameter(
                    f"H,{parachute.id}",
                    f"{parachute.id} derived canopy height",
                    float(backend_parachute.height),
                    "m",
                    "RocketPy Parachute",
                ),
                _parameter(
                    f"C_added,{parachute.id}",
                    f"{parachute.id} added-mass coefficient",
                    float(backend_parachute.added_mass_coefficient),
                    "1",
                    "RocketPy Parachute",
                ),
            )
        )


def _rigid_body_equations() -> tuple[ModelEquation, ...]:
    return (
        ModelEquation(
            id="kinematics",
            name="Translational kinematics",
            phase="all",
            expression="r_dot = v",
            latex=r"\dot{\mathbf r}=\mathbf v",
            explanation="Position is integrated in the local East-North-Up frame.",
            implementation_reference=RIGID_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="mass_properties",
            name="Time-varying mass and inertia",
            phase="all",
            expression=(
                "m_p_dot = -T/v_e; m = m_dry + m_p; "
                "I = I_vehicle + shifted(I_motor + I_propellant)"
            ),
            latex=(
                r"\dot m_p=-\frac{T(t)}{v_e},\quad "
                r"m=m_{\mathrm{dry}}+m_p,\quad "
                r"I(t)=I_{\mathrm{vehicle}}+"
                r"\mathcal P\left(I_{\mathrm{motor}}+I_{\mathrm{propellant}}(t)\right)"
            ),
            explanation=(
                "RocketPy updates total mass, center of mass, inertia, and their "
                "time derivatives as propellant is depleted."
            ),
            implementation_reference=GENERIC_MASS_FLOW_REFERENCE,
        ),
        ModelEquation(
            id="aerodynamic_resultants",
            name="Aerodynamic forces and moments",
            phase="free_flight",
            expression=(
                "[F_a,b, M_a,b] = body_drag + sum(surface_force_moment("
                "v_rel,b, Mach, rho, Re, cp, omega))"
            ),
            latex=(
                r"(\mathbf F_{a,b},\mathbf M_{a,b})="
                r"(\mathbf F_{\mathrm{drag}},\mathbf 0)+"
                r"\sum_i\mathcal A_i(\mathbf v_{\mathrm{rel},i},M,\rho,Re_i,"
                r"\mathbf r_{cp,i},\boldsymbol\omega)"
            ),
            explanation=(
                "RocketPy sums axial body drag with analytical nose, fin, and "
                "tail forces and moments at their centers of pressure."
            ),
            implementation_reference=RIGID_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="coupled_force_terms",
            name="Variable-mass force and moment terms",
            phase="free_flight",
            expression=(
                "T20 = (omega x m r_CM) x omega + omega x T03 + T04 "
                "+ F_g,b + F_a,b; "
                "T21 = (I omega) x omega + T05 omega - F_g,b x r_CM + M_a,b"
            ),
            latex=(
                r"\mathbf T_{20}=(\boldsymbol\omega\times m\mathbf r_{CM})"
                r"\times\boldsymbol\omega+\boldsymbol\omega\times\mathbf T_{03}"
                r"+\mathbf T_{04}+\mathbf F_{g,b}+\mathbf F_{a,b},\quad "
                r"\mathbf T_{21}=(I\boldsymbol\omega)\times\boldsymbol\omega"
                r"+T_{05}\boldsymbol\omega-\mathbf F_{g,b}\times\mathbf r_{CM}"
                r"+\mathbf M_{a,b}"
            ),
            explanation=(
                "T03, T04, and T05 contain mass-flow, center-of-mass, nozzle, "
                "thrust, and inertia-derivative terms from RocketPy."
            ),
            implementation_reference=RIGID_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="rotational_dynamics",
            name="Rigid-body angular acceleration",
            phase="free_flight",
            expression="omega_dot = I_CM^-1 (T21 + T20 x r_CM)",
            latex=(
                r"\dot{\boldsymbol\omega}=I_{CM}^{-1}"
                r"\left(\mathbf T_{21}+\mathbf T_{20}\times\mathbf r_{CM}\right)"
            ),
            explanation=(
                "Aerodynamic moments, fin cant, inertia variation, and mass "
                "offsets drive the three body angular rates."
            ),
            implementation_reference=RIGID_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="quaternion_kinematics",
            name="Quaternion attitude kinematics",
            phase="free_flight",
            expression="q_dot = 0.5 Omega(omega) q",
            latex=(
                r"\dot{\mathbf q}=\frac12"
                r"\begin{bmatrix}"
                r"0&-\omega_1&-\omega_2&-\omega_3\\"
                r"\omega_1&0&\omega_3&-\omega_2\\"
                r"\omega_2&-\omega_3&0&\omega_1\\"
                r"\omega_3&\omega_2&-\omega_1&0"
                r"\end{bmatrix}\mathbf q"
            ),
            explanation="The quaternion evolves from the solved body angular rate.",
            implementation_reference=RIGID_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="translation_6dof",
            name="Coupled translational acceleration",
            phase="free_flight",
            expression=("v_dot = K(q) (T20/m - r_CM x omega_dot) - 2 omega_earth x v"),
            latex=(
                r"\dot{\mathbf v}=K(\mathbf q)"
                r"\left(\frac{\mathbf T_{20}}{m}-"
                r"\mathbf r_{CM}\times\dot{\boldsymbol\omega}\right)"
                r"-2\boldsymbol\omega_E\times\mathbf v"
            ),
            explanation=(
                "Translation is coupled to angular acceleration and includes "
                "RocketPy's local Coriolis term."
            ),
            implementation_reference=RIGID_FLIGHT_REFERENCE,
        ),
        ModelEquation(
            id="rail_constraint",
            name="One-degree-of-freedom rail dynamics",
            phase="rail",
            expression=(
                "b_z = K(q)e_3; "
                "a_parallel = max(0, (T + R_b,z)/m - b_z,z g); "
                "v_dot = b_z a_parallel"
            ),
            latex=(
                r"\mathbf b_z=K(\mathbf q)\mathbf e_3,\quad "
                r"a_\parallel=\max\left(0,\frac{T+R_{b,z}}{m}-b_{z,z}g\right),"
                r"\quad\dot{\mathbf v}=\mathbf b_z a_\parallel"
            ),
            explanation=(
                "The vehicle remains constrained to the rail before switching "
                "to the full 6DOF equations."
            ),
            implementation_reference=RAIL_REFERENCE,
        ),
    )


def _parachute_descent_equation() -> ModelEquation:
    return ModelEquation(
        id="parachute_descent",
        name="Dry-mass parachute descent with added mass",
        phase="recovery",
        expression=(
            "v_rel = v - wind; "
            "m_a = C_added rho (2/3) pi R^2 H; "
            "D = -0.5 rho cd_s ||v_rel|| v_rel; "
            "v_dot = (D + [0,0,-m_dry g])/(m_dry + m_a) "
            "- 2 omega_earth x v; q_dot = 0; omega_dot = 0"
        ),
        latex=(
            r"\mathbf v_r=\mathbf v-\mathbf w,\quad "
            r"m_a=C_{\mathrm{added}}\rho\frac{2}{3}\pi R^2H,\quad "
            r"\mathbf D=-\frac12\rho(C_DS)\lVert\mathbf v_r\rVert\mathbf v_r,"
            r"\quad \dot{\mathbf v}="
            r"\frac{\mathbf D+[0,0,-m_{\mathrm{dry}}g]^T}"
            r"{m_{\mathrm{dry}}+m_a}"
            r"-2\boldsymbol\omega_E\times\mathbf v"
        ),
        explanation=(
            "After canopy inflation RocketPy uses dry vehicle mass plus canopy "
            "added mass. It freezes quaternion and angular-rate derivatives."
        ),
        implementation_reference=PARACHUTE_REFERENCE,
    )


def _recovery_model_events(config: ScenarioConfig) -> tuple[ModelEvent, ...]:
    if config.recovery is None:
        return ()
    events: list[ModelEvent] = []
    trigger_reference = (
        "rocketpy.rocket.parachute.Parachute._Parachute__evaluate_trigger_function"
    )
    for parachute in config.recovery.parachutes:
        if parachute.trigger.kind == "apogee":
            condition = "v_z < 0 at the next trigger sample"
        else:
            condition = f"v_z < 0 and h_AGL < {parachute.trigger.altitude_agl_m:.9g} m"
        events.extend(
            (
                ModelEvent(
                    id=f"parachute_{parachute.id}_trigger",
                    condition=condition,
                    direction="sampled false-to-true transition",
                    action=f"schedule {parachute.id} canopy inflation",
                    implementation_reference=trigger_reference,
                ),
                ModelEvent(
                    id=f"parachute_{parachute.id}_deployment",
                    condition=f"t = t_trigger + {parachute.lag_s:.9g} s",
                    direction="increasing time",
                    action=(f"activate {parachute.id} and switch to parachute descent"),
                    implementation_reference=PARACHUTE_REFERENCE,
                ),
            )
        )
    return tuple(events)


def render_model_report(manifest: ModelManifest) -> str:
    lines = [
        f"# Dynamics Model: {manifest.model_name}",
        "",
        "## Provenance",
        "",
        f"- Backend: `{manifest.backend_name} {manifest.backend_version}`",
        f"- Dynamics contract: `{manifest.dynamics}`",
        f"- Fidelity: {manifest.fidelity}",
        "",
        (
            "This report describes the version-pinned equations used by the selected "
            "backend path. It is intended for audit and review, not as a symbolic "
            "replacement for the backend implementation."
        ),
        "",
        "## Coordinate System",
        "",
    ]
    lines.extend(f"- {item}" for item in manifest.coordinate_system)
    lines.extend(
        [
            "",
            "## State Vector",
            "",
            "| Symbol | Name | Unit | Role |",
            "|---|---|---:|---|",
        ]
    )
    lines.extend(
        f"| `{state.symbol}` | {state.name} | {state.unit or '-'} | {state.role} |"
        for state in manifest.state_vector
    )
    lines.extend(
        [
            "",
            "## Initial State",
            "",
            "| Symbol | Name | Value | Unit | Source |",
            "|---|---|---:|---:|---|",
        ]
    )
    lines.extend(
        f"| `{parameter.symbol}` | {parameter.name} | "
        f"{_format_value(parameter.value)} | {parameter.unit or '-'} | "
        f"{parameter.source} |"
        for parameter in manifest.initial_state
    )
    lines.extend(["", "## Governing Equations", ""])
    for equation in manifest.equations:
        lines.extend(
            [
                f"### {equation.id}: {equation.name}",
                "",
                f"Active phase: `{equation.phase}`",
                "",
                "$$",
                equation.latex,
                "$$",
                "",
                equation.explanation,
                "",
                f"Runtime reference: `{equation.implementation_reference}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Instantiated Parameters",
            "",
            "| Symbol | Name | Value | Unit | Source |",
            "|---|---|---:|---:|---|",
        ]
    )
    lines.extend(
        f"| `{parameter.symbol}` | {parameter.name} | "
        f"{_format_value(parameter.value)} | {parameter.unit or '-'} | "
        f"{parameter.source} |"
        for parameter in manifest.parameters
    )
    if manifest.input_series:
        lines.extend(["", "## Input Series", ""])
    for input_series in manifest.input_series:
        lines.extend(
            [
                f"### {input_series.id}: {input_series.name}",
                "",
                f"Source: `{input_series.source}`",
                "",
                (
                    f"| {input_series.independent_name} "
                    f"({input_series.independent_unit}) | "
                    f"{input_series.dependent_name} "
                    f"({input_series.dependent_unit}) |"
                ),
                "|---:|---:|",
            ]
        )
        lines.extend(
            f"| {independent:.6g} | {dependent:.6g} |"
            for independent, dependent in input_series.samples
        )
        lines.append("")
    lines.extend(
        [
            "",
            "## Event Conditions",
            "",
            "| Event | Condition | Direction | Action |",
            "|---|---|---|---|",
        ]
    )
    lines.extend(
        f"| `{event.id}` | `{event.condition}` | {event.direction} | {event.action} |"
        for event in manifest.events
    )
    lines.extend(["", "## Assumptions", ""])
    lines.extend(f"- {item}" for item in manifest.assumptions)
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in manifest.limitations)
    lines.extend(["", "## Runtime References", ""])
    lines.extend(f"- `{item}`" for item in manifest.implementation_references)
    return "\n".join(lines) + "\n"


def _state_vector() -> tuple[StateVariable, ...]:
    return (
        StateVariable("x", "East position", "m", "integrated translation"),
        StateVariable("y", "North position", "m", "integrated translation"),
        StateVariable(
            "z", "Up position above sea level", "m", "integrated translation"
        ),
        StateVariable("v_x", "East velocity", "m/s", "integrated translation"),
        StateVariable("v_y", "North velocity", "m/s", "integrated translation"),
        StateVariable("v_z", "Up velocity", "m/s", "integrated translation"),
        StateVariable("e_0", "Quaternion scalar", "1", "auxiliary attitude"),
        StateVariable("e_1", "Quaternion x component", "1", "auxiliary attitude"),
        StateVariable("e_2", "Quaternion y component", "1", "auxiliary attitude"),
        StateVariable("e_3", "Quaternion z component", "1", "auxiliary attitude"),
        StateVariable("omega_1", "Body angular rate 1", "rad/s", "fixed at zero"),
        StateVariable("omega_2", "Body angular rate 2", "rad/s", "fixed at zero"),
        StateVariable("omega_3", "Body angular rate 3", "rad/s", "fixed at zero"),
    )


def _rigid_body_state_vector() -> tuple[StateVariable, ...]:
    return (
        StateVariable("x", "East position", "m", "integrated translation"),
        StateVariable("y", "North position", "m", "integrated translation"),
        StateVariable(
            "z", "Up position above sea level", "m", "integrated translation"
        ),
        StateVariable("v_x", "East velocity", "m/s", "integrated translation"),
        StateVariable("v_y", "North velocity", "m/s", "integrated translation"),
        StateVariable("v_z", "Up velocity", "m/s", "integrated translation"),
        StateVariable("e_0", "Quaternion scalar", "1", "integrated attitude"),
        StateVariable("e_1", "Quaternion x component", "1", "integrated attitude"),
        StateVariable("e_2", "Quaternion y component", "1", "integrated attitude"),
        StateVariable("e_3", "Quaternion z component", "1", "integrated attitude"),
        StateVariable("omega_1", "Body angular rate 1", "rad/s", "integrated rotation"),
        StateVariable("omega_2", "Body angular rate 2", "rad/s", "integrated rotation"),
        StateVariable("omega_3", "Body angular rate 3", "rad/s", "integrated rotation"),
    )


def _initial_state(flight: object) -> tuple[ModelParameter, ...]:
    values = flight.initial_solution
    definitions = (
        ("x_0", "Initial East position", "m", 1),
        ("y_0", "Initial North position", "m", 2),
        ("z_0", "Initial altitude above sea level", "m", 3),
        ("v_x0", "Initial East velocity", "m/s", 4),
        ("v_y0", "Initial North velocity", "m/s", 5),
        ("v_z0", "Initial Up velocity", "m/s", 6),
        ("e_00", "Initial quaternion scalar", "1", 7),
        ("e_10", "Initial quaternion x component", "1", 8),
        ("e_20", "Initial quaternion y component", "1", 9),
        ("e_30", "Initial quaternion z component", "1", 10),
        ("omega_10", "Initial body angular rate 1", "rad/s", 11),
        ("omega_20", "Initial body angular rate 2", "rad/s", 12),
        ("omega_30", "Initial body angular rate 3", "rad/s", 13),
    )
    return tuple(
        _parameter(symbol, name, float(values[index]), unit, "RocketPy Flight")
        for symbol, name, unit, index in definitions
    )


def _attitude_equation(config: ScenarioConfig) -> ModelEquation:
    if config.vehicle.weathercock_coefficient == 0:
        return ModelEquation(
            id="attitude",
            name="Frozen auxiliary attitude",
            phase="all",
            expression="q_dot = 0; omega_dot = 0",
            latex=r"\dot{\mathbf q}=\mathbf 0,\quad\dot{\boldsymbol\omega}=\mathbf 0",
            explanation=(
                "The configured weathercock coefficient is zero. The launch "
                "quaternion remains fixed and no torque dynamics are solved."
            ),
            implementation_reference=FREE_FLIGHT_REFERENCE,
        )
    return ModelEquation(
        id="attitude",
        name="Simplified weathercock alignment",
        phase="free_flight",
        expression=(
            "b_z = K(q)e_3; d = -v_f/||v_f||; "
            "omega_cmd,b = K(q)^T unit(b_z x d) k_wc sin(theta); "
            "q_dot = 0.5 Omega(omega_cmd,b) q; omega_dot = 0"
        ),
        latex=(
            r"\mathbf b_z=K(\mathbf q)\mathbf e_3,\quad "
            r"\mathbf d=-\frac{\mathbf v_f}{V_f},\quad "
            r"\boldsymbol\omega_{\mathrm{cmd},b}=K^T"
            r"\frac{\mathbf b_z\times\mathbf d}{\lVert\mathbf b_z\times\mathbf d\rVert}"
            r"k_{\mathrm{wc}}\sin\theta,\quad "
            r"\dot{\mathbf q}=\frac12\Omega(\boldsymbol\omega_{\mathrm{cmd},b})\mathbf q"
        ),
        explanation=(
            "This is a kinematic alignment law, including a separate anti-aligned "
            "branch in RocketPy. It is not rotational rigid-body dynamics."
        ),
        implementation_reference=FREE_FLIGHT_REFERENCE,
    )


def _events(
    free_flight_reference: str = FREE_FLIGHT_REFERENCE,
) -> tuple[ModelEvent, ...]:
    return (
        ModelEvent(
            id="rail_departure",
            condition="||r - r_launch||^2 - L_rail^2 = 0",
            direction="outward crossing",
            action="switch from rail dynamics to free-flight dynamics",
            implementation_reference=EVENT_REFERENCE,
        ),
        ModelEvent(
            id="burnout",
            condition="t = t_b",
            direction="increasing time",
            action="set thrust to zero and switch to C_D,off",
            implementation_reference=free_flight_reference,
        ),
        ModelEvent(
            id="apogee",
            condition="v_z = 0",
            direction="positive to negative",
            action="record apogee using a linearly interpolated root",
            implementation_reference=EVENT_REFERENCE,
        ),
        ModelEvent(
            id="impact",
            condition="z - z_ground = 0",
            direction="downward crossing",
            action="terminate flight at the interpolated ground intersection",
            implementation_reference=EVENT_REFERENCE,
        ),
    )


def _thrust_input_series(config: ScenarioConfig) -> ModelInputSeries:
    return ModelInputSeries(
        id="thrust_curve",
        name="Motor thrust curve",
        independent_name="Time",
        independent_unit="s",
        dependent_name="Thrust",
        dependent_unit="N",
        samples=config.motor.thrust_curve,
        source="scenario.motor.thrust_curve",
    )


def _parameter(
    symbol: str,
    name: str,
    value: float | str,
    unit: str,
    source: str,
) -> ModelParameter:
    return ModelParameter(symbol, name, value, unit, source)


def _format_value(value: float | str) -> str:
    if isinstance(value, float):
        return f"{value:.9g}"
    return value
