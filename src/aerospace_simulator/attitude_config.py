from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import ScenarioValidationError

ATTITUDE_CONTRACT_SCHEMA = "wms.aerospace.spacecraft_attitude.v1"
ATTITUDE_TASK_KINDS = (
    "spacecraft_inertial_pointing_gnc",
    "spacecraft_rate_damping_gnc",
)
SUPPORTED_ATTITUDE_CONTROLLERS = ("mrp_feedback_pd", "rate_damping")
SUPPORTED_REACTION_WHEEL_MODELS = ("Honeywell_HR16",)
HR16_MOMENTUM_OPTIONS_N_M_S = (50.0, 75.0, 100.0)
HR16_MAX_MOTOR_TORQUE_N_M = 0.2
HR16_MAX_SPEED_RPM = 6000.0


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioValidationError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ScenarioValidationError(f"{path} must be finite")
    return number


def _positive(value: Any, path: str) -> float:
    number = _number(value, path)
    if number <= 0.0:
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


def _vector3(value: Any, path: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise ScenarioValidationError(f"{path} must contain exactly three numbers")
    result = tuple(
        _number(component, f"{path}[{index}]") for index, component in enumerate(value)
    )
    return result


def _matrix3(value: Any, path: str) -> tuple[tuple[float, float, float], ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise ScenarioValidationError(f"{path} must contain exactly three axes")
    result = tuple(
        _vector3(axis, f"{path}[{index}]") for index, axis in enumerate(value)
    )
    return result


def _require_integer_ratio(numerator: float, denominator: float, path: str) -> None:
    ratio = numerator / denominator
    if not math.isclose(ratio, round(ratio), rel_tol=0.0, abs_tol=1e-9):
        raise ScenarioValidationError(f"{path} must be an integer multiple")


@dataclass(frozen=True)
class AttitudeSpacecraftConfig:
    mass_kg: float
    principal_inertia_kg_m2: tuple[float, float, float]


@dataclass(frozen=True)
class AttitudeInitialState:
    mrp_sigma_bn: tuple[float, float, float]
    angular_velocity_bn_body_rad_s: tuple[float, float, float]


@dataclass(frozen=True)
class AttitudeGNCConfig:
    enabled: bool
    navigation: str
    guidance: str
    reference_mrp_sigma_rn: tuple[float, float, float]
    controller: str
    mrp_gain_n_m: float
    rate_gain_n_m_s: float


@dataclass(frozen=True)
class ReactionWheelArrayConfig:
    model_id: str
    spin_axes_body: tuple[tuple[float, float, float], ...]
    initial_speed_rpm: tuple[float, float, float]
    max_momentum_n_m_s: float
    max_motor_torque_n_m: float
    max_speed_rpm: float


@dataclass(frozen=True)
class AttitudePropagationConfig:
    duration_s: float
    integrator: str
    step_size_s: float
    output_interval_s: float


@dataclass(frozen=True)
class SpacecraftAttitudeConfig:
    schema_version: int
    name: str
    description: str
    backend: str
    dynamics: str
    spacecraft: AttitudeSpacecraftConfig
    initial_state: AttitudeInitialState
    gnc: AttitudeGNCConfig
    reaction_wheels: ReactionWheelArrayConfig
    propagation: AttitudePropagationConfig

    def protocol_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "spacecraft": asdict(self.spacecraft),
            "initial_state": asdict(self.initial_state),
            "gnc": asdict(self.gnc),
            "reaction_wheels": asdict(self.reaction_wheels),
            "propagation": asdict(self.propagation),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> SpacecraftAttitudeConfig:
        required_root = {
            "schema_version",
            "name",
            "description",
            "backend",
            "dynamics",
            "spacecraft",
            "initial_state",
            "gnc",
            "reaction_wheels",
            "propagation",
        }
        missing_root = required_root - set(raw)
        unknown_root = set(raw) - required_root
        if missing_root:
            raise ScenarioValidationError(
                "attitude scenario is missing fields: "
                + ", ".join(sorted(missing_root))
            )
        if unknown_root:
            raise ScenarioValidationError(
                "attitude scenario contains unknown fields: "
                + ", ".join(sorted(unknown_root))
            )
        if raw["schema_version"] != 1:
            raise ScenarioValidationError("schema_version must be 1")

        name = _required_string(raw["name"], "name")
        description = _required_string(raw["description"], "description")
        backend = _required_string(raw["backend"], "backend")
        dynamics = _required_string(raw["dynamics"], "dynamics")
        if dynamics not in ATTITUDE_TASK_KINDS:
            choices = ", ".join(ATTITUDE_TASK_KINDS)
            raise ScenarioValidationError(f"dynamics must be one of: {choices}")

        spacecraft_raw = _section(
            raw,
            "spacecraft",
            required={"mass_kg", "principal_inertia_kg_m2"},
        )
        inertia = _vector3(
            spacecraft_raw["principal_inertia_kg_m2"],
            "spacecraft.principal_inertia_kg_m2",
        )
        if any(moment <= 0.0 for moment in inertia):
            raise ScenarioValidationError(
                "spacecraft.principal_inertia_kg_m2 values must be greater than zero"
            )
        for index, moment in enumerate(inertia):
            other_sum = sum(inertia) - moment
            if moment >= other_sum:
                raise ScenarioValidationError(
                    "spacecraft.principal_inertia_kg_m2 must satisfy the "
                    f"principal-moment triangle inequality at index {index}"
                )
        spacecraft = AttitudeSpacecraftConfig(
            mass_kg=_bounded(
                spacecraft_raw["mass_kg"],
                "spacecraft.mass_kg",
                1.0,
                100_000.0,
            ),
            principal_inertia_kg_m2=inertia,
        )

        initial_raw = _section(
            raw,
            "initial_state",
            required={"mrp_sigma_bn", "angular_velocity_bn_body_rad_s"},
        )
        initial_mrp = _vector3(
            initial_raw["mrp_sigma_bn"],
            "initial_state.mrp_sigma_bn",
        )
        if math.sqrt(sum(component**2 for component in initial_mrp)) >= 1.0:
            raise ScenarioValidationError(
                "initial_state.mrp_sigma_bn must lie in the principal MRP set"
            )
        initial_rate = _vector3(
            initial_raw["angular_velocity_bn_body_rad_s"],
            "initial_state.angular_velocity_bn_body_rad_s",
        )
        if any(abs(component) > 1.0 for component in initial_rate):
            raise ScenarioValidationError(
                "initial_state.angular_velocity_bn_body_rad_s components "
                "must be in [-1, 1]"
            )
        initial_state = AttitudeInitialState(
            mrp_sigma_bn=initial_mrp,
            angular_velocity_bn_body_rad_s=initial_rate,
        )

        gnc_raw = _section(
            raw,
            "gnc",
            required={
                "enabled",
                "navigation",
                "guidance",
                "reference_mrp_sigma_rn",
                "controller",
                "mrp_gain_n_m",
                "rate_gain_n_m_s",
            },
        )
        navigation = _required_string(gnc_raw["navigation"], "gnc.navigation")
        if navigation != "simple_nav_perfect":
            raise ScenarioValidationError("gnc.navigation must be 'simple_nav_perfect'")
        guidance = _required_string(gnc_raw["guidance"], "gnc.guidance")
        if guidance != "inertial_fixed_mrp":
            raise ScenarioValidationError("gnc.guidance must be 'inertial_fixed_mrp'")
        gnc_enabled = _boolean(gnc_raw["enabled"], "gnc.enabled")
        controller = _required_string(gnc_raw["controller"], "gnc.controller")
        if controller not in SUPPORTED_ATTITUDE_CONTROLLERS:
            choices = ", ".join(SUPPORTED_ATTITUDE_CONTROLLERS)
            raise ScenarioValidationError(f"gnc.controller must be one of: {choices}")
        if dynamics == "spacecraft_rate_damping_gnc":
            if not gnc_enabled:
                raise ScenarioValidationError(
                    "spacecraft_rate_damping_gnc requires gnc.enabled=true"
                )
            if controller != "rate_damping":
                raise ScenarioValidationError(
                    "spacecraft_rate_damping_gnc requires gnc.controller='rate_damping'"
                )
        elif controller != "mrp_feedback_pd":
            raise ScenarioValidationError(
                "spacecraft_inertial_pointing_gnc requires "
                "gnc.controller='mrp_feedback_pd'"
            )
        reference_mrp = _vector3(
            gnc_raw["reference_mrp_sigma_rn"],
            "gnc.reference_mrp_sigma_rn",
        )
        if math.sqrt(sum(component**2 for component in reference_mrp)) >= 1.0:
            raise ScenarioValidationError(
                "gnc.reference_mrp_sigma_rn must lie in the principal MRP set"
            )
        mrp_gain = _number(gnc_raw["mrp_gain_n_m"], "gnc.mrp_gain_n_m")
        if controller == "rate_damping":
            if mrp_gain != 0.0:
                raise ScenarioValidationError(
                    "gnc.mrp_gain_n_m must be zero for rate_damping"
                )
        elif mrp_gain <= 0.0:
            raise ScenarioValidationError(
                "gnc.mrp_gain_n_m must be greater than zero for mrp_feedback_pd"
            )
        gnc = AttitudeGNCConfig(
            enabled=gnc_enabled,
            navigation=navigation,
            guidance=guidance,
            reference_mrp_sigma_rn=reference_mrp,
            controller=controller,
            mrp_gain_n_m=mrp_gain,
            rate_gain_n_m_s=_positive(
                gnc_raw["rate_gain_n_m_s"],
                "gnc.rate_gain_n_m_s",
            ),
        )

        wheel_raw = _section(
            raw,
            "reaction_wheels",
            required={
                "model_id",
                "spin_axes_body",
                "initial_speed_rpm",
                "max_momentum_n_m_s",
                "max_motor_torque_n_m",
                "max_speed_rpm",
            },
        )
        model_id = _required_string(
            wheel_raw["model_id"],
            "reaction_wheels.model_id",
        )
        if model_id not in SUPPORTED_REACTION_WHEEL_MODELS:
            choices = ", ".join(SUPPORTED_REACTION_WHEEL_MODELS)
            raise ScenarioValidationError(
                f"reaction_wheels.model_id must be one of: {choices}"
            )
        spin_axes = _matrix3(
            wheel_raw["spin_axes_body"],
            "reaction_wheels.spin_axes_body",
        )
        for index, axis in enumerate(spin_axes):
            norm = math.sqrt(sum(component**2 for component in axis))
            if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-9):
                raise ScenarioValidationError(
                    f"reaction_wheels.spin_axes_body[{index}] must be a unit vector"
                )
        for first_index in range(3):
            for second_index in range(first_index + 1, 3):
                dot = sum(
                    first * second
                    for first, second in zip(
                        spin_axes[first_index],
                        spin_axes[second_index],
                        strict=True,
                    )
                )
                if not math.isclose(dot, 0.0, rel_tol=0.0, abs_tol=1e-9):
                    raise ScenarioValidationError(
                        "reaction_wheels.spin_axes_body must be mutually orthogonal"
                    )
        initial_speed = _vector3(
            wheel_raw["initial_speed_rpm"],
            "reaction_wheels.initial_speed_rpm",
        )
        max_speed = _positive(
            wheel_raw["max_speed_rpm"],
            "reaction_wheels.max_speed_rpm",
        )
        if not math.isclose(
            max_speed,
            HR16_MAX_SPEED_RPM,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ScenarioValidationError(
                f"reaction_wheels.max_speed_rpm must be {HR16_MAX_SPEED_RPM:g} "
                "for Honeywell_HR16"
            )
        if any(abs(speed) > max_speed for speed in initial_speed):
            raise ScenarioValidationError(
                "reaction_wheels.initial_speed_rpm exceeds max_speed_rpm"
            )
        max_momentum = _positive(
            wheel_raw["max_momentum_n_m_s"],
            "reaction_wheels.max_momentum_n_m_s",
        )
        if max_momentum not in HR16_MOMENTUM_OPTIONS_N_M_S:
            choices = ", ".join(f"{value:g}" for value in HR16_MOMENTUM_OPTIONS_N_M_S)
            raise ScenarioValidationError(
                "reaction_wheels.max_momentum_n_m_s must be one of: " + choices
            )
        max_motor_torque = _positive(
            wheel_raw["max_motor_torque_n_m"],
            "reaction_wheels.max_motor_torque_n_m",
        )
        if not math.isclose(
            max_motor_torque,
            HR16_MAX_MOTOR_TORQUE_N_M,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ScenarioValidationError(
                "reaction_wheels.max_motor_torque_n_m must be "
                f"{HR16_MAX_MOTOR_TORQUE_N_M:g} for Honeywell_HR16"
            )
        reaction_wheels = ReactionWheelArrayConfig(
            model_id=model_id,
            spin_axes_body=spin_axes,
            initial_speed_rpm=initial_speed,
            max_momentum_n_m_s=max_momentum,
            max_motor_torque_n_m=max_motor_torque,
            max_speed_rpm=max_speed,
        )

        propagation_raw = _section(
            raw,
            "propagation",
            required={
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
        duration = _bounded(
            propagation_raw["duration_s"],
            "propagation.duration_s",
            0.1,
            3600.0,
        )
        step_size = _bounded(
            propagation_raw["step_size_s"],
            "propagation.step_size_s",
            0.001,
            1.0,
        )
        output_interval = _positive(
            propagation_raw["output_interval_s"],
            "propagation.output_interval_s",
        )
        if output_interval < step_size:
            raise ScenarioValidationError(
                "propagation.output_interval_s must be at least step_size_s"
            )
        _require_integer_ratio(duration, step_size, "propagation.duration_s")
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
        propagation = AttitudePropagationConfig(
            duration_s=duration,
            integrator=integrator,
            step_size_s=step_size,
            output_interval_s=output_interval,
        )

        result = cls(
            schema_version=1,
            name=name,
            description=description,
            backend=backend,
            dynamics=dynamics,
            spacecraft=spacecraft,
            initial_state=initial_state,
            gnc=gnc,
            reaction_wheels=reaction_wheels,
            propagation=propagation,
        )
        return result


def load_attitude_scenario(path: str | Path) -> SpacecraftAttitudeConfig:
    scenario_path = Path(path)
    try:
        raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioValidationError(
            f"attitude scenario file not found: {scenario_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(
            f"attitude scenario is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise ScenarioValidationError("attitude scenario root must be an object")
    result = SpacecraftAttitudeConfig.from_mapping(raw)
    return result
