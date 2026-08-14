from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any


class ScenarioValidationError(ValueError):
    """Raised when a scenario cannot be represented without changing its meaning."""


POINT_MASS_DYNAMICS = (
    "single_stage_point_mass_3dof",
    "single_stage_point_mass_3dof_recovery",
)
RIGID_BODY_DYNAMICS = (
    "single_stage_rigid_body_6dof",
    "single_stage_rigid_body_6dof_recovery",
)
RECOVERY_DYNAMICS = (
    "single_stage_point_mass_3dof_recovery",
    "single_stage_rigid_body_6dof_recovery",
)


def _section(
    raw: Mapping[str, Any],
    name: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    value = raw.get(name)
    if not isinstance(value, Mapping):
        raise ScenarioValidationError(f"{name} must be an object")
    optional = optional or set()
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise ScenarioValidationError(
            f"{name} is missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ScenarioValidationError(
            f"{name} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioValidationError(f"{path} must be a number")
    return float(value)


def _positive(value: Any, path: str) -> float:
    number = _number(value, path)
    if number <= 0:
        raise ScenarioValidationError(f"{path} must be greater than zero")
    return number


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioValidationError(f"{path} must be an integer")
    return value


def _positive_tuple3(value: Any, path: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise ScenarioValidationError(f"{path} must contain exactly three values")
    return (
        _positive(value[0], f"{path}[0]"),
        _positive(value[1], f"{path}[1]"),
        _positive(value[2], f"{path}[2]"),
    )


def _orientation(value: Any, path: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ScenarioValidationError(f"{path} must be one of: {choices}")
    return value


def _omit_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _omit_none(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, tuple):
        return tuple(_omit_none(item) for item in value)
    if isinstance(value, list):
        return [_omit_none(item) for item in value]
    return value


@dataclass(frozen=True)
class EnvironmentConfig:
    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    atmospheric_model: str


@dataclass(frozen=True)
class VehicleConfig:
    radius_m: float
    dry_mass_without_motor_kg: float
    center_of_mass_without_motor_m: float
    drag_coefficient_power_on: float
    drag_coefficient_power_off: float
    weathercock_coefficient: float


@dataclass(frozen=True)
class MotorConfig:
    thrust_curve: tuple[tuple[float, float], ...]
    dry_mass_kg: float
    propellant_initial_mass_kg: float
    burn_time_s: float


@dataclass(frozen=True)
class RigidBodyMotorConfig:
    dry_inertia_kg_m2: tuple[float, float, float]
    chamber_radius_m: float
    chamber_height_m: float
    chamber_position_m: float
    nozzle_radius_m: float
    center_of_dry_mass_position_m: float
    nozzle_position_m: float
    coordinate_system_orientation: str


@dataclass(frozen=True)
class NoseConfig:
    length_m: float
    kind: str
    position_m: float


@dataclass(frozen=True)
class FinsConfig:
    count: int
    root_chord_m: float
    tip_chord_m: float
    span_m: float
    position_m: float
    cant_angle_deg: float


@dataclass(frozen=True)
class TailConfig:
    top_radius_m: float
    bottom_radius_m: float
    length_m: float
    position_m: float


@dataclass(frozen=True)
class RailButtonsConfig:
    upper_position_m: float
    lower_position_m: float
    angular_position_deg: float


@dataclass(frozen=True)
class RigidBodyConfig:
    vehicle_dry_inertia_kg_m2: tuple[float, float, float]
    coordinate_system_orientation: str
    motor_position_m: float
    motor: RigidBodyMotorConfig
    nose: NoseConfig
    fins: FinsConfig
    tail: TailConfig | None
    rail_buttons: RailButtonsConfig


@dataclass(frozen=True)
class ParachuteTriggerConfig:
    kind: str
    altitude_agl_m: float | None


@dataclass(frozen=True)
class ParachuteConfig:
    id: str
    cd_s_m2: float
    trigger: ParachuteTriggerConfig
    sampling_rate_hz: float
    lag_s: float


@dataclass(frozen=True)
class RecoveryConfig:
    parachutes: tuple[ParachuteConfig, ...]


@dataclass(frozen=True)
class LaunchConfig:
    rail_length_m: float
    inclination_deg: float
    heading_deg: float
    max_time_s: float
    max_time_step_s: float


@dataclass(frozen=True)
class OutputConfig:
    sample_interval_s: float


@dataclass(frozen=True)
class ScenarioConfig:
    schema_version: int
    name: str
    description: str
    backend: str
    dynamics: str
    environment: EnvironmentConfig
    vehicle: VehicleConfig
    motor: MotorConfig
    rigid_body: RigidBodyConfig | None
    recovery: RecoveryConfig | None
    launch: LaunchConfig
    output: OutputConfig

    def protocol_document(self) -> dict[str, object]:
        vehicle = asdict(self.vehicle)
        if self.dynamics not in POINT_MASS_DYNAMICS:
            vehicle.pop("weathercock_coefficient")
        document = {
            "schema_version": self.schema_version,
            "environment": asdict(self.environment),
            "vehicle": vehicle,
            "motor": asdict(self.motor),
            "launch": asdict(self.launch),
            "output": asdict(self.output),
        }
        if self.rigid_body is not None:
            document["rigid_body"] = asdict(self.rigid_body)
        if self.recovery is not None:
            document["recovery"] = asdict(self.recovery)
        return _omit_none(document)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ScenarioConfig:
        allowed_root = {
            "schema_version",
            "name",
            "description",
            "backend",
            "dynamics",
            "environment",
            "vehicle",
            "motor",
            "rigid_body",
            "recovery",
            "launch",
            "output",
        }
        unknown_root = set(raw) - allowed_root
        if unknown_root:
            raise ScenarioValidationError(
                f"scenario contains unknown fields: {', '.join(sorted(unknown_root))}"
            )

        if raw.get("schema_version") != 1:
            raise ScenarioValidationError("schema_version must be 1")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ScenarioValidationError("name must be a non-empty string")
        description = raw.get("description", "")
        if not isinstance(description, str):
            raise ScenarioValidationError("description must be a string")
        backend = raw.get("backend")
        if not isinstance(backend, str) or not backend.strip():
            raise ScenarioValidationError("backend must be a non-empty string")
        backend = backend.strip()
        dynamics = raw.get("dynamics")
        supported_dynamics = {*POINT_MASS_DYNAMICS, *RIGID_BODY_DYNAMICS}
        if dynamics not in supported_dynamics:
            raise ScenarioValidationError(
                "dynamics must be one of: " + ", ".join(sorted(supported_dynamics))
            )

        environment_raw = _section(
            raw,
            "environment",
            required={
                "latitude_deg",
                "longitude_deg",
                "elevation_m",
                "atmospheric_model",
            },
        )
        atmosphere = environment_raw["atmospheric_model"]
        if atmosphere != "standard_atmosphere":
            raise ScenarioValidationError(
                "only environment.atmospheric_model='standard_atmosphere' is supported"
            )
        latitude = _number(environment_raw["latitude_deg"], "environment.latitude_deg")
        longitude = _number(
            environment_raw["longitude_deg"], "environment.longitude_deg"
        )
        if not -90 <= latitude <= 90:
            raise ScenarioValidationError(
                "environment.latitude_deg must be between -90 and 90"
            )
        if not -180 <= longitude <= 180:
            raise ScenarioValidationError(
                "environment.longitude_deg must be between -180 and 180"
            )
        environment = EnvironmentConfig(
            latitude_deg=latitude,
            longitude_deg=longitude,
            elevation_m=_number(
                environment_raw["elevation_m"], "environment.elevation_m"
            ),
            atmospheric_model=atmosphere,
        )

        vehicle_raw = _section(
            raw,
            "vehicle",
            required={
                "radius_m",
                "dry_mass_without_motor_kg",
                "center_of_mass_without_motor_m",
                "drag_coefficient_power_on",
                "drag_coefficient_power_off",
            },
            optional={"weathercock_coefficient"},
        )
        weathercock = _number(
            vehicle_raw.get("weathercock_coefficient", 0.0),
            "vehicle.weathercock_coefficient",
        )
        if weathercock < 0:
            raise ScenarioValidationError(
                "vehicle.weathercock_coefficient must not be negative"
            )
        vehicle = VehicleConfig(
            radius_m=_positive(vehicle_raw["radius_m"], "vehicle.radius_m"),
            dry_mass_without_motor_kg=_positive(
                vehicle_raw["dry_mass_without_motor_kg"],
                "vehicle.dry_mass_without_motor_kg",
            ),
            center_of_mass_without_motor_m=_number(
                vehicle_raw["center_of_mass_without_motor_m"],
                "vehicle.center_of_mass_without_motor_m",
            ),
            drag_coefficient_power_on=_positive(
                vehicle_raw["drag_coefficient_power_on"],
                "vehicle.drag_coefficient_power_on",
            ),
            drag_coefficient_power_off=_positive(
                vehicle_raw["drag_coefficient_power_off"],
                "vehicle.drag_coefficient_power_off",
            ),
            weathercock_coefficient=weathercock,
        )

        motor_raw = _section(
            raw,
            "motor",
            required={
                "thrust_curve",
                "dry_mass_kg",
                "propellant_initial_mass_kg",
                "burn_time_s",
            },
        )
        burn_time = _positive(motor_raw["burn_time_s"], "motor.burn_time_s")
        thrust_curve = _parse_thrust_curve(motor_raw["thrust_curve"], burn_time)
        motor = MotorConfig(
            thrust_curve=thrust_curve,
            dry_mass_kg=_positive(motor_raw["dry_mass_kg"], "motor.dry_mass_kg"),
            propellant_initial_mass_kg=_positive(
                motor_raw["propellant_initial_mass_kg"],
                "motor.propellant_initial_mass_kg",
            ),
            burn_time_s=burn_time,
        )
        if dynamics not in POINT_MASS_DYNAMICS and weathercock != 0:
            raise ScenarioValidationError(
                "vehicle.weathercock_coefficient is only valid for point-mass 3DOF"
            )
        rigid_body = _parse_rigid_body(raw, dynamics)
        recovery = _parse_recovery(raw, dynamics)

        launch_raw = _section(
            raw,
            "launch",
            required={
                "rail_length_m",
                "inclination_deg",
                "heading_deg",
                "max_time_s",
                "max_time_step_s",
            },
        )
        inclination = _number(launch_raw["inclination_deg"], "launch.inclination_deg")
        if not 0 < inclination <= 90:
            raise ScenarioValidationError(
                "launch.inclination_deg must be in the interval (0, 90]"
            )
        heading = _number(launch_raw["heading_deg"], "launch.heading_deg")
        if not 0 <= heading < 360:
            raise ScenarioValidationError(
                "launch.heading_deg must be in the interval [0, 360)"
            )
        max_time = _positive(launch_raw["max_time_s"], "launch.max_time_s")
        if max_time <= burn_time:
            raise ScenarioValidationError(
                "launch.max_time_s must be greater than motor.burn_time_s"
            )
        launch = LaunchConfig(
            rail_length_m=_positive(
                launch_raw["rail_length_m"], "launch.rail_length_m"
            ),
            inclination_deg=inclination,
            heading_deg=heading,
            max_time_s=max_time,
            max_time_step_s=_positive(
                launch_raw["max_time_step_s"], "launch.max_time_step_s"
            ),
        )

        output_raw = _section(
            raw,
            "output",
            required={"sample_interval_s"},
        )
        output = OutputConfig(
            sample_interval_s=_positive(
                output_raw["sample_interval_s"], "output.sample_interval_s"
            )
        )

        return cls(
            schema_version=1,
            name=name.strip(),
            description=description.strip(),
            backend=backend,
            dynamics=dynamics,
            environment=environment,
            vehicle=vehicle,
            motor=motor,
            rigid_body=rigid_body,
            recovery=recovery,
            launch=launch,
            output=output,
        )


def _parse_rigid_body(raw: Mapping[str, Any], dynamics: str) -> RigidBodyConfig | None:
    raw_rigid_body = raw.get("rigid_body")
    if dynamics in POINT_MASS_DYNAMICS:
        if raw_rigid_body is not None:
            raise ScenarioValidationError(
                f"rigid_body is not valid for dynamics={dynamics!r}"
            )
        return None

    rigid_body = _section(
        raw,
        "rigid_body",
        required={
            "vehicle_dry_inertia_kg_m2",
            "coordinate_system_orientation",
            "motor_position_m",
            "motor",
            "nose",
            "fins",
            "rail_buttons",
        },
        optional={"tail"},
    )
    motor_raw = _section(
        rigid_body,
        "motor",
        required={
            "dry_inertia_kg_m2",
            "chamber_radius_m",
            "chamber_height_m",
            "chamber_position_m",
            "nozzle_radius_m",
            "center_of_dry_mass_position_m",
            "nozzle_position_m",
            "coordinate_system_orientation",
        },
    )
    motor = RigidBodyMotorConfig(
        dry_inertia_kg_m2=_positive_tuple3(
            motor_raw["dry_inertia_kg_m2"],
            "rigid_body.motor.dry_inertia_kg_m2",
        ),
        chamber_radius_m=_positive(
            motor_raw["chamber_radius_m"],
            "rigid_body.motor.chamber_radius_m",
        ),
        chamber_height_m=_positive(
            motor_raw["chamber_height_m"],
            "rigid_body.motor.chamber_height_m",
        ),
        chamber_position_m=_number(
            motor_raw["chamber_position_m"],
            "rigid_body.motor.chamber_position_m",
        ),
        nozzle_radius_m=_positive(
            motor_raw["nozzle_radius_m"],
            "rigid_body.motor.nozzle_radius_m",
        ),
        center_of_dry_mass_position_m=_number(
            motor_raw["center_of_dry_mass_position_m"],
            "rigid_body.motor.center_of_dry_mass_position_m",
        ),
        nozzle_position_m=_number(
            motor_raw["nozzle_position_m"],
            "rigid_body.motor.nozzle_position_m",
        ),
        coordinate_system_orientation=_orientation(
            motor_raw["coordinate_system_orientation"],
            "rigid_body.motor.coordinate_system_orientation",
            {
                "nozzle_to_combustion_chamber",
                "combustion_chamber_to_nozzle",
            },
        ),
    )

    nose_raw = _section(
        rigid_body,
        "nose",
        required={"length_m", "kind", "position_m"},
    )
    nose_kind = nose_raw["kind"]
    if not isinstance(nose_kind, str) or not nose_kind.strip():
        raise ScenarioValidationError("rigid_body.nose.kind must be a string")
    nose = NoseConfig(
        length_m=_positive(nose_raw["length_m"], "rigid_body.nose.length_m"),
        kind=nose_kind.strip(),
        position_m=_number(nose_raw["position_m"], "rigid_body.nose.position_m"),
    )

    fins_raw = _section(
        rigid_body,
        "fins",
        required={
            "count",
            "root_chord_m",
            "tip_chord_m",
            "span_m",
            "position_m",
            "cant_angle_deg",
        },
    )
    fin_count = _integer(fins_raw["count"], "rigid_body.fins.count")
    if fin_count < 3:
        raise ScenarioValidationError("rigid_body.fins.count must be at least 3")
    cant_angle = _number(fins_raw["cant_angle_deg"], "rigid_body.fins.cant_angle_deg")
    if not -45 <= cant_angle <= 45:
        raise ScenarioValidationError(
            "rigid_body.fins.cant_angle_deg must be between -45 and 45"
        )
    fins = FinsConfig(
        count=fin_count,
        root_chord_m=_positive(
            fins_raw["root_chord_m"], "rigid_body.fins.root_chord_m"
        ),
        tip_chord_m=_positive(fins_raw["tip_chord_m"], "rigid_body.fins.tip_chord_m"),
        span_m=_positive(fins_raw["span_m"], "rigid_body.fins.span_m"),
        position_m=_number(fins_raw["position_m"], "rigid_body.fins.position_m"),
        cant_angle_deg=cant_angle,
    )

    tail = None
    if "tail" in rigid_body:
        tail_raw = _section(
            rigid_body,
            "tail",
            required={
                "top_radius_m",
                "bottom_radius_m",
                "length_m",
                "position_m",
            },
        )
        tail = TailConfig(
            top_radius_m=_positive(
                tail_raw["top_radius_m"], "rigid_body.tail.top_radius_m"
            ),
            bottom_radius_m=_positive(
                tail_raw["bottom_radius_m"],
                "rigid_body.tail.bottom_radius_m",
            ),
            length_m=_positive(tail_raw["length_m"], "rigid_body.tail.length_m"),
            position_m=_number(tail_raw["position_m"], "rigid_body.tail.position_m"),
        )

    rail_raw = _section(
        rigid_body,
        "rail_buttons",
        required={
            "upper_position_m",
            "lower_position_m",
            "angular_position_deg",
        },
    )
    upper_position = _number(
        rail_raw["upper_position_m"],
        "rigid_body.rail_buttons.upper_position_m",
    )
    lower_position = _number(
        rail_raw["lower_position_m"],
        "rigid_body.rail_buttons.lower_position_m",
    )
    if upper_position == lower_position:
        raise ScenarioValidationError(
            "rigid_body rail button positions must be different"
        )
    angular_position = _number(
        rail_raw["angular_position_deg"],
        "rigid_body.rail_buttons.angular_position_deg",
    )
    if not 0 <= angular_position < 360:
        raise ScenarioValidationError(
            "rigid_body.rail_buttons.angular_position_deg must be in [0, 360)"
        )

    return RigidBodyConfig(
        vehicle_dry_inertia_kg_m2=_positive_tuple3(
            rigid_body["vehicle_dry_inertia_kg_m2"],
            "rigid_body.vehicle_dry_inertia_kg_m2",
        ),
        coordinate_system_orientation=_orientation(
            rigid_body["coordinate_system_orientation"],
            "rigid_body.coordinate_system_orientation",
            {"tail_to_nose", "nose_to_tail"},
        ),
        motor_position_m=_number(
            rigid_body["motor_position_m"],
            "rigid_body.motor_position_m",
        ),
        motor=motor,
        nose=nose,
        fins=fins,
        tail=tail,
        rail_buttons=RailButtonsConfig(
            upper_position_m=upper_position,
            lower_position_m=lower_position,
            angular_position_deg=angular_position,
        ),
    )


def _parse_recovery(raw: Mapping[str, Any], dynamics: str) -> RecoveryConfig | None:
    raw_recovery = raw.get("recovery")
    if dynamics not in RECOVERY_DYNAMICS:
        if raw_recovery is not None:
            raise ScenarioValidationError(
                f"recovery is not valid for dynamics={dynamics!r}"
            )
        return None

    recovery = _section(raw, "recovery", required={"parachutes"})
    raw_parachutes = recovery["parachutes"]
    if not isinstance(raw_parachutes, Sequence) or isinstance(
        raw_parachutes, (str, bytes)
    ):
        raise ScenarioValidationError("recovery.parachutes must be an array")
    if not raw_parachutes:
        raise ScenarioValidationError(
            "recovery.parachutes must contain at least one parachute"
        )

    parachutes: list[ParachuteConfig] = []
    for index, raw_parachute in enumerate(raw_parachutes):
        path = f"recovery.parachutes[{index}]"
        if not isinstance(raw_parachute, Mapping):
            raise ScenarioValidationError(f"{path} must be an object")
        required = {"id", "cd_s_m2", "trigger", "sampling_rate_hz", "lag_s"}
        missing = required - set(raw_parachute)
        unknown = set(raw_parachute) - required
        if missing:
            raise ScenarioValidationError(
                f"{path} is missing fields: {', '.join(sorted(missing))}"
            )
        if unknown:
            raise ScenarioValidationError(
                f"{path} contains unknown fields: {', '.join(sorted(unknown))}"
            )

        parachute_id = raw_parachute["id"]
        if (
            not isinstance(parachute_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", parachute_id) is None
        ):
            raise ScenarioValidationError(f"{path}.id must use lower_snake_case")
        trigger_raw = raw_parachute["trigger"]
        if not isinstance(trigger_raw, Mapping):
            raise ScenarioValidationError(f"{path}.trigger must be an object")
        trigger_kind = trigger_raw.get("kind")
        if trigger_kind == "apogee":
            if set(trigger_raw) != {"kind"}:
                raise ScenarioValidationError(
                    f"{path}.trigger kind='apogee' only accepts the kind field"
                )
            trigger = ParachuteTriggerConfig(
                kind="apogee",
                altitude_agl_m=None,
            )
        elif trigger_kind == "descending_altitude":
            if set(trigger_raw) != {"kind", "altitude_agl_m"}:
                raise ScenarioValidationError(
                    f"{path}.trigger kind='descending_altitude' requires altitude_agl_m"
                )
            trigger = ParachuteTriggerConfig(
                kind="descending_altitude",
                altitude_agl_m=_positive(
                    trigger_raw["altitude_agl_m"],
                    f"{path}.trigger.altitude_agl_m",
                ),
            )
        else:
            raise ScenarioValidationError(
                f"{path}.trigger.kind must be 'apogee' or 'descending_altitude'"
            )

        lag_s = _number(raw_parachute["lag_s"], f"{path}.lag_s")
        if lag_s < 0:
            raise ScenarioValidationError(f"{path}.lag_s must not be negative")
        parachutes.append(
            ParachuteConfig(
                id=parachute_id,
                cd_s_m2=_positive(raw_parachute["cd_s_m2"], f"{path}.cd_s_m2"),
                trigger=trigger,
                sampling_rate_hz=_positive(
                    raw_parachute["sampling_rate_hz"],
                    f"{path}.sampling_rate_hz",
                ),
                lag_s=lag_s,
            )
        )

    ids = [parachute.id for parachute in parachutes]
    if len(set(ids)) != len(ids):
        raise ScenarioValidationError("recovery parachute ids must be unique")
    return RecoveryConfig(parachutes=tuple(parachutes))


def _parse_thrust_curve(
    raw_curve: Any, burn_time_s: float
) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw_curve, Sequence) or isinstance(raw_curve, (str, bytes)):
        raise ScenarioValidationError("motor.thrust_curve must be an array")
    points: list[tuple[float, float]] = []
    for index, raw_point in enumerate(raw_curve):
        if (
            not isinstance(raw_point, Sequence)
            or isinstance(raw_point, (str, bytes))
            or len(raw_point) != 2
        ):
            raise ScenarioValidationError(
                f"motor.thrust_curve[{index}] must be [time_s, thrust_n]"
            )
        time_s = _number(raw_point[0], f"motor.thrust_curve[{index}][0]")
        thrust_n = _number(raw_point[1], f"motor.thrust_curve[{index}][1]")
        if time_s < 0 or thrust_n < 0:
            raise ScenarioValidationError(
                "motor.thrust_curve times and thrust values must not be negative"
            )
        points.append((time_s, thrust_n))
    if len(points) < 2:
        raise ScenarioValidationError("motor.thrust_curve needs at least two points")
    if any(right[0] <= left[0] for left, right in pairwise(points)):
        raise ScenarioValidationError(
            "motor.thrust_curve time values must be strictly increasing"
        )
    if points[0][0] != 0:
        raise ScenarioValidationError("motor.thrust_curve must start at t=0")
    if abs(points[-1][0] - burn_time_s) > 1e-9:
        raise ScenarioValidationError(
            "the final motor.thrust_curve time must equal motor.burn_time_s"
        )
    if max(thrust for _, thrust in points) <= 0:
        raise ScenarioValidationError("motor.thrust_curve must contain positive thrust")
    return tuple(points)


def load_scenario(path: str | Path) -> ScenarioConfig:
    scenario_path = Path(path)
    try:
        raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioValidationError(
            f"scenario file not found: {scenario_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(f"scenario is not valid JSON: {exc.msg}") from exc
    if not isinstance(raw, Mapping):
        raise ScenarioValidationError("scenario root must be an object")
    return ScenarioConfig.from_mapping(raw)
