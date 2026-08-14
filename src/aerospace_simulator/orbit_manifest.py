from __future__ import annotations

import numpy as np

from .models import (
    ModelEquation,
    ModelEvent,
    ModelManifest,
    ModelParameter,
    StateVariable,
)
from .orbit_config import OrbitPropagationConfig

POINT_MASS_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.acceleration.point_mass_gravity"
)
SPHERICAL_HARMONIC_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.acceleration.spherical_harmonic_gravity"
)
GRAVITY_FIELD_REFERENCE = (
    "tudatpy.kernel.dynamics.environment_setup.gravity_field.spherical_harmonic"
)
AERODYNAMIC_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.acceleration.aerodynamic"
)
ATMOSPHERE_REFERENCE = (
    "tudatpy.kernel.dynamics.environment_setup.atmosphere.exponential"
)
INTEGRATOR_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.integrator.runge_kutta_fixed_step"
)
PROPAGATOR_REFERENCE = (
    "tudatpy.kernel.dynamics.propagation_setup.propagator.translational"
)
SIMULATOR_REFERENCE = "tudatpy.kernel.dynamics.simulator.create_dynamics_simulator"
ELEMENT_CONVERSION_REFERENCE = (
    "tudatpy.kernel.astro.element_conversion.cartesian_to_keplerian"
)


def build_orbit_model_manifest(
    config: OrbitPropagationConfig,
    *,
    initial_cartesian_state: np.ndarray,
    backend_version: str,
) -> ModelManifest:
    equations = [
        ModelEquation(
            id="translational_kinematics",
            name="Earth-centered translational kinematics",
            phase="all",
            expression="r_dot = v",
            latex=r"\dot{\mathbf r}=\mathbf v",
            explanation=(
                "The spacecraft Cartesian position is integrated in the inertial "
                "J2000 frame."
            ),
            implementation_reference=PROPAGATOR_REFERENCE,
        )
    ]
    references = [
        PROPAGATOR_REFERENCE,
        INTEGRATOR_REFERENCE,
        SIMULATOR_REFERENCE,
        ELEMENT_CONVERSION_REFERENCE,
    ]
    if config.dynamics == "earth_orbit_j2":
        equations.append(
            ModelEquation(
                id="earth_j2_gravity",
                name="Earth central and J2 gravity",
                phase="all",
                expression=(
                    "U = mu/r [1 - J2 (R_e/r)^2 P2(sin(phi))]; v_dot = R_I<-B grad_B(U)"
                ),
                latex=(
                    r"U=\frac{\mu}{r}\left[1-J_2"
                    r"\left(\frac{R_e}{r}\right)^2P_2(\sin\phi)\right],\quad "
                    r"\dot{\mathbf v}=\mathbf R^{I/B}\nabla^B U"
                ),
                explanation=(
                    "TudatPy evaluates a fully normalized degree-2, order-0 "
                    "spherical-harmonic field with C20=-J2/sqrt(5)."
                ),
                implementation_reference=SPHERICAL_HARMONIC_REFERENCE,
            )
        )
        references.extend((GRAVITY_FIELD_REFERENCE, SPHERICAL_HARMONIC_REFERENCE))
    else:
        equations.append(
            ModelEquation(
                id="earth_point_mass_gravity",
                name="Earth point-mass gravity",
                phase="all",
                expression="v_dot = -mu r / ||r||^3",
                latex=(
                    r"\dot{\mathbf v}=-\frac{\mu}{\lVert\mathbf r\rVert^3}"
                    r"\mathbf r"
                ),
                explanation=(
                    "Only Earth's point-mass attraction is applied. No other "
                    "acceleration is present."
                ),
                implementation_reference=POINT_MASS_REFERENCE,
            )
        )
        references.append(POINT_MASS_REFERENCE)
    if config.aerodynamics.enabled:
        equations.append(
            ModelEquation(
                id="aerodynamic_drag",
                name="Atmospheric aerodynamic drag",
                phase="all",
                expression=(
                    "rho(h) = rho_0 exp(-h/H); "
                    "a_drag = -rho ||v_rel||^2 C_D A v_hat_rel / (2 m)"
                ),
                latex=(
                    r"\rho(h)=\rho_0e^{-h/H},\qquad "
                    r"\mathbf a_D=-\frac{\rho C_DA}{2m}"
                    r"\lVert\mathbf v_{\mathrm{rel}}\rVert"
                    r"\mathbf v_{\mathrm{rel}}"
                ),
                explanation=(
                    "TudatPy evaluates drag relative to the rotating exponential "
                    "Earth atmosphere using the configured constant coefficient."
                ),
                implementation_reference=AERODYNAMIC_REFERENCE,
            )
        )
        references.extend((ATMOSPHERE_REFERENCE, AERODYNAMIC_REFERENCE))
    if config.dynamics == "earth_orbit_j2":
        energy_expression = "epsilon = ||v||^2 / 2 - mu/r [1 - J2 (R_e/r)^2 P2(z/r)]"
        energy_latex = (
            r"\epsilon=\frac{\lVert\mathbf v\rVert^2}{2}"
            r"-\frac{\mu}{r}\left[1-J_2"
            r"\left(\frac{R_e}{r}\right)^2P_2\left(\frac{z}{r}\right)\right]"
        )
    else:
        energy_expression = "epsilon = ||v||^2 / 2 - mu / ||r||"
        energy_latex = (
            r"\epsilon=\frac{\lVert\mathbf v\rVert^2}{2}"
            r"-\frac{\mu}{\lVert\mathbf r\rVert}"
        )
    equations.extend(
        (
            ModelEquation(
                id="specific_orbital_energy",
                name="Specific mechanical energy diagnostic",
                phase="postprocessing",
                expression=energy_expression,
                latex=energy_latex,
                explanation=(
                    "The diagnostic uses the same point-mass or J2 gravitational "
                    "potential as the selected gravity model. With drag enabled, "
                    "its decrease is physical rather than integration drift."
                ),
                implementation_reference="aerospace_simulator.orbit_backend",
            ),
            ModelEquation(
                id="specific_angular_momentum",
                name="Specific angular momentum diagnostic",
                phase="postprocessing",
                expression="h = ||r x v||",
                latex=r"h=\lVert\mathbf r\times\mathbf v\rVert",
                explanation=(
                    "The magnitude is reported as a diagnostic. It is conserved "
                    "for central gravity and may vary under J2 or aerodynamic drag."
                ),
                implementation_reference="aerospace_simulator.orbit_backend",
            ),
            ModelEquation(
                id="osculating_keplerian_elements",
                name="Osculating Keplerian element conversion",
                phase="postprocessing",
                expression="(a,e,i,omega,Omega,nu) = cartesian_to_keplerian(r,v,mu)",
                latex=(
                    r"(a,e,i,\omega,\Omega,\nu)"
                    r"=\mathcal K(\mathbf r,\mathbf v;\mu)"
                ),
                explanation=(
                    "TudatPy converts each sampled Cartesian state to osculating "
                    "Keplerian elements. RAAN is unwrapped for secular-trend review."
                ),
                implementation_reference=ELEMENT_CONVERSION_REFERENCE,
            ),
        )
    )
    initial_cartesian = np.asarray(initial_cartesian_state, dtype=float)
    if initial_cartesian.shape != (6,):
        raise ValueError("initial_cartesian_state must contain six values")
    initial_state = tuple(
        ModelParameter(symbol, name, float(initial_cartesian[index]), unit, source)
        for index, (symbol, name, unit, source) in enumerate(
            (
                ("x_0", "Initial J2000 x position", "m", "TudatPy conversion"),
                ("y_0", "Initial J2000 y position", "m", "TudatPy conversion"),
                ("z_0", "Initial J2000 z position", "m", "TudatPy conversion"),
                ("v_x0", "Initial J2000 x velocity", "m/s", "TudatPy conversion"),
                ("v_y0", "Initial J2000 y velocity", "m/s", "TudatPy conversion"),
                ("v_z0", "Initial J2000 z velocity", "m/s", "TudatPy conversion"),
            )
        )
    )
    central = config.central_body
    orbit = config.initial_state
    propagation = config.propagation
    parameters = (
        ModelParameter(
            "mu",
            "Earth gravitational parameter",
            central.gravitational_parameter_m3_s2,
            "m^3/s^2",
            "contract.central_body",
        ),
        ModelParameter(
            "R_e",
            "Earth equatorial reference radius",
            central.equatorial_radius_m,
            "m",
            "contract.central_body",
        ),
        ModelParameter(
            "J2",
            "Earth second zonal harmonic",
            central.j2,
            "1",
            "contract.central_body",
        ),
        ModelParameter(
            "omega_e",
            "Earth rotation rate",
            central.rotation_rate_rad_s,
            "rad/s",
            "contract.central_body",
        ),
        ModelParameter(
            "m_sc",
            "Spacecraft mass",
            config.spacecraft.mass_kg,
            "kg",
            "contract.spacecraft",
        ),
        ModelParameter(
            "a_0",
            "Initial semi-major axis",
            orbit.semi_major_axis_m,
            "m",
            "contract.initial_state",
        ),
        ModelParameter(
            "e_0",
            "Initial eccentricity",
            orbit.eccentricity,
            "1",
            "contract.initial_state",
        ),
        ModelParameter(
            "i_0",
            "Initial inclination",
            orbit.inclination_deg,
            "deg",
            "contract.initial_state",
        ),
        ModelParameter(
            "omega_0",
            "Initial argument of periapsis",
            orbit.argument_of_periapsis_deg,
            "deg",
            "contract.initial_state",
        ),
        ModelParameter(
            "Omega_0",
            "Initial right ascension of ascending node",
            orbit.raan_deg,
            "deg",
            "contract.initial_state",
        ),
        ModelParameter(
            "nu_0",
            "Initial true anomaly",
            orbit.true_anomaly_deg,
            "deg",
            "contract.initial_state",
        ),
        ModelParameter(
            "t_0",
            "Start epoch since J2000 TDB",
            propagation.start_epoch_s_since_j2000,
            "s",
            "contract.propagation",
        ),
        ModelParameter(
            "Delta_t",
            "Propagation duration",
            propagation.duration_s,
            "s",
            "contract.propagation",
        ),
        ModelParameter(
            "dt",
            "Fixed RK4 integration step",
            propagation.step_size_s,
            "s",
            "contract.propagation",
        ),
        ModelParameter(
            "dt_out",
            "Output sampling interval",
            propagation.output_interval_s,
            "s",
            "contract.propagation",
        ),
        *(
            (
                ModelParameter(
                    "A_ref",
                    "Aerodynamic reference area",
                    config.aerodynamics.reference_area_m2,
                    "m^2",
                    "contract.aerodynamics",
                ),
                ModelParameter(
                    "C_D",
                    "Constant drag coefficient",
                    config.aerodynamics.drag_coefficient,
                    "1",
                    "contract.aerodynamics",
                ),
                ModelParameter(
                    "H",
                    "Exponential atmosphere scale height",
                    config.aerodynamics.atmosphere_scale_height_m,
                    "m",
                    "contract.aerodynamics",
                ),
                ModelParameter(
                    "rho_0",
                    "Exponential atmosphere surface density",
                    config.aerodynamics.atmosphere_surface_density_kg_m3,
                    "kg/m^3",
                    "contract.aerodynamics",
                ),
            )
            if config.aerodynamics.enabled
            else ()
        ),
    )
    assumptions = [
        "The spacecraft is a mass point and its mass remains constant.",
        "The central body constants are supplied explicitly by the contract.",
        "The initial epoch is expressed as seconds since J2000 TDB.",
        "A fixed-step classical fourth-order Runge-Kutta integrator is used.",
        "Earth point-mass gravity is enabled for earth_orbit_two_body.",
        "Earth degree-2 order-0 gravity is enabled for earth_orbit_j2.",
    ]
    limitations = [
        "Third-body gravity is not modeled.",
        "Solar radiation pressure and relativistic effects are not modeled.",
        "Finite burns, impulsive maneuvers, attitude, and GNC are not modeled.",
        "Altitude is radial distance minus the equatorial reference radius.",
        (
            "The report is curated against the pinned TudatPy implementation; "
            "it is not generated from symbolic source code."
        ),
    ]
    if config.aerodynamics.enabled:
        assumptions.append(
            "Atmospheric density is spherical, exponential, and co-rotates with Earth."
        )
        limitations.append(
            "Drag uses a constant coefficient and area without attitude coupling."
        )
    else:
        limitations.append("Atmospheric drag is disabled by the contract.")
    result = ModelManifest(
        schema_version=1,
        fidelity="version-pinned documentation projection of runtime equations",
        backend_name="TudatPy",
        backend_version=backend_version,
        model_name=config.name,
        dynamics=config.dynamics,
        coordinate_system=(
            "Origin is Earth's center of mass.",
            "Axes use the inertial J2000 orientation.",
            "Cartesian positions and velocities are Earth-relative.",
            (
                "The J2 field is axisymmetric about the Earth-fixed z-axis; the "
                "configured simple rotation model supplies the required frame link."
            ),
        ),
        state_vector=(
            StateVariable("x", "J2000 x position", "m", "integrated translation"),
            StateVariable("y", "J2000 y position", "m", "integrated translation"),
            StateVariable("z", "J2000 z position", "m", "integrated translation"),
            StateVariable("v_x", "J2000 x velocity", "m/s", "integrated translation"),
            StateVariable("v_y", "J2000 y velocity", "m/s", "integrated translation"),
            StateVariable("v_z", "J2000 z velocity", "m/s", "integrated translation"),
        ),
        initial_state=initial_state,
        equations=tuple(equations),
        parameters=parameters,
        input_series=(),
        events=(
            ModelEvent(
                id="propagation_start",
                condition="t = t_0",
                direction="increasing time",
                action="initialize Cartesian state from Keplerian elements",
                implementation_reference=ELEMENT_CONVERSION_REFERENCE,
            ),
            ModelEvent(
                id="propagation_end",
                condition="t = t_0 + Delta_t",
                direction="increasing time",
                action="terminate exactly at the requested final epoch",
                implementation_reference=PROPAGATOR_REFERENCE,
            ),
        ),
        assumptions=tuple(assumptions),
        limitations=tuple(limitations),
        implementation_references=tuple(references),
    )
    return result
