from __future__ import annotations

import math
from collections.abc import Mapping

from .aircraft_config import SUPPORTED_AIRCRAFT_MODELS, AircraftFlightConfig
from .models import (
    ModelEquation,
    ModelEvent,
    ModelInputSeries,
    ModelManifest,
    ModelParameter,
    StateVariable,
)

PROPAGATE_REFERENCE = "JSBSim::FGPropagate::Run"
TRANSLATION_REFERENCE = "JSBSim::FGAccelerations::CalculateUVWdot"
ROTATION_REFERENCE = "JSBSim::FGAccelerations::CalculatePQRdot"
AERODYNAMICS_REFERENCE = "JSBSim::FGAerodynamics::Run"
PROPULSION_REFERENCE = "JSBSim::FGPropulsion::Run"
ATMOSPHERE_REFERENCE = "JSBSim::FGStandardAtmosphere::Calculate"
TRIM_REFERENCE = "JSBSim::FGSimplexTrim"


def build_aircraft_model_manifest(
    config: AircraftFlightConfig,
    *,
    runtime: Mapping[str, float | str],
    backend_version: str,
) -> ModelManifest:
    aircraft_model = config.aircraft.model_id
    model_reference = f"jsbsim/aircraft/{aircraft_model}/{aircraft_model}.xml"
    initial_quaternion = _euler_to_quaternion(
        roll_deg=float(runtime["initial_roll_deg"]),
        pitch_deg=float(runtime["initial_pitch_deg"]),
        heading_deg=float(runtime["initial_heading_deg"]),
    )
    initial_state = (
        _parameter("x_N,0", "Initial local north displacement", 0.0, "m", "derived"),
        _parameter("x_E,0", "Initial local east displacement", 0.0, "m", "derived"),
        _parameter(
            "h_0",
            "Initial altitude MSL",
            float(runtime["initial_altitude_msl_m"]),
            "m",
            "JSBSim trimmed state",
        ),
        _parameter(
            "u_0",
            "Initial body forward velocity",
            float(runtime["initial_u_m_s"]),
            "m/s",
            "JSBSim trimmed state",
        ),
        _parameter(
            "v_0",
            "Initial body right velocity",
            float(runtime["initial_v_m_s"]),
            "m/s",
            "JSBSim trimmed state",
        ),
        _parameter(
            "w_0",
            "Initial body down velocity",
            float(runtime["initial_w_m_s"]),
            "m/s",
            "JSBSim trimmed state",
        ),
        *tuple(
            _parameter(
                f"e_{index},0",
                f"Initial attitude quaternion component {index}",
                value,
                "1",
                "derived from JSBSim trimmed Euler attitude",
            )
            for index, value in enumerate(initial_quaternion)
        ),
        _parameter("p(0)", "Initial body roll rate", 0.0, "rad/s", "trim target"),
        _parameter("q(0)", "Initial body pitch rate", 0.0, "rad/s", "trim target"),
        _parameter("r(0)", "Initial body yaw rate", 0.0, "rad/s", "trim target"),
    )
    parameters = (
        _parameter(
            "m_0",
            "Initial aircraft mass",
            float(runtime["mass_kg"]),
            "kg",
            "JSBSim inertia/mass-slugs",
        ),
        _parameter(
            "I_xx",
            "Body roll moment of inertia",
            float(runtime["ixx_kg_m2"]),
            "kg m^2",
            f"{aircraft_model} model mass properties",
        ),
        _parameter(
            "I_yy",
            "Body pitch moment of inertia",
            float(runtime["iyy_kg_m2"]),
            "kg m^2",
            f"{aircraft_model} model mass properties",
        ),
        _parameter(
            "I_zz",
            "Body yaw moment of inertia",
            float(runtime["izz_kg_m2"]),
            "kg m^2",
            f"{aircraft_model} model mass properties",
        ),
        _parameter(
            "S",
            "Wing reference area",
            float(runtime["wing_area_m2"]),
            "m^2",
            model_reference,
        ),
        _parameter(
            "b",
            "Wing span",
            float(runtime["wing_span_m"]),
            "m",
            model_reference,
        ),
        _parameter(
            "c_bar",
            "Mean aerodynamic chord",
            float(runtime["mean_chord_m"]),
            "m",
            model_reference,
        ),
        _parameter(
            "V_C,target",
            "Requested calibrated airspeed",
            config.initial_condition.calibrated_airspeed_m_s,
            "m/s",
            "contract.initial_condition",
        ),
        _parameter(
            "V_C,trim",
            "Trimmed calibrated airspeed",
            float(runtime["trim_calibrated_airspeed_m_s"]),
            "m/s",
            "JSBSim trim result",
        ),
        _parameter(
            "V_T,trim",
            "Trimmed true airspeed",
            float(runtime["trim_true_airspeed_m_s"]),
            "m/s",
            "JSBSim trim result",
        ),
        _parameter(
            "alpha_trim",
            "Trimmed angle of attack",
            float(runtime["trim_alpha_deg"]),
            "deg",
            "JSBSim trim result",
        ),
        _parameter(
            "delta_t,trim",
            "Trimmed throttle command",
            float(runtime["trim_throttle_norm"]),
            "1",
            "JSBSim trim result",
        ),
        _parameter(
            "delta_e,trim",
            "Trimmed pitch trim command",
            float(runtime["trim_pitch_trim_norm"]),
            "1",
            "JSBSim trim result",
        ),
        _parameter(
            "dt",
            "Fixed integration step",
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
    )
    result = ModelManifest(
        schema_version=1,
        fidelity="version-pinned documentation projection of runtime equations",
        backend_name="JSBSim",
        backend_version=backend_version,
        model_name=config.name,
        dynamics=config.dynamics,
        coordinate_system=(
            "Position is reported in a local North-East-Up tangent frame.",
            "Navigation velocity uses North-East-Down axes.",
            "Body axes are Forward-Right-Down.",
            "Euler attitude maps body axes relative to local North-East-Down.",
            "Latitude and longitude use the JSBSim WGS84 Earth model.",
        ),
        state_vector=_state_vector(),
        initial_state=initial_state,
        equations=_equations(aircraft_model),
        parameters=parameters,
        input_series=_control_input_series(config),
        events=_events(config),
        assumptions=(
            (
                f"The bundled JSBSim {aircraft_model} model is used without "
                "XML modification."
            ),
            "A single rigid aircraft body is propagated.",
            "The JSBSim 1976 U.S. Standard Atmosphere is selected.",
            "Wind is constant in the local North-East-Down frame.",
            "The piston engine is running before longitudinal trim.",
            "Control segments are open-loop deltas relative to trim commands.",
            "Control segments do not overlap.",
            "Landing gear remains modeled by the bundled aircraft definition.",
        ),
        limitations=(
            (
                "Bundled JSBSim aircraft definitions are research and "
                "engineering models, not certification data."
            ),
            "The model is not endorsed by the aircraft manufacturer.",
            "No autopilot, guidance law, sensor model, or closed-loop controller is used.",
            (
                "Only the explicitly validated "
                f"{', '.join(SUPPORTED_AIRCRAFT_MODELS)} models are accepted."
            ),
            "Trim targets may be adjusted internally to produce a consistent state.",
            "The report is curated against JSBSim 1.3.1, not symbolically extracted.",
        ),
        implementation_references=(
            model_reference,
            PROPAGATE_REFERENCE,
            TRANSLATION_REFERENCE,
            ROTATION_REFERENCE,
            AERODYNAMICS_REFERENCE,
            PROPULSION_REFERENCE,
            ATMOSPHERE_REFERENCE,
            TRIM_REFERENCE,
        ),
    )
    return result


def _state_vector() -> tuple[StateVariable, ...]:
    return (
        StateVariable("x_N", "Local north displacement", "m", "integrated position"),
        StateVariable("x_E", "Local east displacement", "m", "integrated position"),
        StateVariable("h", "Altitude above mean sea level", "m", "integrated position"),
        StateVariable("u", "Body forward velocity", "m/s", "integrated translation"),
        StateVariable("v", "Body right velocity", "m/s", "integrated translation"),
        StateVariable("w", "Body down velocity", "m/s", "integrated translation"),
        StateVariable("e_0", "Quaternion scalar", "1", "integrated attitude"),
        StateVariable("e_1", "Quaternion x component", "1", "integrated attitude"),
        StateVariable("e_2", "Quaternion y component", "1", "integrated attitude"),
        StateVariable("e_3", "Quaternion z component", "1", "integrated attitude"),
        StateVariable("p", "Body roll rate", "rad/s", "integrated rotation"),
        StateVariable("q", "Body pitch rate", "rad/s", "integrated rotation"),
        StateVariable("r", "Body yaw rate", "rad/s", "integrated rotation"),
    )


def _equations(
    aircraft_model: str,
) -> tuple[ModelEquation, ...]:
    return (
        ModelEquation(
            id="position_kinematics",
            name="Local position kinematics",
            phase="all",
            expression="r_dot_NED = C_b_to_NED(q) v_b",
            latex=r"\dot{\mathbf r}_{NED}=C_b^{NED}(\mathbf q)\mathbf v_b",
            explanation=(
                "JSBSim propagates geodetic position on its rotating WGS84 Earth "
                "and exposes local displacement from the initial location."
            ),
            implementation_reference=PROPAGATE_REFERENCE,
        ),
        ModelEquation(
            id="body_translation",
            name="Body-axis translational dynamics",
            phase="all",
            expression=(
                "m (v_dot_b + omega_b x v_b) = F_aero,b + F_prop,b + F_gravity,b"
            ),
            latex=(
                r"m\left(\dot{\mathbf v}_b+\boldsymbol\omega_b\times\mathbf v_b"
                r"\right)=\mathbf F_{a,b}+\mathbf F_{p,b}+\mathbf F_{g,b}"
            ),
            explanation=(
                "The full nonlinear body-axis translation includes aerodynamic, "
                "propulsive, gravitational, and rotating-Earth contributions."
            ),
            implementation_reference=TRANSLATION_REFERENCE,
        ),
        ModelEquation(
            id="rigid_body_rotation",
            name="Rigid-body rotational dynamics",
            phase="all",
            expression="J omega_dot + omega x (J omega) = M_aero + M_prop",
            latex=(
                r"J\dot{\boldsymbol\omega}+\boldsymbol\omega\times"
                r"(J\boldsymbol\omega)=\mathbf M_a+\mathbf M_p"
            ),
            explanation=(
                "JSBSim integrates roll, pitch, and yaw rates using the current "
                "mass properties and total applied moments."
            ),
            implementation_reference=ROTATION_REFERENCE,
        ),
        ModelEquation(
            id="quaternion_kinematics",
            name="Quaternion attitude kinematics",
            phase="all",
            expression="q_dot = 0.5 Omega(omega) q",
            latex=r"\dot{\mathbf q}=\frac12\Omega(\boldsymbol\omega)\mathbf q",
            explanation=(
                "The internal quaternion is integrated and exposed as Euler angles."
            ),
            implementation_reference=PROPAGATE_REFERENCE,
        ),
        ModelEquation(
            id="aerodynamic_resultants",
            name="Aerodynamic force and moment resultants",
            phase="all",
            expression=(
                "L=qbar S C_L; D=qbar S C_D; Y=qbar S C_Y; "
                "l=qbar S b C_l; m=qbar S c_bar C_m; n=qbar S b C_n"
            ),
            latex=(
                r"L=\bar qSC_L,\ D=\bar qSC_D,\ Y=\bar qSC_Y,\quad "
                r"\ell=\bar qSbC_\ell,\ m=\bar qS\bar cC_m,\ n=\bar qSbC_n"
            ),
            explanation=(
                f"The bundled {aircraft_model} XML combines tabulated and functional "
                "coefficients of aerodynamic angles, rates, and control positions."
            ),
            implementation_reference=AERODYNAMICS_REFERENCE,
        ),
        ModelEquation(
            id="piston_propeller_propulsion",
            name="Piston-engine propeller propulsion",
            phase="all",
            expression="F_prop = engine(throttle, mixture, atmosphere) + propeller(J)",
            latex=(
                r"\mathbf F_p=\mathcal P(\delta_t,\delta_m,\rho,a,V,"
                r"\mathrm{RPM},J)"
            ),
            explanation=(
                "The selected aircraft XML composes its bundled engine and "
                "propeller definitions."
            ),
            implementation_reference=PROPULSION_REFERENCE,
        ),
        ModelEquation(
            id="control_schedule",
            name="Trim-relative open-loop controls",
            phase="scheduled segments",
            expression="delta(t) = delta_trim + delta_segment for t in [start,end)",
            latex=(
                r"\boldsymbol\delta(t)=\boldsymbol\delta_{trim}"
                r"+\Delta\boldsymbol\delta_k,\quad t\in[t_{k,0},t_{k,1})"
            ),
            explanation=(
                "Aileron, elevator, rudder, and throttle deltas are applied at "
                "every fixed integration step."
            ),
            implementation_reference="aerospace_simulator.aircraft_backend",
        ),
    )


def _events(config: AircraftFlightConfig) -> tuple[ModelEvent, ...]:
    events: list[ModelEvent] = [
        ModelEvent(
            id="trim_complete",
            condition="t = 0 after longitudinal trim convergence",
            direction="initialization",
            action="capture trim state and baseline controls",
            implementation_reference=TRIM_REFERENCE,
        )
    ]
    for segment in config.controls.segments:
        events.extend(
            (
                ModelEvent(
                    id=f"control_{segment.id}_start",
                    condition=f"t = {segment.start_time_s:.9g} s",
                    direction="increasing time",
                    action=f"apply trim-relative control segment {segment.id}",
                    implementation_reference="aerospace_simulator.aircraft_backend",
                ),
                ModelEvent(
                    id=f"control_{segment.id}_end",
                    condition=f"t = {segment.end_time_s:.9g} s",
                    direction="increasing time",
                    action="restore trim baseline controls",
                    implementation_reference="aerospace_simulator.aircraft_backend",
                ),
            )
        )
    events.append(
        ModelEvent(
            id="propagation_end",
            condition=f"t = {config.propagation.duration_s:.9g} s",
            direction="increasing time",
            action="terminate fixed-step propagation",
            implementation_reference=PROPAGATE_REFERENCE,
        )
    )
    result = tuple(events)
    return result


def _control_input_series(
    config: AircraftFlightConfig,
) -> tuple[ModelInputSeries, ...]:
    if not config.controls.segments:
        return ()
    definitions = (
        ("aileron_delta", "Aileron command delta", "aileron_delta_norm"),
        ("elevator_delta", "Elevator command delta", "elevator_delta_norm"),
        ("rudder_delta", "Rudder command delta", "rudder_delta_norm"),
        ("throttle_delta", "Throttle command delta", "throttle_delta_norm"),
    )
    series: list[ModelInputSeries] = []
    for series_id, name, attribute in definitions:
        samples: list[tuple[float, float]] = [(0.0, 0.0)]
        for segment in config.controls.segments:
            value = float(getattr(segment, attribute))
            samples.extend(
                (
                    (segment.start_time_s, value),
                    (segment.end_time_s, 0.0),
                )
            )
        if samples[-1][0] != config.propagation.duration_s:
            samples.append((config.propagation.duration_s, 0.0))
        series.append(
            ModelInputSeries(
                id=series_id,
                name=name,
                independent_name="Time",
                independent_unit="s",
                dependent_name="Trim-relative command",
                dependent_unit="1",
                samples=tuple(samples),
                source="contract.controls.segments",
            )
        )
    result = tuple(series)
    return result


def _euler_to_quaternion(
    *,
    roll_deg: float,
    pitch_deg: float,
    heading_deg: float,
) -> tuple[float, float, float, float]:
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    yaw = math.radians(heading_deg)
    cr = math.cos(roll / 2.0)
    sr = math.sin(roll / 2.0)
    cp = math.cos(pitch / 2.0)
    sp = math.sin(pitch / 2.0)
    cy = math.cos(yaw / 2.0)
    sy = math.sin(yaw / 2.0)
    result = (
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    )
    return result


def _parameter(
    symbol: str,
    name: str,
    value: float | str,
    unit: str,
    source: str,
) -> ModelParameter:
    return ModelParameter(symbol, name, value, unit, source)
