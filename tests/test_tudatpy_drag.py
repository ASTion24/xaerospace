from pathlib import Path

import pytest

from aerospace_simulator.request_io import load_request
from aerospace_simulator.simulation import run_request

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "earth_orbit_j2_drag_demo.json"
)


@pytest.fixture(scope="module")
def drag_result():
    return run_request(load_request(SCENARIO_PATH))


def test_j2_drag_variant_executes_real_aerodynamic_decay(drag_result):
    assert drag_result.backend.backend_id == "tudatpy"
    assert drag_result.request.contract.aerodynamics.enabled is True
    assert drag_result.metric("semi_major_axis_change").value < -1000.0
    assert drag_result.metric("max_relative_specific_energy_variation").value > 1e-5
    energy = drag_result.channel("specific_orbital_energy").values
    assert energy[-1] < energy[0]


def test_j2_drag_manifest_exposes_force_and_atmosphere(drag_result):
    manifest = drag_result.model_manifest
    equation_ids = {equation.id for equation in manifest.equations}
    parameters = {
        parameter.symbol: parameter.value for parameter in manifest.parameters
    }

    assert "earth_j2_gravity" in equation_ids
    assert "aerodynamic_drag" in equation_ids
    assert parameters["A_ref"] == pytest.approx(20.0)
    assert parameters["C_D"] == pytest.approx(2.2)
    assert parameters["H"] == pytest.approx(8500.0)
    assert any(
        reference.endswith("acceleration.aerodynamic")
        for reference in manifest.implementation_references
    )
