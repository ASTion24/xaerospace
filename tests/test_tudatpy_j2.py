import math
from pathlib import Path

import numpy as np
import pytest

from aerospace_simulator.request_io import load_request
from aerospace_simulator.simulation import run_request

SCENARIO_PATH = Path(__file__).parents[1] / "scenarios" / "earth_orbit_j2_demo.json"


@pytest.fixture(scope="module")
def j2_result():
    return run_request(load_request(SCENARIO_PATH))


def _first_order_j2_nodal_rate_deg_day(result) -> float:
    config = result.request.contract
    central = config.central_body
    orbit = config.initial_state
    mean_motion = math.sqrt(
        central.gravitational_parameter_m3_s2 / orbit.semi_major_axis_m**3
    )
    rate_rad_s = (
        -1.5
        * central.j2
        * mean_motion
        * (central.equatorial_radius_m / orbit.semi_major_axis_m) ** 2
        * math.cos(math.radians(orbit.inclination_deg))
        / (1.0 - orbit.eccentricity**2) ** 2
    )
    return math.degrees(rate_rad_s) * 86400.0


def test_j2_propagation_produces_expected_secular_nodal_regression(j2_result):
    numerical_rate = j2_result.metric("nodal_precession_rate").value
    analytical_rate = _first_order_j2_nodal_rate_deg_day(j2_result)

    assert numerical_rate < -3.0
    assert numerical_rate == pytest.approx(analytical_rate, rel=0.03)
    assert j2_result.metric("raan_change").value == pytest.approx(
        numerical_rate,
        abs=1e-10,
    )


def test_j2_orbit_has_real_short_period_element_variation(j2_result):
    semi_major_axis = j2_result.channel("semi_major_axis").values
    eccentricity = j2_result.channel("eccentricity").values
    inclination = j2_result.channel("inclination").values

    assert np.ptp(semi_major_axis) > 10000.0
    assert np.ptp(eccentricity) > 1e-3
    assert np.ptp(inclination) > 0.01
    assert j2_result.metric("max_relative_specific_energy_drift").value < 1e-4


def test_j2_manifest_exposes_spherical_harmonic_model(j2_result):
    manifest = j2_result.model_manifest
    equation_ids = {equation.id for equation in manifest.equations}
    parameters = {
        parameter.symbol: parameter.value for parameter in manifest.parameters
    }

    assert "earth_j2_gravity" in equation_ids
    assert parameters["J2"] == pytest.approx(1.08262668e-3)
    assert parameters["R_e"] == pytest.approx(6378137.0)
    assert any(
        reference.endswith("acceleration.spherical_harmonic_gravity")
        for reference in manifest.implementation_references
    )
