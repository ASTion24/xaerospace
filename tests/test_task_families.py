from pathlib import Path

import pytest

from aerospace_simulator.protocol import BackendCapabilities
from aerospace_simulator.request_io import load_request
from aerospace_simulator.simulation import create_default_registry
from aerospace_simulator.task_families import (
    TaskFamilyNotFoundError,
    TaskFamilyRegistry,
    TaskFamilyRegistryError,
    create_default_task_family_registry,
)

SCENARIO_DIR = Path(__file__).parents[1] / "scenarios"


def test_default_registry_groups_existing_tasks_into_five_families():
    registry = create_default_task_family_registry()
    families = registry.families()

    assert {family.family_id for family in families} == {
        "aircraft_flight",
        "launch_to_orbit",
        "orbit_propagation",
        "rocket_flight",
        "spacecraft_gnc",
    }
    assert sum(len(family.variants) for family in families) == 16
    assert registry.get("rocket_flight").default_variant_id == ("point_mass_3dof")
    assert {
        variant.variant_id for variant in registry.get("rocket_flight").variants
    } == {
        "point_mass_3dof",
        "point_mass_3dof_recovery",
        "rigid_body_6dof",
        "rigid_body_6dof_recovery",
    }
    assert all(
        variant.assistant is not None
        for family in families
        for variant in family.variants
    )
    assert all(family.assistant_parameters for family in families)
    assert {
        parameter.path
        for parameter in registry.get("orbit_propagation").assistant_parameters
    } >= {
        "initial_state.semi_major_axis_m",
        "propagation.duration_s",
    }


def test_every_bundled_scenario_maps_to_one_family_and_variant():
    registry = create_default_task_family_registry()

    descriptions = [
        registry.describe_request(load_request(path))
        for path in sorted(SCENARIO_DIR.glob("*.json"))
    ]

    assert len(descriptions) == 17
    assert {item["family_id"] for item in descriptions} == {
        "aircraft_flight",
        "launch_to_orbit",
        "orbit_propagation",
        "rocket_flight",
        "spacecraft_gnc",
    }
    assert all(item["component_ids"] for item in descriptions)
    assert {item["variant_id"] for item in descriptions} == {
        "c172p_trimmed_6dof",
        "c172r_trimmed_6dof",
        "c182_trimmed_6dof",
        "c310_trimmed_6dof",
        "j3cub_trimmed_6dof",
        "earth_j2",
        "earth_j2_aerodynamic_drag",
        "earth_two_body",
        "inertial_pointing_rw",
        "point_mass_3dof",
        "point_mass_3dof_recovery",
        "reaction_wheel_rate_damping",
        "rigid_body_6dof",
        "rigid_body_6dof_recovery",
        "uncontrolled_attitude_rw",
        "two_stage_220km_reference",
    }


def test_family_schema_exposes_variants_and_component_extensions():
    registry = create_default_task_family_registry()
    schema = registry.get("orbit_propagation").schema_document()

    assert schema["$id"] == ("wms.aerospace.family.orbit_propagation.v2")
    assert schema["x-wms-family-id"] == "orbit_propagation"
    assert schema["properties"]["dynamics"]["enum"] == [
        "earth_orbit_two_body",
        "earth_orbit_j2",
    ]
    assert schema["properties"]["backend"]["enum"] == ["tudatpy"]
    assert schema["x-wms-assistant-parameters"]
    assert schema["x-wms-parameter-definitions-url"] == ("/api/parameter-definitions")
    assert schema["x-wms-parameter-definitions"]["recommendedPaths"][
        "orbit_propagation"
    ]
    assert {item["component_id"] for item in schema["x-wms-components"]} == {
        "orbit.gravity.point_mass",
        "orbit.gravity.spherical_harmonic_j2",
        "orbit.environment.exponential_atmosphere",
        "orbit.force.aerodynamic_drag",
        "orbit.propagator.rk4_fixed",
    }


def test_family_registry_cross_checks_backend_component_declarations():
    family_registry = create_default_task_family_registry()
    backend_registry = create_default_registry()

    family_registry.validate_backend_capabilities(backend_registry.capabilities())

    incomplete = BackendCapabilities(
        backend_id="rocketpy",
        backend_name="RocketPy",
        backend_version="test",
        supported_task_kinds=(
            "single_stage_point_mass_3dof",
            "single_stage_point_mass_3dof_recovery",
            "single_stage_rigid_body_6dof",
            "single_stage_rigid_body_6dof_recovery",
        ),
        supported_contract_schemas=("wms.aerospace.scenario.v1",),
        supported_family_ids=("rocket_flight",),
        supported_component_ids=("rocket.fidelity.point_mass_3dof",),
    )
    with pytest.raises(
        TaskFamilyRegistryError,
        match="missing components",
    ):
        family_registry.validate_backend_capabilities((incomplete,))


def test_unknown_family_and_variant_fail_closed():
    registry = create_default_task_family_registry()

    with pytest.raises(TaskFamilyNotFoundError, match="not found"):
        registry.get("missing")
    with pytest.raises(TaskFamilyNotFoundError, match="no variant"):
        registry.get("rocket_flight").variant("missing")

    duplicate_registry = TaskFamilyRegistry()
    family = registry.get("rocket_flight")
    duplicate_registry.register(family)
    with pytest.raises(TaskFamilyRegistryError, match="already registered"):
        duplicate_registry.register(family)
