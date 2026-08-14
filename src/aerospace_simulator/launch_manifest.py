from __future__ import annotations

import numpy as np

from .launch_config import STANDARD_GRAVITY_M_S2, LaunchToOrbitConfig
from .models import (
    ModelEquation,
    ModelEvent,
    ModelInputSeries,
    ModelManifest,
    ModelParameter,
    StateVariable,
)

TRANSLATIONAL_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.propagator.translational"
)
MASS_REFERENCE = "tudatpy.kernel.dynamics.propagation_setup.propagator.mass"
MULTITYPE_REFERENCE = "tudatpy.kernel.dynamics.propagation_setup.propagator.multitype"
GRAVITY_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.acceleration.spherical_harmonic_gravity"
)
AERODYNAMIC_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.acceleration.aerodynamic"
)
CUSTOM_THRUST_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.acceleration.custom_acceleration"
)
CUSTOM_MASS_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.mass_rate.custom_mass_rate"
)
INTEGRATOR_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.integrator.runge_kutta_fixed_step"
)
ELEMENT_REFERENCE = "tudatpy.kernel.astro.element_conversion.cartesian_to_keplerian"


def build_launch_model_manifest(
    config: LaunchToOrbitConfig,
    *,
    initial_cartesian_state: np.ndarray,
    backend_version: str,
) -> ModelManifest:
    initial_state = np.asarray(initial_cartesian_state, dtype=float)
    if initial_state.shape != (6,):
        raise ValueError("initial_cartesian_state must contain six values")
    state_vector = (
        StateVariable("x", "J2000 x position", "m", "integrated"),
        StateVariable("y", "J2000 y position", "m", "integrated"),
        StateVariable("z", "J2000 z position", "m", "integrated"),
        StateVariable("v_x", "J2000 x velocity", "m/s", "integrated"),
        StateVariable("v_y", "J2000 y velocity", "m/s", "integrated"),
        StateVariable("v_z", "J2000 z velocity", "m/s", "integrated"),
        StateVariable("m", "Launch-vehicle mass", "kg", "integrated"),
    )
    initial_parameters = tuple(
        ModelParameter(symbol, name, float(initial_state[index]), unit, source)
        for index, (symbol, name, unit, source) in enumerate(
            (
                ("x_0", "Initial J2000 x position", "m", "launch site"),
                ("y_0", "Initial J2000 y position", "m", "launch site"),
                ("z_0", "Initial J2000 z position", "m", "launch site"),
                ("v_x0", "Initial J2000 x velocity", "m/s", "Earth rotation"),
                ("v_y0", "Initial J2000 y velocity", "m/s", "Earth rotation"),
                ("v_z0", "Initial J2000 z velocity", "m/s", "Earth rotation"),
            )
        )
    ) + (
        ModelParameter(
            "m_0",
            "Lift-off mass",
            config.lift_off_mass_kg,
            "kg",
            "contract.vehicle + contract.stages",
        ),
    )
    equations = (
        ModelEquation(
            id="launch_translational_kinematics",
            name="Earth-centered launch kinematics",
            phase="all",
            expression="r_dot = v",
            latex=r"\dot{\mathbf r}=\mathbf v",
            explanation=(
                "TudatPy integrates the launch vehicle in the Earth-centered "
                "J2000 frame from the rotating launch-site initial state."
            ),
            implementation_reference=TRANSLATIONAL_REFERENCE,
        ),
        ModelEquation(
            id="launch_j2_gravity",
            name="Earth central and J2 gravity",
            phase="all",
            expression=("a_gravity = grad[mu/r (1 - J2 (R_e/r)^2 P2(sin(phi)))]"),
            latex=(
                r"\mathbf a_g=\nabla\left[\frac{\mu}{r}\left(1-J_2"
                r"\left(\frac{R_e}{r}\right)^2P_2(\sin\phi)\right)\right]"
            ),
            explanation=(
                "TudatPy evaluates a degree-2, order-0 spherical-harmonic "
                "Earth gravity field during powered ascent and orbital coast."
            ),
            implementation_reference=GRAVITY_REFERENCE,
        ),
        ModelEquation(
            id="launch_aerodynamic_drag",
            name="Rotating-atmosphere aerodynamic drag",
            phase="all",
            expression=(
                "rho = rho_0 exp(-h/H); a_D = -rho C_D A ||v_rel|| v_rel / (2m)"
            ),
            latex=(
                r"\rho=\rho_0e^{-h/H},\qquad "
                r"\mathbf a_D=-\frac{\rho C_DA}{2m}"
                r"\lVert\mathbf v_{\rm rel}\rVert\mathbf v_{\rm rel}"
            ),
            explanation=(
                "TudatPy computes air-relative velocity against its rotating "
                "exponential Earth atmosphere."
            ),
            implementation_reference=AERODYNAMIC_REFERENCE,
        ),
        ModelEquation(
            id="guided_stage_thrust",
            name="Pitch-programmed stage thrust",
            phase="powered ascent",
            expression=("a_T = T/m [sin(gamma) e_radial + cos(gamma) e_east]"),
            latex=(
                r"\mathbf a_T=\frac{T}{m}\left(\sin\gamma\,\hat{\mathbf e}_r"
                r"+\cos\gamma\,\hat{\mathbf e}_E\right)"
            ),
            explanation=(
                "Each stage follows its contract-defined piecewise-linear pitch "
                "program in the local radial-east plane."
            ),
            implementation_reference=CUSTOM_THRUST_REFERENCE,
        ),
        ModelEquation(
            id="launch_mass_depletion",
            name="Propellant mass depletion",
            phase="powered ascent",
            expression="m_dot = -T / (Isp g0)",
            latex=r"\dot m=-\frac{T}{I_{\rm sp}g_0}",
            explanation=(
                "TudatPy propagates vehicle mass together with the Cartesian "
                "state. Contract validation closes the propellant mass balance."
            ),
            implementation_reference=CUSTOM_MASS_REFERENCE,
        ),
        ModelEquation(
            id="stage_separation_mass_jump",
            name="Stage-separation dry-mass jettison",
            phase="stage separation",
            expression="m_plus = m_minus - m_dry,jettisoned",
            latex=r"m^+=m^--m_{\rm dry,jettisoned}",
            explanation=(
                "The first-stage dry mass is removed explicitly between TudatPy "
                "propagation arcs while Cartesian state remains continuous."
            ),
            implementation_reference="aerospace_simulator.tudat_launch_worker",
        ),
        ModelEquation(
            id="insertion_orbital_elements",
            name="Insertion Cartesian-to-Keplerian conversion",
            phase="postprocessing",
            expression="(a,e,i,omega,Omega,nu) = K(r,v;mu)",
            latex=(
                r"(a,e,i,\omega,\Omega,\nu)"
                r"=\mathcal K(\mathbf r,\mathbf v;\mu)"
            ),
            explanation=(
                "TudatPy converts the second-stage cutoff state to osculating "
                "elements used by the physical release acceptance."
            ),
            implementation_reference=ELEMENT_REFERENCE,
        ),
    )
    parameters: list[ModelParameter] = [
        ModelParameter(
            "mu",
            "Earth gravitational parameter",
            config.central_body.gravitational_parameter_m3_s2,
            "m^3/s^2",
            "contract.central_body",
        ),
        ModelParameter(
            "R_e",
            "Earth equatorial radius",
            config.central_body.equatorial_radius_m,
            "m",
            "contract.central_body",
        ),
        ModelParameter(
            "omega_e",
            "Earth rotation rate",
            config.central_body.rotation_rate_rad_s,
            "rad/s",
            "contract.central_body",
        ),
        ModelParameter(
            "C_D",
            "Launch vehicle drag coefficient",
            config.vehicle.drag_coefficient,
            "1",
            "contract.vehicle",
        ),
        ModelParameter(
            "A_ref",
            "Launch vehicle reference area",
            config.vehicle.reference_area_m2,
            "m^2",
            "contract.vehicle",
        ),
        ModelParameter(
            "m_payload",
            "Delivered payload mass",
            config.vehicle.payload_mass_kg,
            "kg",
            "contract.vehicle",
        ),
        ModelParameter(
            "g_0",
            "Standard gravity",
            STANDARD_GRAVITY_M_S2,
            "m/s^2",
            "physical constant",
        ),
    ]
    for index, stage in enumerate(config.stages, start=1):
        parameters.extend(
            (
                ModelParameter(
                    f"T_{index}",
                    f"Stage {index} thrust",
                    stage.thrust_n,
                    "N",
                    f"contract.stages[{index - 1}]",
                ),
                ModelParameter(
                    f"Isp_{index}",
                    f"Stage {index} specific impulse",
                    stage.specific_impulse_s,
                    "s",
                    f"contract.stages[{index - 1}]",
                ),
                ModelParameter(
                    f"m_prop_{index}",
                    f"Stage {index} propellant mass",
                    stage.propellant_mass_kg,
                    "kg",
                    f"contract.stages[{index - 1}]",
                ),
                ModelParameter(
                    f"m_dry_{index}",
                    f"Stage {index} dry mass",
                    stage.dry_mass_kg,
                    "kg",
                    f"contract.stages[{index - 1}]",
                ),
            )
        )
    input_series = tuple(
        ModelInputSeries(
            id=f"stage_{index}_pitch_program",
            name=f"Stage {index} pitch guidance",
            independent_name="Stage elapsed time",
            independent_unit="s",
            dependent_name="Pitch above local horizontal",
            dependent_unit="deg",
            samples=tuple(
                (point.elapsed_time_s, point.pitch_deg)
                for point in stage.guidance_pitch_program
            ),
            source=f"contract.stages[{index - 1}].guidance_pitch_program",
        )
        for index, stage in enumerate(config.stages, start=1)
    )
    first_burnout = config.stages[0].burn_time_s
    insertion_time = config.insertion_time_s
    events = (
        ModelEvent(
            id="stage_1_burnout",
            condition=f"t = {first_burnout} s",
            direction="time increasing",
            action="Terminate stage-1 thrust and mass flow.",
            implementation_reference="aerospace_simulator.tudat_launch_worker",
        ),
        ModelEvent(
            id="stage_1_separation",
            condition=f"t = {first_burnout} s",
            direction="time increasing",
            action="Jettison stage-1 dry mass and initialize the next arc.",
            implementation_reference="aerospace_simulator.tudat_launch_worker",
        ),
        ModelEvent(
            id="stage_2_burnout",
            condition=f"t = {insertion_time} s",
            direction="time increasing",
            action="Terminate stage-2 thrust and begin orbital coast.",
            implementation_reference="aerospace_simulator.tudat_launch_worker",
        ),
        ModelEvent(
            id="orbit_verification_end",
            condition=f"t = {config.duration_s} s",
            direction="time increasing",
            action="Stop the post-insertion coast verification.",
            implementation_reference=TRANSLATIONAL_REFERENCE,
        ),
    )
    references = (
        TRANSLATIONAL_REFERENCE,
        MASS_REFERENCE,
        MULTITYPE_REFERENCE,
        GRAVITY_REFERENCE,
        AERODYNAMIC_REFERENCE,
        CUSTOM_THRUST_REFERENCE,
        CUSTOM_MASS_REFERENCE,
        INTEGRATOR_REFERENCE,
        ELEMENT_REFERENCE,
    )
    return ModelManifest(
        schema_version=1,
        fidelity="3DOF point-mass two-stage launch with propagated mass",
        backend_name="TudatPy",
        backend_version=backend_version,
        model_name=config.name,
        dynamics=config.dynamics,
        coordinate_system=(
            "Earth-centered J2000 inertial Cartesian state",
            "Earth-fixed rotating exponential atmosphere",
            "Local radial-east pitch guidance plane",
        ),
        state_vector=state_vector,
        initial_state=initial_parameters,
        equations=equations,
        parameters=tuple(parameters),
        input_series=input_series,
        events=events,
        assumptions=(
            "Each stage has constant vacuum thrust and specific impulse.",
            "Guidance is a prescribed pitch program without closed-loop navigation.",
            "The vehicle is a point mass with constant drag area and coefficient.",
            "Stage separation is instantaneous and preserves Cartesian state.",
        ),
        limitations=(
            "No attitude dynamics, structural loads, winds, or engine throttling.",
            "The atmosphere is exponential rather than a high-fidelity weather model.",
            "The Earth orientation model uses constant rotation without IERS data.",
            "Only an explicit two-stage architecture is supported by this contract.",
        ),
        implementation_references=references,
    )
