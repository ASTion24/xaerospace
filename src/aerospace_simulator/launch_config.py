from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from .config import ScenarioValidationError
from .orbit_config import CentralBodyConfig

LAUNCH_CONTRACT_SCHEMA = "wms.aerospace.launch_to_orbit.v1"
LAUNCH_TASK_KINDS = ("two_stage_launch_to_orbit",)
STANDARD_GRAVITY_M_S2 = 9.80665


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


def _integer_ratio(numerator: float, denominator: float, path: str) -> None:
    ratio = numerator / denominator
    if not math.isclose(ratio, round(ratio), rel_tol=0.0, abs_tol=1e-10):
        raise ScenarioValidationError(f"{path} must be an integer multiple")


@dataclass(frozen=True)
class LaunchSiteConfig:
    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    start_epoch_s_since_j2000: float
    initial_vertical_speed_m_s: float


@dataclass(frozen=True)
class LaunchVehicleConfig:
    payload_mass_kg: float
    reference_area_m2: float
    drag_coefficient: float


@dataclass(frozen=True)
class GuidancePoint:
    elapsed_time_s: float
    pitch_deg: float


@dataclass(frozen=True)
class LaunchStageConfig:
    stage_id: str
    dry_mass_kg: float
    propellant_mass_kg: float
    thrust_n: float
    specific_impulse_s: float
    burn_time_s: float
    guidance_pitch_program: tuple[GuidancePoint, ...]

    @property
    def mass_flow_rate_kg_s(self) -> float:
        return self.thrust_n / (self.specific_impulse_s * STANDARD_GRAVITY_M_S2)


@dataclass(frozen=True)
class LaunchPropagationSettings:
    integrator: str
    step_size_s: float
    output_interval_s: float
    post_insertion_coast_duration_s: float


@dataclass(frozen=True)
class TargetOrbitConfig:
    altitude_m: float
    altitude_tolerance_m: float
    maximum_eccentricity: float


@dataclass(frozen=True)
class LaunchToOrbitConfig:
    schema_version: int
    name: str
    description: str
    backend: str
    dynamics: str
    frame: str
    central_body: CentralBodyConfig
    launch_site: LaunchSiteConfig
    vehicle: LaunchVehicleConfig
    stages: tuple[LaunchStageConfig, ...]
    propagation: LaunchPropagationSettings
    target_orbit: TargetOrbitConfig

    @property
    def lift_off_mass_kg(self) -> float:
        return self.vehicle.payload_mass_kg + sum(
            stage.dry_mass_kg + stage.propellant_mass_kg for stage in self.stages
        )

    @property
    def insertion_time_s(self) -> float:
        return sum(stage.burn_time_s for stage in self.stages)

    @property
    def duration_s(self) -> float:
        return self.insertion_time_s + self.propagation.post_insertion_coast_duration_s

    def protocol_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "frame": self.frame,
            "central_body": asdict(self.central_body),
            "launch_site": asdict(self.launch_site),
            "vehicle": asdict(self.vehicle),
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "dry_mass_kg": stage.dry_mass_kg,
                    "propellant_mass_kg": stage.propellant_mass_kg,
                    "thrust_n": stage.thrust_n,
                    "specific_impulse_s": stage.specific_impulse_s,
                    "burn_time_s": stage.burn_time_s,
                    "guidance_pitch_program": [
                        [point.elapsed_time_s, point.pitch_deg]
                        for point in stage.guidance_pitch_program
                    ],
                }
                for stage in self.stages
            ],
            "propagation": asdict(self.propagation),
            "target_orbit": asdict(self.target_orbit),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> LaunchToOrbitConfig:
        required_root = {
            "schema_version",
            "name",
            "description",
            "backend",
            "dynamics",
            "frame",
            "central_body",
            "launch_site",
            "vehicle",
            "stages",
            "propagation",
            "target_orbit",
        }
        missing = required_root - set(raw)
        unknown = set(raw) - required_root
        if missing:
            raise ScenarioValidationError(
                "launch scenario is missing fields: " + ", ".join(sorted(missing))
            )
        if unknown:
            raise ScenarioValidationError(
                "launch scenario contains unknown fields: " + ", ".join(sorted(unknown))
            )
        if raw["schema_version"] != 1:
            raise ScenarioValidationError("schema_version must be 1")
        name = _required_string(raw["name"], "name")
        description = _required_string(raw["description"], "description")
        backend = _required_string(raw["backend"], "backend")
        if backend not in {"tudatpy", "auto"}:
            raise ScenarioValidationError("backend must be 'tudatpy' or 'auto'")
        dynamics = _required_string(raw["dynamics"], "dynamics")
        if dynamics not in LAUNCH_TASK_KINDS:
            raise ScenarioValidationError(
                "dynamics must be 'two_stage_launch_to_orbit'"
            )
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

        launch_raw = _section(
            raw,
            "launch_site",
            required={
                "latitude_deg",
                "longitude_deg",
                "elevation_m",
                "start_epoch_s_since_j2000",
                "initial_vertical_speed_m_s",
            },
        )
        latitude = _number(launch_raw["latitude_deg"], "launch_site.latitude_deg")
        longitude = _number(
            launch_raw["longitude_deg"],
            "launch_site.longitude_deg",
        )
        if not -90.0 <= latitude <= 90.0:
            raise ScenarioValidationError(
                "launch_site.latitude_deg must be in [-90, 90]"
            )
        if not -180.0 <= longitude <= 180.0:
            raise ScenarioValidationError(
                "launch_site.longitude_deg must be in [-180, 180]"
            )
        initial_vertical_speed = _positive(
            launch_raw["initial_vertical_speed_m_s"],
            "launch_site.initial_vertical_speed_m_s",
        )
        if initial_vertical_speed > 100.0:
            raise ScenarioValidationError(
                "launch_site.initial_vertical_speed_m_s must not exceed 100"
            )
        launch_site = LaunchSiteConfig(
            latitude_deg=latitude,
            longitude_deg=longitude,
            elevation_m=_number(
                launch_raw["elevation_m"],
                "launch_site.elevation_m",
            ),
            start_epoch_s_since_j2000=_number(
                launch_raw["start_epoch_s_since_j2000"],
                "launch_site.start_epoch_s_since_j2000",
            ),
            initial_vertical_speed_m_s=initial_vertical_speed,
        )

        vehicle_raw = _section(
            raw,
            "vehicle",
            required={
                "payload_mass_kg",
                "reference_area_m2",
                "drag_coefficient",
            },
        )
        vehicle = LaunchVehicleConfig(
            payload_mass_kg=_positive(
                vehicle_raw["payload_mass_kg"],
                "vehicle.payload_mass_kg",
            ),
            reference_area_m2=_positive(
                vehicle_raw["reference_area_m2"],
                "vehicle.reference_area_m2",
            ),
            drag_coefficient=_positive(
                vehicle_raw["drag_coefficient"],
                "vehicle.drag_coefficient",
            ),
        )

        propagation_raw = _section(
            raw,
            "propagation",
            required={
                "integrator",
                "step_size_s",
                "output_interval_s",
                "post_insertion_coast_duration_s",
            },
        )
        integrator = _required_string(
            propagation_raw["integrator"],
            "propagation.integrator",
        )
        if integrator != "rk4_fixed":
            raise ScenarioValidationError("propagation.integrator must be 'rk4_fixed'")
        step_size = _positive(
            propagation_raw["step_size_s"],
            "propagation.step_size_s",
        )
        output_interval = _positive(
            propagation_raw["output_interval_s"],
            "propagation.output_interval_s",
        )
        coast_duration = _positive(
            propagation_raw["post_insertion_coast_duration_s"],
            "propagation.post_insertion_coast_duration_s",
        )
        if output_interval < step_size:
            raise ScenarioValidationError(
                "propagation.output_interval_s must be at least step_size_s"
            )
        _integer_ratio(
            output_interval,
            step_size,
            "propagation.output_interval_s",
        )
        _integer_ratio(
            coast_duration,
            output_interval,
            "propagation.post_insertion_coast_duration_s",
        )
        propagation = LaunchPropagationSettings(
            integrator=integrator,
            step_size_s=step_size,
            output_interval_s=output_interval,
            post_insertion_coast_duration_s=coast_duration,
        )

        stages_raw = raw["stages"]
        if (
            not isinstance(stages_raw, Sequence)
            or isinstance(stages_raw, (str, bytes))
            or len(stages_raw) != 2
        ):
            raise ScenarioValidationError("stages must contain exactly two stages")
        stages = tuple(
            _stage_from_mapping(item, index=index)
            for index, item in enumerate(stages_raw)
        )
        if len({stage.stage_id for stage in stages}) != len(stages):
            raise ScenarioValidationError("stages.stage_id values must be unique")
        for stage in stages:
            _integer_ratio(
                stage.burn_time_s,
                step_size,
                f"stages.{stage.stage_id}.burn_time_s",
            )
            _integer_ratio(
                stage.burn_time_s,
                output_interval,
                f"stages.{stage.stage_id}.burn_time_s",
            )

        target_raw = _section(
            raw,
            "target_orbit",
            required={
                "altitude_m",
                "altitude_tolerance_m",
                "maximum_eccentricity",
            },
        )
        target_altitude = _positive(
            target_raw["altitude_m"],
            "target_orbit.altitude_m",
        )
        altitude_tolerance = _positive(
            target_raw["altitude_tolerance_m"],
            "target_orbit.altitude_tolerance_m",
        )
        maximum_eccentricity = _positive(
            target_raw["maximum_eccentricity"],
            "target_orbit.maximum_eccentricity",
        )
        if target_altitude < 120_000.0:
            raise ScenarioValidationError(
                "target_orbit.altitude_m must be at least 120000"
            )
        if altitude_tolerance >= target_altitude:
            raise ScenarioValidationError(
                "target_orbit.altitude_tolerance_m must be below altitude_m"
            )
        if maximum_eccentricity >= 0.1:
            raise ScenarioValidationError(
                "target_orbit.maximum_eccentricity must be below 0.1"
            )
        target_orbit = TargetOrbitConfig(
            altitude_m=target_altitude,
            altitude_tolerance_m=altitude_tolerance,
            maximum_eccentricity=maximum_eccentricity,
        )

        result = cls(
            schema_version=1,
            name=name,
            description=description,
            backend=backend,
            dynamics=dynamics,
            frame=frame,
            central_body=central_body,
            launch_site=launch_site,
            vehicle=vehicle,
            stages=stages,
            propagation=propagation,
            target_orbit=target_orbit,
        )
        first_stage_acceleration = stages[0].thrust_n / result.lift_off_mass_kg
        surface_gravity = (
            central_body.gravitational_parameter_m3_s2
            / central_body.equatorial_radius_m**2
        )
        if first_stage_acceleration <= 1.05 * surface_gravity:
            raise ScenarioValidationError(
                "first-stage thrust-to-weight ratio must exceed 1.05 at lift-off"
            )
        return result


def _stage_from_mapping(value: object, *, index: int) -> LaunchStageConfig:
    path = f"stages[{index}]"
    if not isinstance(value, Mapping):
        raise ScenarioValidationError(f"{path} must be an object")
    required = {
        "stage_id",
        "dry_mass_kg",
        "propellant_mass_kg",
        "thrust_n",
        "specific_impulse_s",
        "burn_time_s",
        "guidance_pitch_program",
    }
    missing = required - set(value)
    unknown = set(value) - required
    if missing:
        raise ScenarioValidationError(
            f"{path} is missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ScenarioValidationError(
            f"{path} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    stage_id = _required_string(value["stage_id"], f"{path}.stage_id")
    dry_mass = _positive(value["dry_mass_kg"], f"{path}.dry_mass_kg")
    propellant_mass = _positive(
        value["propellant_mass_kg"],
        f"{path}.propellant_mass_kg",
    )
    thrust = _positive(value["thrust_n"], f"{path}.thrust_n")
    specific_impulse = _positive(
        value["specific_impulse_s"],
        f"{path}.specific_impulse_s",
    )
    burn_time = _positive(value["burn_time_s"], f"{path}.burn_time_s")
    expected_propellant = (
        thrust * burn_time / (specific_impulse * STANDARD_GRAVITY_M_S2)
    )
    if not math.isclose(
        expected_propellant,
        propellant_mass,
        rel_tol=1e-6,
        abs_tol=1e-6,
    ):
        raise ScenarioValidationError(
            f"{path} thrust, specific impulse, burn time, and propellant mass "
            "must satisfy m_propellant = thrust * burn_time / (Isp * g0)"
        )

    program_raw = value["guidance_pitch_program"]
    if (
        not isinstance(program_raw, Sequence)
        or isinstance(program_raw, (str, bytes))
        or len(program_raw) < 2
    ):
        raise ScenarioValidationError(
            f"{path}.guidance_pitch_program must contain at least two points"
        )
    program: list[GuidancePoint] = []
    for point_index, point_raw in enumerate(program_raw):
        point_path = f"{path}.guidance_pitch_program[{point_index}]"
        if (
            not isinstance(point_raw, Sequence)
            or isinstance(point_raw, (str, bytes))
            or len(point_raw) != 2
        ):
            raise ScenarioValidationError(
                f"{point_path} must be [elapsed_time_s, pitch_deg]"
            )
        elapsed = _number(point_raw[0], f"{point_path}[0]")
        pitch = _number(point_raw[1], f"{point_path}[1]")
        if not -10.0 <= pitch <= 90.0:
            raise ScenarioValidationError(f"{point_path}[1] must be in [-10, 90]")
        program.append(GuidancePoint(elapsed_time_s=elapsed, pitch_deg=pitch))
    times = [point.elapsed_time_s for point in program]
    if not math.isclose(times[0], 0.0, abs_tol=1e-12):
        raise ScenarioValidationError(
            f"{path}.guidance_pitch_program must start at zero"
        )
    if any(right <= left for left, right in pairwise(times)):
        raise ScenarioValidationError(
            f"{path}.guidance_pitch_program times must be strictly increasing"
        )
    if not math.isclose(times[-1], burn_time, rel_tol=0.0, abs_tol=1e-10):
        raise ScenarioValidationError(
            f"{path}.guidance_pitch_program must end at burn_time_s"
        )
    return LaunchStageConfig(
        stage_id=stage_id,
        dry_mass_kg=dry_mass,
        propellant_mass_kg=propellant_mass,
        thrust_n=thrust,
        specific_impulse_s=specific_impulse,
        burn_time_s=burn_time,
        guidance_pitch_program=tuple(program),
    )


def load_launch_scenario(path: str | Path) -> LaunchToOrbitConfig:
    scenario_path = Path(path)
    try:
        raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioValidationError(
            f"launch scenario file not found: {scenario_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(
            f"launch scenario is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise ScenarioValidationError("launch scenario root must be an object")
    return LaunchToOrbitConfig.from_mapping(raw)
