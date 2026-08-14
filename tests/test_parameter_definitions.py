import pytest

from aerospace_simulator.parameter_definitions import (
    ParameterCatalog,
    ParameterDefinitionError,
    parameter_catalog,
)


def test_parameter_catalog_is_typed_and_returns_independent_documents():
    catalog = parameter_catalog()
    first = catalog.document(family_id="orbit_propagation")
    first["fields"]["eccentricity"]["en"]["label"] = "mutated"
    second = catalog.document(family_id="orbit_propagation")

    assert catalog.version == 1
    assert len(catalog.sections) == 23
    assert len(catalog.fields) == 114
    assert second["fields"]["eccentricity"]["en"]["label"] == ("Orbit Eccentricity")
    assert list(second["basicSections"]) == ["orbit_propagation"]


def test_parameter_catalog_rejects_inconsistent_bounds():
    with pytest.raises(
        ParameterDefinitionError,
        match="maxExclusive requires a maximum",
    ):
        ParameterCatalog.from_mapping(
            {
                "version": 1,
                "sections": {
                    "section": {
                        "zh-CN": {"label": "分组", "help": "说明"},
                        "en": {"label": "Section", "help": "Help"},
                    }
                },
                "fields": {
                    "value": {
                        "zh-CN": {"label": "值", "help": "说明"},
                        "en": {"label": "Value", "help": "Help"},
                        "unit": "",
                        "maxExclusive": True,
                    }
                },
                "basicSections": {"family": ["section"]},
                "recommendedPaths": {"family": []},
            }
        )
