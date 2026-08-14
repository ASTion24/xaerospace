from pathlib import Path

import pytest

from aerospace_simulator.request_io import load_request
from aerospace_simulator.simulation import create_default_registry, run_request
from aerospace_simulator.task_families import create_default_task_family_registry

SCENARIO_DIR = Path(__file__).parents[1] / "scenarios"


def _release_cases():
    family_registry = create_default_task_family_registry()
    cases = {}
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        request = load_request(path)
        route = family_registry.describe_request(request)
        key = (str(route["family_id"]), str(route["variant_id"]))
        cases.setdefault(key, (path, request.backend_preference))
    if len(cases) != 16:
        raise AssertionError(
            f"release matrix must contain 16 variants, found {len(cases)}"
        )
    return [
        pytest.param(
            path,
            family_id,
            variant_id,
            backend_id,
            id=f"{family_id}-{variant_id}",
        )
        for (family_id, variant_id), (path, backend_id) in sorted(cases.items())
    ]


@pytest.mark.release_gate
@pytest.mark.parametrize(
    ("scenario_path", "family_id", "variant_id", "backend_id"),
    _release_cases(),
)
def test_every_registered_starter_executes_real_backend(
    scenario_path,
    family_id,
    variant_id,
    backend_id,
):
    request = load_request(scenario_path)
    backend_registry = create_default_registry()
    family_registry = create_default_task_family_registry()

    route = family_registry.describe_request(request)
    result = run_request(request, registry=backend_registry)

    assert route["family_id"] == family_id
    assert route["variant_id"] == variant_id
    assert result.backend.backend_id == backend_id
    assert len(result.time_s) > 1
    assert result.channels
    assert result.metrics
    assert any(
        diagnostic.code == "backend_contract_executed"
        for diagnostic in result.diagnostics
    )
    if family_id == "launch_to_orbit":
        assert result.metric("insertion_periapsis_altitude").value > 180_000.0
        assert result.metric("insertion_apoapsis_altitude").value > 180_000.0
        assert result.metric("insertion_eccentricity").value < 0.005
        assert abs(result.metric("mass_balance_error").value) < 1e-6
        assert [event.name for event in result.events] == [
            "liftoff",
            "stage_1_burnout",
            "stage_1_separation",
            "stage_2_ignition",
            "stage_2_burnout",
            "orbital_insertion",
            "orbit_verification_end",
        ]
        assert any(
            diagnostic.code == "target_orbit_verified"
            for diagnostic in result.diagnostics
        )
