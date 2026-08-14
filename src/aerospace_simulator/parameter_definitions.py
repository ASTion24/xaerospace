from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any


class ParameterDefinitionError(RuntimeError):
    """Raised when bundled parameter metadata is incomplete or inconsistent."""


@dataclass(frozen=True)
class LocalizedParameterText:
    label: str
    help: str

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        path: str,
    ) -> LocalizedParameterText:
        if not isinstance(value, dict):
            raise ParameterDefinitionError(f"{path} must be an object")
        label = value.get("label")
        help_text = value.get("help")
        if not isinstance(label, str) or not label.strip():
            raise ParameterDefinitionError(f"{path}.label must not be empty")
        if not isinstance(help_text, str) or not help_text.strip():
            raise ParameterDefinitionError(f"{path}.help must not be empty")
        return cls(label=label, help=help_text)

    def document(self) -> dict[str, str]:
        return {"label": self.label, "help": self.help}


@dataclass(frozen=True)
class ParameterSectionSpec:
    zh_cn: LocalizedParameterText
    en: LocalizedParameterText

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        path: str,
    ) -> ParameterSectionSpec:
        if not isinstance(value, dict):
            raise ParameterDefinitionError(f"{path} must be an object")
        return cls(
            zh_cn=LocalizedParameterText.from_mapping(
                value.get("zh-CN"),
                path=f"{path}.zh-CN",
            ),
            en=LocalizedParameterText.from_mapping(
                value.get("en"),
                path=f"{path}.en",
            ),
        )

    def document(self) -> dict[str, object]:
        return {
            "zh-CN": self.zh_cn.document(),
            "en": self.en.document(),
        }


@dataclass(frozen=True)
class ParameterFieldSpec:
    zh_cn: LocalizedParameterText
    en: LocalizedParameterText
    unit: str
    minimum: float | None = None
    maximum: float | None = None
    minimum_exclusive: bool = False
    maximum_exclusive: bool = False
    input_minimum: float | None = None
    input_maximum: float | None = None
    step: float | str | None = None

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        path: str,
    ) -> ParameterFieldSpec:
        if not isinstance(value, dict):
            raise ParameterDefinitionError(f"{path} must be an object")
        allowed = {
            "zh-CN",
            "en",
            "unit",
            "min",
            "max",
            "minExclusive",
            "maxExclusive",
            "inputMin",
            "inputMax",
            "step",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ParameterDefinitionError(
                f"{path} contains unknown fields: {', '.join(sorted(unknown))}"
            )
        unit = value.get("unit", "")
        if not isinstance(unit, str):
            raise ParameterDefinitionError(f"{path}.unit must be a string")
        minimum = _optional_number(value.get("min"), f"{path}.min")
        maximum = _optional_number(value.get("max"), f"{path}.max")
        input_minimum = _optional_number(value.get("inputMin"), f"{path}.inputMin")
        input_maximum = _optional_number(value.get("inputMax"), f"{path}.inputMax")
        minimum_exclusive = _boolean(
            value.get("minExclusive", False),
            f"{path}.minExclusive",
        )
        maximum_exclusive = _boolean(
            value.get("maxExclusive", False),
            f"{path}.maxExclusive",
        )
        step = value.get("step")
        if step is not None and (
            isinstance(step, bool) or not isinstance(step, (int, float, str))
        ):
            raise ParameterDefinitionError(f"{path}.step must be a number or string")
        if minimum is not None and maximum is not None and minimum >= maximum:
            raise ParameterDefinitionError(f"{path} has an invalid numeric range")
        if minimum_exclusive and minimum is None:
            raise ParameterDefinitionError(f"{path}.minExclusive requires a minimum")
        if maximum_exclusive and maximum is None:
            raise ParameterDefinitionError(f"{path}.maxExclusive requires a maximum")
        if (
            input_minimum is not None
            and minimum is not None
            and input_minimum < minimum
        ):
            raise ParameterDefinitionError(f"{path}.inputMin is below its minimum")
        if (
            input_maximum is not None
            and maximum is not None
            and input_maximum > maximum
        ):
            raise ParameterDefinitionError(f"{path}.inputMax exceeds its maximum")
        return cls(
            zh_cn=LocalizedParameterText.from_mapping(
                value.get("zh-CN"),
                path=f"{path}.zh-CN",
            ),
            en=LocalizedParameterText.from_mapping(
                value.get("en"),
                path=f"{path}.en",
            ),
            unit=unit,
            minimum=minimum,
            maximum=maximum,
            minimum_exclusive=minimum_exclusive,
            maximum_exclusive=maximum_exclusive,
            input_minimum=input_minimum,
            input_maximum=input_maximum,
            step=step,
        )

    def document(self) -> dict[str, object]:
        result: dict[str, object] = {
            "zh-CN": self.zh_cn.document(),
            "en": self.en.document(),
            "unit": self.unit,
        }
        optional = (
            ("min", self.minimum),
            ("max", self.maximum),
            ("inputMin", self.input_minimum),
            ("inputMax", self.input_maximum),
            ("step", self.step),
        )
        result.update({key: value for key, value in optional if value is not None})
        if self.minimum_exclusive:
            result["minExclusive"] = True
        if self.maximum_exclusive:
            result["maxExclusive"] = True
        return result


@dataclass(frozen=True)
class ParameterCatalog:
    version: int
    sections: dict[str, ParameterSectionSpec]
    fields: dict[str, ParameterFieldSpec]
    basic_sections: dict[str, tuple[str, ...]]
    recommended_paths: dict[str, tuple[str, ...]]

    @classmethod
    def from_mapping(cls, value: object) -> ParameterCatalog:
        if not isinstance(value, dict):
            raise ParameterDefinitionError("parameter catalog must be an object")
        version = value.get("version")
        if version != 1:
            raise ParameterDefinitionError("parameter catalog version must be 1")
        sections_raw = _mapping(value.get("sections"), "sections")
        fields_raw = _mapping(value.get("fields"), "fields")
        basic_raw = _mapping(value.get("basicSections"), "basicSections")
        recommended_raw = _mapping(
            value.get("recommendedPaths"),
            "recommendedPaths",
        )
        sections = {
            key: ParameterSectionSpec.from_mapping(item, path=f"sections.{key}")
            for key, item in sections_raw.items()
        }
        fields = {
            key: ParameterFieldSpec.from_mapping(item, path=f"fields.{key}")
            for key, item in fields_raw.items()
        }
        basic_sections = {
            key: _string_tuple(item, f"basicSections.{key}")
            for key, item in basic_raw.items()
        }
        recommended_paths = {
            key: _string_tuple(item, f"recommendedPaths.{key}")
            for key, item in recommended_raw.items()
        }
        if set(basic_sections) != set(recommended_paths):
            raise ParameterDefinitionError(
                "basicSections and recommendedPaths must declare the same families"
            )
        missing_sections = {
            section
            for family_sections in basic_sections.values()
            for section in family_sections
            if section not in sections
        }
        if missing_sections:
            raise ParameterDefinitionError(
                "basicSections references unknown sections: "
                + ", ".join(sorted(missing_sections))
            )
        return cls(
            version=version,
            sections=sections,
            fields=fields,
            basic_sections=basic_sections,
            recommended_paths=recommended_paths,
        )

    def document(self, *, family_id: str | None = None) -> dict[str, object]:
        family_ids = (
            (family_id,)
            if family_id is not None
            else tuple(sorted(self.basic_sections))
        )
        for current_family_id in family_ids:
            if current_family_id not in self.basic_sections:
                raise ParameterDefinitionError(
                    f"parameter definitions do not include family {current_family_id!r}"
                )
        result = {
            "version": self.version,
            "sections": {key: item.document() for key, item in self.sections.items()},
            "fields": {key: item.document() for key, item in self.fields.items()},
            "basicSections": {
                key: list(self.basic_sections[key]) for key in family_ids
            },
            "recommendedPaths": {
                key: list(self.recommended_paths[key]) for key in family_ids
            },
        }
        return deepcopy(result)


@lru_cache(maxsize=1)
def parameter_catalog() -> ParameterCatalog:
    resource = files("aerospace_simulator").joinpath("parameter_definitions.json")
    try:
        raw = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ParameterDefinitionError(
            "bundled parameter definitions could not be loaded"
        ) from exc
    return ParameterCatalog.from_mapping(raw)


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ParameterDefinitionError(f"{path} must be a non-empty object")
    if any(not isinstance(key, str) or not key for key in value):
        raise ParameterDefinitionError(f"{path} keys must be non-empty strings")
    return value


def _string_tuple(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ParameterDefinitionError(f"{path} must be a list of strings")
    if len(set(value)) != len(value):
        raise ParameterDefinitionError(f"{path} values must be unique")
    return tuple(value)


def _optional_number(value: object, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterDefinitionError(f"{path} must be a number")
    return float(value)


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ParameterDefinitionError(f"{path} must be a boolean")
    return value
