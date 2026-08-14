from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import ScenarioValidationError

ORBIT_CONTRACT_SCHEMA = "wms.aerospace.orbit_propagation.v2"
ORBIT_TASK_KINDS = ("earth_orbit_two_body", "earth_orbit_j2")


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioValidationError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ScenarioValidationError(f"{path} must be finite")
    return number


def _positive(value: Any, path: str) -> float:
    number = _number(value, path)
    if number <= 0:
        raise ScenarioValidationError(f"{path} must be greater than zero")
    return number


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ScenarioValidationError(f"{path} must be a boolean")
    return value


def _section(
    raw: Mapping[str, Any],
    name: str,
    *,
    required: set[str],
) -> Mapping[str, Any]:
    value = raw.get(name)
    if not isinstance(value, Mapping):
        raise ScenarioValidationError(f"{name} must be an object")
    missing = required - set(value)
    unknown = set(value) - required
    if missing:
        raise ScenarioValidationError(
            f"{name} is missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ScenarioValidationError(
            f"{name} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return value


def _require_integer_ratio(numerator: float, denominator: float, path: str) -> None:
    ratio = numerator / denominator
    if not math.isclose(ratio, round(ratio), rel_tol=0.0, abs_tol=1e-10):
        raise ScenarioValidationError(f"{path} must be an integer multiple")


@dataclass(frozen=True)
class CentralBodyConfig:
    name: str
    gravitational_parameter_m3_s2: float
    equatorial_radius_m: float
    j2: float
    rotation_rate_rad_s: float


@dataclass(frozen=True)
class SpacecraftConfig:
    name: str
    mass_kg: float


@dataclass(frozen=True)
class OrbitAerodynamicsConfig:
    enabled: bool
    reference_area_m2: float
    drag_coefficient: float
    atmosphere_scale_height_m: float
    atmosphere_surface_density_kg_m3: float


@dataclass(frozen=True)
class KeplerianInitialState:
    semi_major_axis_m: float
    eccentricity: float
    inclination_deg: float
    argument_of_periapsis_deg: float
    raan_deg: float
    true_anomaly_deg: float


@dataclass(frozen=True)
class OrbitPropagationSettings:
    start_epoch_s_since_j2000: float
    duration_s: float
    integrator: str
    step_size_s: float
    output_interval_s: float


@dataclass(frozen=True)
class OrbitPropagationConfig:
    schema_version: int
    name: str
    description: str
    backend: str
    dynamics: str
    frame: str
    central_body: CentralBodyConfig
    spacecraft: SpacecraftConfig
    aerodynamics: OrbitAerodynamicsConfig
    initial_state: KeplerianInitialState
    propagation: OrbitPropagationSettings

    def protocol_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "frame": self.frame,
            "central_body": asdict(self.central_body),
            "spacecraft": asdict(self.spacecraft),
            "aerodynamics": asdict(self.aerodynamics),
            "initial_state": asdict(self.initial_state),
            "propagation": asdict(self.propagation),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> OrbitPropagationConfig:
        required_root = {
            "schema_version",
            "name",
            "description",
            "backend",
            "dynamics",
            "frame",
            "central_body",
            "spacecraft",
            "aerodynamics",
            "initial_state",
            "propagation",
        }
        missing_root = required_root - set(raw)
        unknown_root = set(raw) - required_root
        if missing_root:
            raise ScenarioValidationError(
                "orbit scenario is missing fields: " + ", ".join(sorted(missing_root))
            )
        if unknown_root:
            raise ScenarioValidationError(
                "orbit scenario contains unknown fields: "
                + ", ".join(sorted(unknown_root))
            )
        if raw["schema_version"] != 2:
            raise ScenarioValidationError("schema_version must be 2")

        name = _required_string(raw["name"], "name")
        description = _required_string(raw["description"], "description")
        backend = _required_string(raw["backend"], "backend")
        dynamics = _required_string(raw["dynamics"], "dynamics")
        if dynamics not in ORBIT_TASK_KINDS:
            choices = ", ".join(ORBIT_TASK_KINDS)
            raise ScenarioValidationError(f"dynamics must be one of: {choices}")
        frame = _required_string(raw["frame"], "frame")
        if frame != "J2000":
            raise ScenarioValidationError("frame must be 'J2000'")

        central_raw = _section(
            raw,
            "central_body",
            required={
                "name",
                "gravitational_parameter_m3_s2",
                "equatorial_radius_m",
                "j2",
                "rotation_rate_rad_s",
            },
        )
        central_name = _required_string(central_raw["name"], "central_body.name")
        if central_name != "Earth":
            raise ScenarioValidationError("central_body.name must be 'Earth'")
        central_body = CentralBodyConfig(
            name=central_name,
            gravitational_parameter_m3_s2=_positive(
                central_raw["gravitational_parameter_m3_s2"],
                "central_body.gravitational_parameter_m3_s2",
            ),
            equatorial_radius_m=_positive(
                central_raw["equatorial_radius_m"],
                "central_body.equatorial_radius_m",
            ),
            j2=_positive(central_raw["j2"], "central_body.j2"),
            rotation_rate_rad_s=_positive(
                central_raw["rotation_rate_rad_s"],
                "central_body.rotation_rate_rad_s",
            ),
        )

        spacecraft_raw = _section(
            raw,
            "spacecraft",
            required={"name", "mass_kg"},
        )
        spacecraft = SpacecraftConfig(
            name=_required_string(spacecraft_raw["name"], "spacecraft.name"),
            mass_kg=_positive(spacecraft_raw["mass_kg"], "spacecraft.mass_kg"),
        )
        if spacecraft.name == central_body.name:
            raise ScenarioValidationError(
                "spacecraft.name must differ from central_body.name"
            )

        aerodynamics_raw = _section(
            raw,
            "aerodynamics",
            required={
                "enabled",
                "reference_area_m2",
                "drag_coefficient",
                "atmosphere_scale_height_m",
                "atmosphere_surface_density_kg_m3",
            },
        )
        aerodynamics = OrbitAerodynamicsConfig(
            enabled=_boolean(
                aerodynamics_raw["enabled"],
                "aerodynamics.enabled",
            ),
            reference_area_m2=_positive(
                aerodynamics_raw["reference_area_m2"],
                "aerodynamics.reference_area_m2",
            ),
            drag_coefficient=_positive(
                aerodynamics_raw["drag_coefficient"],
                "aerodynamics.drag_coefficient",
            ),
            atmosphere_scale_height_m=_positive(
                aerodynamics_raw["atmosphere_scale_height_m"],
                "aerodynamics.atmosphere_scale_height_m",
            ),
            atmosphere_surface_density_kg_m3=_positive(
                aerodynamics_raw["atmosphere_surface_density_kg_m3"],
                "aerodynamics.atmosphere_surface_density_kg_m3",
            ),
        )
        if aerodynamics.enabled and dynamics != "earth_orbit_j2":
            raise ScenarioValidationError(
                "aerodynamics.enabled requires earth_orbit_j2 dynamics"
            )

        initial_raw = _section(
            raw,
            "initial_state",
            required={
                "semi_major_axis_m",
                "eccentricity",
                "inclination_deg",
                "argument_of_periapsis_deg",
                "raan_deg",
                "true_anomaly_deg",
            },
        )
        semi_major_axis = _positive(
            initial_raw["semi_major_axis_m"],
            "initial_state.semi_major_axis_m",
        )
        eccentricity = _number(
            initial_raw["eccentricity"],
            "initial_state.eccentricity",
        )
        if not 1e-6 <= eccentricity < 1.0:
            raise ScenarioValidationError(
                "initial_state.eccentricity must be in [1e-6, 1)"
            )
        inclination = _number(
            initial_raw["inclination_deg"],
            "initial_state.inclination_deg",
        )
        if not 0.1 <= inclination <= 179.9:
            raise ScenarioValidationError(
                "initial_state.inclination_deg must be in [0.1, 179.9]"
            )
        for field in (
            "argument_of_periapsis_deg",
            "raan_deg",
            "true_anomaly_deg",
        ):
            angle = _number(initial_raw[field], f"initial_state.{field}")
            if not 0 <= angle < 360:
                raise ScenarioValidationError(
                    f"initial_state.{field} must be in [0, 360)"
                )
        periapsis_radius = semi_major_axis * (1.0 - eccentricity)
        if periapsis_radius <= central_body.equatorial_radius_m:
            raise ScenarioValidationError(
                "initial orbit intersects the central body at periapsis"
            )
        initial_state = KeplerianInitialState(
            semi_major_axis_m=semi_major_axis,
            eccentricity=eccentricity,
            inclination_deg=inclination,
            argument_of_periapsis_deg=float(initial_raw["argument_of_periapsis_deg"]),
            raan_deg=float(initial_raw["raan_deg"]),
            true_anomaly_deg=float(initial_raw["true_anomaly_deg"]),
        )

        propagation_raw = _section(
            raw,
            "propagation",
            required={
                "start_epoch_s_since_j2000",
                "duration_s",
                "integrator",
                "step_size_s",
                "output_interval_s",
            },
        )
        integrator = _required_string(
            propagation_raw["integrator"],
            "propagation.integrator",
        )
        if integrator != "rk4_fixed":
            raise ScenarioValidationError("propagation.integrator must be 'rk4_fixed'")
        duration = _positive(propagation_raw["duration_s"], "propagation.duration_s")
        if duration > 31 * 86400:
            raise ScenarioValidationError(
                "propagation.duration_s must not exceed 31 days"
            )
        step_size = _positive(
            propagation_raw["step_size_s"],
            "propagation.step_size_s",
        )
        output_interval = _positive(
            propagation_raw["output_interval_s"],
            "propagation.output_interval_s",
        )
        if output_interval < step_size:
            raise ScenarioValidationError(
                "propagation.output_interval_s must be at least step_size_s"
            )
        _require_integer_ratio(
            duration,
            step_size,
            "propagation.duration_s",
        )
        _require_integer_ratio(
            output_interval,
            step_size,
            "propagation.output_interval_s",
        )
        _require_integer_ratio(
            duration,
            output_interval,
            "propagation.duration_s",
        )
        propagation = OrbitPropagationSettings(
            start_epoch_s_since_j2000=_number(
                propagation_raw["start_epoch_s_since_j2000"],
                "propagation.start_epoch_s_since_j2000",
            ),
            duration_s=duration,
            integrator=integrator,
            step_size_s=step_size,
            output_interval_s=output_interval,
        )

        result = cls(
            schema_version=2,
            name=name,
            description=description,
            backend=backend,
            dynamics=dynamics,
            frame=frame,
            central_body=central_body,
            spacecraft=spacecraft,
            aerodynamics=aerodynamics,
            initial_state=initial_state,
            propagation=propagation,
        )
        return result


def load_orbit_scenario(path: str | Path) -> OrbitPropagationConfig:
    scenario_path = Path(path)
    try:
        raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioValidationError(
            f"orbit scenario file not found: {scenario_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(
            f"orbit scenario is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise ScenarioValidationError("orbit scenario root must be an object")
    result = OrbitPropagationConfig.from_mapping(raw)
    return result
