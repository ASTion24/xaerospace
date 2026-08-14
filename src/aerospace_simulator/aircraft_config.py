from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from .config import ScenarioValidationError

AIRCRAFT_CONTRACT_SCHEMA = "wms.aerospace.aircraft_flight.v1"
AIRCRAFT_TASK_KINDS = ("fixed_wing_trimmed_6dof",)
SUPPORTED_AIRCRAFT_MODELS = ("c172p", "c172r", "c182", "c310", "J3Cub")


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


def _bounded(value: Any, path: str, minimum: float, maximum: float) -> float:
    number = _number(value, path)
    if not minimum <= number <= maximum:
        raise ScenarioValidationError(f"{path} must be in [{minimum:g}, {maximum:g}]")
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


def _require_integer_ratio(numerator: float, denominator: float, path: str) -> None:
    ratio = numerator / denominator
    if not math.isclose(ratio, round(ratio), rel_tol=0.0, abs_tol=1e-9):
        raise ScenarioValidationError(f"{path} must be an integer multiple")


@dataclass(frozen=True)
class AircraftModelConfig:
    model_id: str


@dataclass(frozen=True)
class AircraftInitialCondition:
    latitude_deg: float
    longitude_deg: float
    altitude_msl_m: float
    calibrated_airspeed_m_s: float
    flight_path_angle_deg: float
    heading_deg: float


@dataclass(frozen=True)
class AircraftEnvironmentConfig:
    atmosphere: str
    wind_north_m_s: float
    wind_east_m_s: float
    wind_down_m_s: float


@dataclass(frozen=True)
class AircraftTrimConfig:
    mode: str


@dataclass(frozen=True)
class ControlSegment:
    id: str
    start_time_s: float
    end_time_s: float
    aileron_delta_norm: float
    elevator_delta_norm: float
    rudder_delta_norm: float
    throttle_delta_norm: float


@dataclass(frozen=True)
class AircraftControlsConfig:
    reference: str
    segments: tuple[ControlSegment, ...]


@dataclass(frozen=True)
class AircraftPropagationConfig:
    duration_s: float
    step_size_s: float
    output_interval_s: float


@dataclass(frozen=True)
class AircraftFlightConfig:
    schema_version: int
    name: str
    description: str
    backend: str
    dynamics: str
    aircraft: AircraftModelConfig
    initial_condition: AircraftInitialCondition
    environment: AircraftEnvironmentConfig
    trim: AircraftTrimConfig
    controls: AircraftControlsConfig
    propagation: AircraftPropagationConfig

    def protocol_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "aircraft": asdict(self.aircraft),
            "initial_condition": asdict(self.initial_condition),
            "environment": asdict(self.environment),
            "trim": asdict(self.trim),
            "controls": asdict(self.controls),
            "propagation": asdict(self.propagation),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> AircraftFlightConfig:
        required_root = {
            "schema_version",
            "name",
            "description",
            "backend",
            "dynamics",
            "aircraft",
            "initial_condition",
            "environment",
            "trim",
            "controls",
            "propagation",
        }
        missing_root = required_root - set(raw)
        unknown_root = set(raw) - required_root
        if missing_root:
            raise ScenarioValidationError(
                "aircraft scenario is missing fields: "
                + ", ".join(sorted(missing_root))
            )
        if unknown_root:
            raise ScenarioValidationError(
                "aircraft scenario contains unknown fields: "
                + ", ".join(sorted(unknown_root))
            )
        if raw["schema_version"] != 1:
            raise ScenarioValidationError("schema_version must be 1")

        name = _required_string(raw["name"], "name")
        description = _required_string(raw["description"], "description")
        backend = _required_string(raw["backend"], "backend")
        dynamics = _required_string(raw["dynamics"], "dynamics")
        if dynamics not in AIRCRAFT_TASK_KINDS:
            choices = ", ".join(AIRCRAFT_TASK_KINDS)
            raise ScenarioValidationError(f"dynamics must be one of: {choices}")

        aircraft_raw = _section(
            raw,
            "aircraft",
            required={"model_id"},
        )
        model_id = _required_string(aircraft_raw["model_id"], "aircraft.model_id")
        if model_id not in SUPPORTED_AIRCRAFT_MODELS:
            choices = ", ".join(SUPPORTED_AIRCRAFT_MODELS)
            raise ScenarioValidationError(
                f"aircraft.model_id must be one of: {choices}"
            )
        aircraft = AircraftModelConfig(model_id=model_id)

        initial_raw = _section(
            raw,
            "initial_condition",
            required={
                "latitude_deg",
                "longitude_deg",
                "altitude_msl_m",
                "calibrated_airspeed_m_s",
                "flight_path_angle_deg",
                "heading_deg",
            },
        )
        initial_condition = AircraftInitialCondition(
            latitude_deg=_bounded(
                initial_raw["latitude_deg"],
                "initial_condition.latitude_deg",
                -90.0,
                90.0,
            ),
            longitude_deg=_bounded(
                initial_raw["longitude_deg"],
                "initial_condition.longitude_deg",
                -180.0,
                180.0,
            ),
            altitude_msl_m=_bounded(
                initial_raw["altitude_msl_m"],
                "initial_condition.altitude_msl_m",
                0.0,
                5000.0,
            ),
            calibrated_airspeed_m_s=_bounded(
                initial_raw["calibrated_airspeed_m_s"],
                "initial_condition.calibrated_airspeed_m_s",
                30.0,
                65.0,
            ),
            flight_path_angle_deg=_bounded(
                initial_raw["flight_path_angle_deg"],
                "initial_condition.flight_path_angle_deg",
                -5.0,
                5.0,
            ),
            heading_deg=_bounded(
                initial_raw["heading_deg"],
                "initial_condition.heading_deg",
                0.0,
                360.0,
            ),
        )
        if initial_condition.heading_deg == 360.0:
            raise ScenarioValidationError(
                "initial_condition.heading_deg must be less than 360"
            )

        environment_raw = _section(
            raw,
            "environment",
            required={
                "atmosphere",
                "wind_north_m_s",
                "wind_east_m_s",
                "wind_down_m_s",
            },
        )
        atmosphere = _required_string(
            environment_raw["atmosphere"],
            "environment.atmosphere",
        )
        if atmosphere != "us_standard_1976":
            raise ScenarioValidationError(
                "environment.atmosphere must be 'us_standard_1976'"
            )
        environment = AircraftEnvironmentConfig(
            atmosphere=atmosphere,
            wind_north_m_s=_bounded(
                environment_raw["wind_north_m_s"],
                "environment.wind_north_m_s",
                -50.0,
                50.0,
            ),
            wind_east_m_s=_bounded(
                environment_raw["wind_east_m_s"],
                "environment.wind_east_m_s",
                -50.0,
                50.0,
            ),
            wind_down_m_s=_bounded(
                environment_raw["wind_down_m_s"],
                "environment.wind_down_m_s",
                -20.0,
                20.0,
            ),
        )

        trim_raw = _section(raw, "trim", required={"mode"})
        trim_mode = _required_string(trim_raw["mode"], "trim.mode")
        if trim_mode != "longitudinal":
            raise ScenarioValidationError("trim.mode must be 'longitudinal'")
        trim = AircraftTrimConfig(mode=trim_mode)

        propagation_raw = _section(
            raw,
            "propagation",
            required={"duration_s", "step_size_s", "output_interval_s"},
        )
        duration = _positive(
            propagation_raw["duration_s"],
            "propagation.duration_s",
        )
        if duration > 300.0:
            raise ScenarioValidationError("propagation.duration_s must not exceed 300")
        step_size = _bounded(
            propagation_raw["step_size_s"],
            "propagation.step_size_s",
            0.001,
            1.0 / 60.0,
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
        propagation = AircraftPropagationConfig(
            duration_s=duration,
            step_size_s=step_size,
            output_interval_s=output_interval,
        )

        controls_raw = _section(
            raw,
            "controls",
            required={"reference", "segments"},
        )
        reference = _required_string(
            controls_raw["reference"],
            "controls.reference",
        )
        if reference != "trim_relative":
            raise ScenarioValidationError("controls.reference must be 'trim_relative'")
        segments = _parse_control_segments(
            controls_raw["segments"],
            duration_s=duration,
        )
        for segment in segments:
            _require_integer_ratio(
                segment.start_time_s,
                step_size,
                f"controls.segments[{segment.id}].start_time_s",
            )
            _require_integer_ratio(
                segment.end_time_s,
                step_size,
                f"controls.segments[{segment.id}].end_time_s",
            )
        controls = AircraftControlsConfig(
            reference=reference,
            segments=segments,
        )

        result = cls(
            schema_version=1,
            name=name,
            description=description,
            backend=backend,
            dynamics=dynamics,
            aircraft=aircraft,
            initial_condition=initial_condition,
            environment=environment,
            trim=trim,
            controls=controls,
            propagation=propagation,
        )
        return result


def _parse_control_segments(
    raw: Any,
    *,
    duration_s: float,
) -> tuple[ControlSegment, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ScenarioValidationError("controls.segments must be an array")
    segments: list[ControlSegment] = []
    required = {
        "id",
        "start_time_s",
        "end_time_s",
        "aileron_delta_norm",
        "elevator_delta_norm",
        "rudder_delta_norm",
        "throttle_delta_norm",
    }
    for index, item in enumerate(raw):
        path = f"controls.segments[{index}]"
        if not isinstance(item, Mapping):
            raise ScenarioValidationError(f"{path} must be an object")
        missing = required - set(item)
        unknown = set(item) - required
        if missing:
            raise ScenarioValidationError(
                f"{path} is missing fields: {', '.join(sorted(missing))}"
            )
        if unknown:
            raise ScenarioValidationError(
                f"{path} contains unknown fields: {', '.join(sorted(unknown))}"
            )
        start_time = _bounded(
            item["start_time_s"],
            f"{path}.start_time_s",
            0.0,
            duration_s,
        )
        end_time = _bounded(
            item["end_time_s"],
            f"{path}.end_time_s",
            0.0,
            duration_s,
        )
        if end_time <= start_time:
            raise ScenarioValidationError(
                f"{path}.end_time_s must be greater than start_time_s"
            )
        segment = ControlSegment(
            id=_required_string(item["id"], f"{path}.id"),
            start_time_s=start_time,
            end_time_s=end_time,
            aileron_delta_norm=_bounded(
                item["aileron_delta_norm"],
                f"{path}.aileron_delta_norm",
                -1.0,
                1.0,
            ),
            elevator_delta_norm=_bounded(
                item["elevator_delta_norm"],
                f"{path}.elevator_delta_norm",
                -1.0,
                1.0,
            ),
            rudder_delta_norm=_bounded(
                item["rudder_delta_norm"],
                f"{path}.rudder_delta_norm",
                -1.0,
                1.0,
            ),
            throttle_delta_norm=_bounded(
                item["throttle_delta_norm"],
                f"{path}.throttle_delta_norm",
                -1.0,
                1.0,
            ),
        )
        segments.append(segment)
    ids = [segment.id for segment in segments]
    if len(set(ids)) != len(ids):
        raise ScenarioValidationError("controls.segments ids must be unique")
    ordered = tuple(sorted(segments, key=lambda segment: segment.start_time_s))
    for previous, current in pairwise(ordered):
        if current.start_time_s < previous.end_time_s:
            raise ScenarioValidationError("controls.segments must not overlap")
    result = ordered
    return result


def load_aircraft_scenario(path: str | Path) -> AircraftFlightConfig:
    scenario_path = Path(path)
    try:
        raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioValidationError(
            f"aircraft scenario file not found: {scenario_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(
            f"aircraft scenario is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise ScenarioValidationError("aircraft scenario root must be an object")
    result = AircraftFlightConfig.from_mapping(raw)
    return result
