from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from .aircraft_config import (
    AIRCRAFT_CONTRACT_SCHEMA,
    AircraftFlightConfig,
)
from .attitude_config import (
    ATTITUDE_CONTRACT_SCHEMA,
    SpacecraftAttitudeConfig,
)
from .config import ScenarioConfig
from .launch_config import (
    LAUNCH_CONTRACT_SCHEMA,
    LaunchToOrbitConfig,
)
from .orbit_config import (
    ORBIT_CONTRACT_SCHEMA,
    OrbitPropagationConfig,
)
from .parameter_definitions import parameter_catalog
from .protocol import BackendCapabilities, SimulationRequest
from .request_io import AEROSPACE_CONTRACT_SCHEMA


class TaskFamilyRegistryError(RuntimeError):
    """Raised when task-family declarations are incomplete or ambiguous."""


class TaskFamilyNotFoundError(TaskFamilyRegistryError):
    """Raised when a task family or request mapping is unavailable."""


@dataclass(frozen=True)
class ComponentSpec:
    component_id: str
    slot: str
    backend_ids: tuple[str, ...]
    task_kinds: tuple[str, ...]
    parameter_schema: dict[str, object]

    def __post_init__(self) -> None:
        for name, value in (
            ("component_id", self.component_id),
            ("slot", self.slot),
        ):
            if not value:
                raise TaskFamilyRegistryError(f"{name} must not be empty")
        if not self.backend_ids:
            raise TaskFamilyRegistryError(
                f"component {self.component_id!r} must declare a backend"
            )
        if not self.task_kinds:
            raise TaskFamilyRegistryError(
                f"component {self.component_id!r} must declare task kinds"
            )

    def document(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "slot": self.slot,
            "backend_ids": list(self.backend_ids),
            "task_kinds": list(self.task_kinds),
            "parameter_schema": self.parameter_schema,
        }


@dataclass(frozen=True)
class VariantSelector:
    path: str
    equals: object

    def __post_init__(self) -> None:
        if not self.path or any(not part for part in self.path.split(".")):
            raise TaskFamilyRegistryError(
                "variant selector path must contain non-empty field names"
            )

    def matches(self, contract: object) -> bool:
        value = contract
        for part in self.path.split("."):
            try:
                value = value[part] if isinstance(value, dict) else getattr(value, part)
            except (AttributeError, KeyError) as exc:
                raise TaskFamilyRegistryError(
                    f"contract has no selector path {self.path!r}"
                ) from exc
        return value == self.equals

    def document(self) -> dict[str, object]:
        return {"path": self.path, "equals": self.equals}


@dataclass(frozen=True)
class AssistantParameterSpec:
    path: str
    description_en: str
    description_zh: str
    unit: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            (
                self.path,
                self.description_en,
                self.description_zh,
                self.unit,
            )
        ):
            raise TaskFamilyRegistryError(
                "assistant parameter metadata fields must not be empty"
            )
        if any(not part for part in self.path.split(".")):
            raise TaskFamilyRegistryError(
                "assistant parameter path must contain non-empty field names"
            )

    def document(self) -> dict[str, object]:
        return {
            "path": self.path,
            "description_en": self.description_en,
            "description_zh": self.description_zh,
            "unit": self.unit,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class AssistantVariantMetadata:
    summary_en: str
    summary_zh: str
    aliases: tuple[str, ...]
    selection_cues: tuple[str, ...]
    exclusion_cues: tuple[str, ...]
    clarification_topics: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all((self.summary_en, self.summary_zh)):
            raise TaskFamilyRegistryError(
                "assistant variant summaries must not be empty"
            )
        if not self.aliases or not self.selection_cues:
            raise TaskFamilyRegistryError(
                "assistant variant metadata requires aliases and selection cues"
            )

    def document(self) -> dict[str, object]:
        return {
            "summary_en": self.summary_en,
            "summary_zh": self.summary_zh,
            "aliases": list(self.aliases),
            "selection_cues": list(self.selection_cues),
            "exclusion_cues": list(self.exclusion_cues),
            "clarification_topics": list(self.clarification_topics),
        }


@dataclass(frozen=True)
class TaskVariantSpec:
    variant_id: str
    task_kind: str
    component_ids: tuple[str, ...]
    selectors: tuple[VariantSelector, ...] = ()
    assistant: AssistantVariantMetadata | None = None

    def __post_init__(self) -> None:
        if not self.variant_id:
            raise TaskFamilyRegistryError("variant_id must not be empty")
        if not self.task_kind:
            raise TaskFamilyRegistryError("variant task_kind must not be empty")
        if not self.component_ids:
            raise TaskFamilyRegistryError(
                f"variant {self.variant_id!r} must declare components"
            )
        paths = [selector.path for selector in self.selectors]
        if len(set(paths)) != len(paths):
            raise TaskFamilyRegistryError(
                f"variant {self.variant_id!r} has duplicate selector paths"
            )

    def matches(self, request: SimulationRequest) -> bool:
        return request.task_kind == self.task_kind and all(
            selector.matches(request.contract) for selector in self.selectors
        )

    def document(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "task_kind": self.task_kind,
            "component_ids": list(self.component_ids),
            "selectors": [selector.document() for selector in self.selectors],
            "assistant": (
                self.assistant.document() if self.assistant is not None else None
            ),
        }


@dataclass(frozen=True)
class TaskFamilySpec:
    family_id: str
    family_schema: str
    contract_schema: str
    backend_ids: tuple[str, ...]
    contract_type: type[Any]
    default_variant_id: str
    variants: tuple[TaskVariantSpec, ...]
    components: tuple[ComponentSpec, ...]
    assistant_parameters: tuple[AssistantParameterSpec, ...] = ()

    def __post_init__(self) -> None:
        identity = (
            self.family_id,
            self.family_schema,
            self.contract_schema,
            self.default_variant_id,
        )
        if any(not value for value in identity):
            raise TaskFamilyRegistryError(
                "task-family identity fields must not be empty"
            )
        if not self.backend_ids:
            raise TaskFamilyRegistryError(
                f"family {self.family_id!r} must declare a backend"
            )
        variant_ids = [variant.variant_id for variant in self.variants]
        component_ids = [component.component_id for component in self.components]
        parameter_paths = [parameter.path for parameter in self.assistant_parameters]
        if len(set(variant_ids)) != len(variant_ids):
            raise TaskFamilyRegistryError(
                f"family {self.family_id!r} has duplicate variant ids"
            )
        if len(set(component_ids)) != len(component_ids):
            raise TaskFamilyRegistryError(
                f"family {self.family_id!r} has duplicate component ids"
            )
        if len(set(parameter_paths)) != len(parameter_paths):
            raise TaskFamilyRegistryError(
                f"family {self.family_id!r} has duplicate assistant parameter paths"
            )
        if self.default_variant_id not in set(variant_ids):
            raise TaskFamilyRegistryError(
                f"family {self.family_id!r} default variant is not registered"
            )
        declared_components = set(component_ids)
        signatures: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for variant in self.variants:
            if variant.assistant is None:
                raise TaskFamilyRegistryError(
                    f"variant {variant.variant_id!r} lacks assistant metadata"
                )
            signature = (
                variant.task_kind,
                tuple(
                    (selector.path, repr(selector.equals))
                    for selector in variant.selectors
                ),
            )
            if signature in signatures:
                raise TaskFamilyRegistryError(
                    f"family {self.family_id!r} has ambiguous variant selectors"
                )
            signatures.add(signature)
            missing = set(variant.component_ids) - declared_components
            if missing:
                raise TaskFamilyRegistryError(
                    f"variant {variant.variant_id!r} references unknown "
                    f"components: {', '.join(sorted(missing))}"
                )

    @property
    def task_kinds(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(variant.task_kind for variant in self.variants))

    @property
    def component_ids(self) -> tuple[str, ...]:
        return tuple(component.component_id for component in self.components)

    def variant(self, variant_id: str) -> TaskVariantSpec:
        try:
            return next(
                variant for variant in self.variants if variant.variant_id == variant_id
            )
        except StopIteration as exc:
            raise TaskFamilyNotFoundError(
                f"family {self.family_id!r} has no variant {variant_id!r}"
            ) from exc

    def variant_for_request(
        self,
        request: SimulationRequest,
    ) -> TaskVariantSpec:
        candidates = [variant for variant in self.variants if variant.matches(request)]
        if not candidates:
            raise TaskFamilyNotFoundError(
                f"family {self.family_id!r} has no variant matching request "
                f"{request.request_id!r}"
            )
        if len(candidates) > 1:
            raise TaskFamilyRegistryError(
                f"family {self.family_id!r} has multiple variants matching "
                f"request {request.request_id!r}"
            )
        return candidates[0]

    def schema_document(self) -> dict[str, object]:
        schema = TypeAdapter(self.contract_type).json_schema()
        schema["$id"] = self.family_schema
        schema["x-wms-family-id"] = self.family_id
        schema["x-wms-contract-schema"] = self.contract_schema
        schema["x-wms-components"] = [
            component.document() for component in self.components
        ]
        schema["x-wms-assistant-parameters"] = [
            parameter.document() for parameter in self.assistant_parameters
        ]
        schema["x-wms-parameter-definitions-url"] = "/api/parameter-definitions"
        schema["x-wms-parameter-definitions"] = parameter_catalog().document(
            family_id=self.family_id
        )
        properties = schema.get("properties")
        if isinstance(properties, dict):
            dynamics = properties.get("dynamics")
            if isinstance(dynamics, dict):
                dynamics["enum"] = list(self.task_kinds)
            backend = properties.get("backend")
            if isinstance(backend, dict):
                backend["enum"] = list(self.backend_ids)
        return schema

    def summary_document(self) -> dict[str, object]:
        return {
            "family_id": self.family_id,
            "family_schema": self.family_schema,
            "contract_schema": self.contract_schema,
            "backend_ids": list(self.backend_ids),
            "default_variant_id": self.default_variant_id,
            "variant_count": len(self.variants),
            "component_count": len(self.components),
            "task_kinds": list(self.task_kinds),
        }

    def document(self) -> dict[str, object]:
        return {
            **self.summary_document(),
            "variants": [variant.document() for variant in self.variants],
            "components": [component.document() for component in self.components],
            "assistant_parameters": [
                parameter.document() for parameter in self.assistant_parameters
            ],
        }


class TaskFamilyRegistry:
    def __init__(self) -> None:
        self._families: dict[str, TaskFamilySpec] = {}

    def register(self, family: TaskFamilySpec) -> None:
        if family.family_id in self._families:
            raise TaskFamilyRegistryError(
                f"task family is already registered: {family.family_id}"
            )
        parameter_catalog().document(family_id=family.family_id)
        for existing in self._families.values():
            overlap = set(existing.task_kinds) & set(family.task_kinds)
            if overlap and existing.contract_schema == family.contract_schema:
                raise TaskFamilyRegistryError(
                    "task-family request mapping is ambiguous: "
                    + ", ".join(sorted(overlap))
                )
        self._families[family.family_id] = family

    def families(self) -> tuple[TaskFamilySpec, ...]:
        return tuple(self._families[key] for key in sorted(self._families))

    def get(self, family_id: str) -> TaskFamilySpec:
        try:
            return self._families[family_id]
        except KeyError as exc:
            raise TaskFamilyNotFoundError(
                f"task family not found: {family_id}"
            ) from exc

    def family_for_request(self, request: SimulationRequest) -> TaskFamilySpec:
        matches = [
            family
            for family in self._families.values()
            if request.contract_schema == family.contract_schema
            and request.task_kind in family.task_kinds
        ]
        if not matches:
            raise TaskFamilyNotFoundError(
                f"no task family maps task {request.task_kind!r} with "
                f"contract {request.contract_schema!r}"
            )
        if len(matches) > 1:
            raise TaskFamilyRegistryError(
                f"multiple task families map task {request.task_kind!r}"
            )
        return matches[0]

    def describe_request(self, request: SimulationRequest) -> dict[str, object]:
        family = self.family_for_request(request)
        variant = family.variant_for_request(request)
        return {
            "family_id": family.family_id,
            "family_schema": family.family_schema,
            "variant_id": variant.variant_id,
            "component_ids": list(variant.component_ids),
        }

    def validate_backend_capabilities(
        self,
        capabilities: tuple[BackendCapabilities, ...],
    ) -> None:
        by_id = {capability.backend_id: capability for capability in capabilities}
        for family in self._families.values():
            for backend_id in family.backend_ids:
                try:
                    backend = by_id[backend_id]
                except KeyError as exc:
                    raise TaskFamilyRegistryError(
                        f"family {family.family_id!r} requires unregistered "
                        f"backend {backend_id!r}"
                    ) from exc
                if family.family_id not in backend.supported_family_ids:
                    raise TaskFamilyRegistryError(
                        f"backend {backend_id!r} does not declare family "
                        f"{family.family_id!r}"
                    )
                missing = set(family.component_ids) - set(
                    backend.supported_component_ids
                )
                if missing:
                    raise TaskFamilyRegistryError(
                        f"backend {backend_id!r} is missing components: "
                        + ", ".join(sorted(missing))
                    )


def create_default_task_family_registry() -> TaskFamilyRegistry:
    registry = TaskFamilyRegistry()
    registry.register(_rocket_family())
    registry.register(_launch_family())
    registry.register(_orbit_family())
    registry.register(_aircraft_family())
    registry.register(_spacecraft_family())
    return registry


def _empty_schema() -> dict[str, object]:
    return {"type": "object", "additionalProperties": False}


def _component(
    component_id: str,
    slot: str,
    backend_id: str,
    task_kinds: tuple[str, ...],
    parameter_schema: dict[str, object] | None = None,
) -> ComponentSpec:
    return ComponentSpec(
        component_id=component_id,
        slot=slot,
        backend_ids=(backend_id,),
        task_kinds=task_kinds,
        parameter_schema=parameter_schema or _empty_schema(),
    )


def _assistant_variant(
    summary_en: str,
    summary_zh: str,
    *,
    aliases: tuple[str, ...],
    selection_cues: tuple[str, ...],
    exclusion_cues: tuple[str, ...],
    clarification_topics: tuple[str, ...],
) -> AssistantVariantMetadata:
    return AssistantVariantMetadata(
        summary_en=summary_en,
        summary_zh=summary_zh,
        aliases=aliases,
        selection_cues=selection_cues,
        exclusion_cues=exclusion_cues,
        clarification_topics=clarification_topics,
    )


def _parameter(
    path: str,
    description_en: str,
    description_zh: str,
    unit: str,
    *aliases: str,
) -> AssistantParameterSpec:
    return AssistantParameterSpec(
        path=path,
        description_en=description_en,
        description_zh=description_zh,
        unit=unit,
        aliases=aliases,
    )


def _rocket_assistant_parameters() -> tuple[AssistantParameterSpec, ...]:
    return (
        _parameter(
            "environment.elevation_m",
            "launch-site elevation above mean sea level",
            "发射场海拔高度",
            "m",
            "elevation",
            "altitude",
            "海拔",
        ),
        _parameter(
            "vehicle.dry_mass_without_motor_kg",
            "vehicle dry mass excluding the motor",
            "不含发动机的箭体干质量",
            "kg",
            "vehicle mass",
            "dry mass",
            "箭体质量",
        ),
        _parameter(
            "motor.dry_mass_kg",
            "motor dry mass",
            "发动机干质量",
            "kg",
            "motor mass",
            "发动机质量",
        ),
        _parameter(
            "motor.propellant_initial_mass_kg",
            "initial propellant mass",
            "初始推进剂质量",
            "kg",
            "propellant",
            "推进剂",
        ),
        _parameter(
            "motor.burn_time_s",
            "motor burn duration",
            "发动机燃烧时间",
            "s",
            "burn time",
            "燃烧时间",
        ),
        _parameter(
            "launch.rail_length_m",
            "launch rail length",
            "发射导轨长度",
            "m",
            "rail length",
            "导轨长度",
        ),
        _parameter(
            "launch.inclination_deg",
            "launch inclination measured from horizontal",
            "发射倾角（相对水平面）",
            "deg",
            "inclination",
            "launch angle",
            "发射倾角",
        ),
        _parameter(
            "launch.heading_deg",
            "launch azimuth heading",
            "发射航向角",
            "deg",
            "heading",
            "azimuth",
            "航向",
            "方位角",
        ),
        _parameter(
            "launch.max_time_s",
            "maximum simulated flight time",
            "最大仿真飞行时间",
            "s",
            "duration",
            "仿真时间",
        ),
        _parameter(
            "output.sample_interval_s",
            "output sampling interval",
            "输出采样间隔",
            "s",
            "sample interval",
            "采样间隔",
        ),
    )


def _orbit_assistant_parameters() -> tuple[AssistantParameterSpec, ...]:
    return (
        _parameter(
            "spacecraft.mass_kg",
            "spacecraft mass",
            "航天器质量",
            "kg",
            "satellite mass",
            "卫星质量",
        ),
        _parameter(
            "aerodynamics.reference_area_m2",
            "aerodynamic reference area",
            "气动参考面积",
            "m^2",
            "drag area",
            "迎风面积",
        ),
        _parameter(
            "aerodynamics.drag_coefficient",
            "dimensionless drag coefficient",
            "无量纲阻力系数",
            "1",
            "Cd",
            "阻力系数",
        ),
        _parameter(
            "initial_state.semi_major_axis_m",
            "osculating semi-major axis",
            "瞬时轨道半长轴",
            "m",
            "semi-major axis",
            "半长轴",
            "orbit radius",
        ),
        _parameter(
            "initial_state.eccentricity",
            "osculating eccentricity",
            "瞬时轨道偏心率",
            "1",
            "eccentricity",
            "偏心率",
        ),
        _parameter(
            "initial_state.inclination_deg",
            "orbital inclination",
            "轨道倾角",
            "deg",
            "inclination",
            "轨道倾角",
        ),
        _parameter(
            "initial_state.raan_deg",
            "right ascension of the ascending node",
            "升交点赤经",
            "deg",
            "RAAN",
            "升交点赤经",
        ),
        _parameter(
            "initial_state.argument_of_periapsis_deg",
            "argument of periapsis",
            "近地点幅角",
            "deg",
            "argument of periapsis",
            "近地点幅角",
        ),
        _parameter(
            "initial_state.true_anomaly_deg",
            "initial true anomaly",
            "初始真近点角",
            "deg",
            "true anomaly",
            "真近点角",
        ),
        _parameter(
            "propagation.duration_s",
            "orbit propagation duration",
            "轨道传播时长",
            "s",
            "duration",
            "传播时间",
        ),
        _parameter(
            "propagation.step_size_s",
            "fixed integrator step size",
            "固定积分步长",
            "s",
            "time step",
            "积分步长",
        ),
        _parameter(
            "propagation.output_interval_s",
            "state output interval",
            "状态输出间隔",
            "s",
            "output interval",
            "输出间隔",
        ),
    )


def _launch_assistant_parameters() -> tuple[AssistantParameterSpec, ...]:
    return (
        _parameter(
            "launch_site.latitude_deg",
            "launch-site geodetic latitude",
            "发射场大地纬度",
            "deg",
            "launch latitude",
            "发射场纬度",
        ),
        _parameter(
            "launch_site.longitude_deg",
            "launch-site geodetic longitude",
            "发射场大地经度",
            "deg",
            "launch longitude",
            "发射场经度",
        ),
        _parameter(
            "vehicle.payload_mass_kg",
            "payload mass delivered with the upper stage",
            "随上面级送入轨道的有效载荷质量",
            "kg",
            "payload",
            "有效载荷",
        ),
        _parameter(
            "vehicle.reference_area_m2",
            "launch-vehicle aerodynamic reference area",
            "运载火箭气动参考面积",
            "m^2",
            "reference area",
            "气动面积",
        ),
        _parameter(
            "vehicle.drag_coefficient",
            "launch-vehicle drag coefficient",
            "运载火箭阻力系数",
            "1",
            "Cd",
            "阻力系数",
        ),
        _parameter(
            "target_orbit.altitude_m",
            "target circular-orbit altitude",
            "目标近圆轨道高度",
            "m",
            "target altitude",
            "目标轨道高度",
        ),
        _parameter(
            "target_orbit.altitude_tolerance_m",
            "allowed periapsis and apoapsis altitude error",
            "近地点和远地点允许高度误差",
            "m",
            "orbit tolerance",
            "轨道容差",
        ),
        _parameter(
            "target_orbit.maximum_eccentricity",
            "maximum accepted insertion eccentricity",
            "允许的最大入轨偏心率",
            "1",
            "eccentricity limit",
            "偏心率上限",
        ),
        _parameter(
            "propagation.step_size_s",
            "fixed TudatPy integration step",
            "TudatPy 固定积分步长",
            "s",
            "time step",
            "积分步长",
        ),
        _parameter(
            "propagation.output_interval_s",
            "normalized result output interval",
            "归一化结果输出间隔",
            "s",
            "output interval",
            "输出间隔",
        ),
        _parameter(
            "propagation.post_insertion_coast_duration_s",
            "unpowered orbital verification duration after insertion",
            "入轨后的无动力轨道验证时长",
            "s",
            "coast duration",
            "入轨滑行时间",
        ),
    )


def _aircraft_assistant_parameters() -> tuple[AssistantParameterSpec, ...]:
    return (
        _parameter(
            "initial_condition.latitude_deg",
            "initial geodetic latitude",
            "初始大地纬度",
            "deg",
            "latitude",
            "纬度",
        ),
        _parameter(
            "initial_condition.longitude_deg",
            "initial geodetic longitude",
            "初始大地经度",
            "deg",
            "longitude",
            "经度",
        ),
        _parameter(
            "initial_condition.altitude_msl_m",
            "initial altitude above mean sea level",
            "初始海拔高度",
            "m",
            "altitude",
            "飞行高度",
            "海拔",
        ),
        _parameter(
            "initial_condition.calibrated_airspeed_m_s",
            "initial calibrated airspeed",
            "初始校准空速",
            "m/s",
            "airspeed",
            "speed",
            "空速",
            "速度",
        ),
        _parameter(
            "initial_condition.flight_path_angle_deg",
            "initial flight-path angle",
            "初始航迹倾角",
            "deg",
            "flight path angle",
            "航迹角",
        ),
        _parameter(
            "initial_condition.heading_deg",
            "initial true heading",
            "初始真航向",
            "deg",
            "heading",
            "航向",
        ),
        _parameter(
            "environment.wind_north_m_s",
            "northward wind component",
            "北向风速分量",
            "m/s",
            "north wind",
            "北风分量",
        ),
        _parameter(
            "environment.wind_east_m_s",
            "eastward wind component",
            "东向风速分量",
            "m/s",
            "east wind",
            "东风分量",
        ),
        _parameter(
            "environment.wind_down_m_s",
            "downward wind component",
            "向下风速分量",
            "m/s",
            "vertical wind",
            "垂直风",
        ),
        _parameter(
            "propagation.duration_s",
            "flight simulation duration",
            "飞行仿真时长",
            "s",
            "duration",
            "仿真时间",
        ),
        _parameter(
            "propagation.step_size_s",
            "fixed dynamics step size",
            "固定动力学步长",
            "s",
            "time step",
            "步长",
        ),
        _parameter(
            "propagation.output_interval_s",
            "state output interval",
            "状态输出间隔",
            "s",
            "output interval",
            "输出间隔",
        ),
    )


def _spacecraft_assistant_parameters() -> tuple[AssistantParameterSpec, ...]:
    return (
        _parameter(
            "spacecraft.mass_kg",
            "rigid-spacecraft mass",
            "刚性航天器质量",
            "kg",
            "spacecraft mass",
            "航天器质量",
        ),
        _parameter(
            "spacecraft.principal_inertia_kg_m2",
            "principal moments of inertia [Ixx, Iyy, Izz]",
            "主惯量 [Ixx, Iyy, Izz]",
            "kg*m^2",
            "inertia",
            "主惯量",
        ),
        _parameter(
            "initial_state.mrp_sigma_bn",
            "initial body attitude in modified Rodrigues parameters",
            "初始本体姿态修正罗德里格斯参数",
            "1",
            "initial MRP",
            "初始姿态",
        ),
        _parameter(
            "initial_state.angular_velocity_bn_body_rad_s",
            "initial body angular velocity [wx, wy, wz]",
            "初始本体角速度 [wx, wy, wz]",
            "rad/s",
            "angular velocity",
            "body rate",
            "角速度",
        ),
        _parameter(
            "gnc.reference_mrp_sigma_rn",
            "inertial attitude reference in MRP coordinates",
            "惯性姿态参考 MRP",
            "1",
            "reference attitude",
            "目标姿态",
        ),
        _parameter(
            "gnc.mrp_gain_n_m",
            "MRP attitude feedback gain",
            "MRP 姿态反馈增益",
            "N*m",
            "attitude gain",
            "K gain",
            "姿态增益",
        ),
        _parameter(
            "gnc.rate_gain_n_m_s",
            "angular-rate feedback gain",
            "角速度反馈增益",
            "N*m*s",
            "rate gain",
            "P gain",
            "速率增益",
        ),
        _parameter(
            "reaction_wheels.initial_speed_rpm",
            "initial reaction-wheel speeds",
            "反作用轮初始转速",
            "rpm",
            "wheel speed",
            "轮速",
        ),
        _parameter(
            "propagation.duration_s",
            "attitude simulation duration",
            "姿态仿真时长",
            "s",
            "duration",
            "仿真时间",
        ),
        _parameter(
            "propagation.step_size_s",
            "fixed attitude integration step",
            "固定姿态积分步长",
            "s",
            "time step",
            "积分步长",
        ),
        _parameter(
            "propagation.output_interval_s",
            "state output interval",
            "状态输出间隔",
            "s",
            "output interval",
            "输出间隔",
        ),
    )


def _rocket_family() -> TaskFamilySpec:
    point_mass = "single_stage_point_mass_3dof"
    point_mass_recovery = "single_stage_point_mass_3dof_recovery"
    rigid_body = "single_stage_rigid_body_6dof"
    rigid_body_recovery = "single_stage_rigid_body_6dof_recovery"
    all_tasks = (
        point_mass,
        point_mass_recovery,
        rigid_body,
        rigid_body_recovery,
    )
    return TaskFamilySpec(
        family_id="rocket_flight",
        family_schema="wms.aerospace.family.rocket_flight.v3",
        contract_schema=AEROSPACE_CONTRACT_SCHEMA,
        backend_ids=("rocketpy",),
        contract_type=ScenarioConfig,
        default_variant_id="point_mass_3dof",
        variants=(
            TaskVariantSpec(
                "point_mass_3dof",
                point_mass,
                ("rocket.fidelity.point_mass_3dof", "rocket.recovery.none"),
                assistant=_assistant_variant(
                    "Point-mass 3DOF rocket ascent and ballistic descent without parachutes.",
                    "无降落伞的质点三自由度火箭上升与弹道下降。",
                    aliases=(
                        "3DOF rocket",
                        "point-mass rocket",
                        "三自由度火箭",
                        "质点火箭",
                    ),
                    selection_cues=(
                        "The user explicitly requests 3DOF or point-mass fidelity.",
                        "The user explicitly excludes parachute recovery or requests ballistic descent.",
                    ),
                    exclusion_cues=(
                        "6DOF, attitude, angular rate, aerodynamic moment, fins",
                        "parachute, drogue, main canopy, recovery",
                    ),
                    clarification_topics=(
                        "Ask 3DOF versus 6DOF when fidelity is not stated.",
                        "Ask whether parachute recovery is required when descent mode is not stated.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "point_mass_3dof_recovery",
                point_mass_recovery,
                (
                    "rocket.fidelity.point_mass_3dof",
                    "rocket.recovery.parachute",
                ),
                assistant=_assistant_variant(
                    "Point-mass 3DOF rocket with deterministic drogue and main parachute recovery.",
                    "带确定性减速伞和主伞回收的质点三自由度火箭。",
                    aliases=(
                        "3DOF recovery rocket",
                        "point-mass rocket with parachutes",
                        "三自由度回收火箭",
                        "三自由度双伞火箭",
                    ),
                    selection_cues=(
                        "The user explicitly requests 3DOF or point-mass fidelity.",
                        "The user requests parachute recovery, a drogue, a main parachute, or dual-canopy recovery.",
                    ),
                    exclusion_cues=(
                        "6DOF, attitude, angular rate, aerodynamic moment, fins",
                        "ballistic descent without parachutes",
                    ),
                    clarification_topics=(
                        "Ask 3DOF versus 6DOF when fidelity is not stated.",
                        "Ask whether recovery is required when descent mode is ambiguous.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "rigid_body_6dof",
                rigid_body,
                ("rocket.fidelity.rigid_body_6dof", "rocket.recovery.none"),
                assistant=_assistant_variant(
                    "Rigid-body 6DOF rocket with attitude and aerodynamic moments, without parachutes.",
                    "包含姿态和气动力矩、无降落伞的刚体六自由度火箭。",
                    aliases=(
                        "6DOF rocket",
                        "rigid-body rocket",
                        "六自由度火箭",
                        "刚体火箭",
                    ),
                    selection_cues=(
                        "The user explicitly requests 6DOF, attitude, angular rates, moments, or fin dynamics.",
                        "The user explicitly excludes parachute recovery.",
                    ),
                    exclusion_cues=(
                        "3DOF, point mass",
                        "parachute, drogue, main canopy, recovery",
                    ),
                    clarification_topics=(
                        "Ask 3DOF versus 6DOF when fidelity is not stated.",
                        "Ask whether parachute recovery is required when descent mode is not stated.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "rigid_body_6dof_recovery",
                rigid_body_recovery,
                (
                    "rocket.fidelity.rigid_body_6dof",
                    "rocket.recovery.parachute",
                ),
                assistant=_assistant_variant(
                    "Rigid-body 6DOF rocket ascent followed by drogue and main parachute recovery.",
                    "刚体六自由度火箭上升，随后使用减速伞和主伞回收。",
                    aliases=(
                        "6DOF recovery rocket",
                        "rigid-body rocket with parachutes",
                        "六自由度回收火箭",
                        "六自由度双伞火箭",
                    ),
                    selection_cues=(
                        "The user explicitly requests 6DOF, attitude, angular rates, moments, or fin dynamics.",
                        "The user requests parachute recovery, a drogue, a main parachute, or dual-canopy recovery.",
                    ),
                    exclusion_cues=(
                        "3DOF, point mass",
                        "ballistic descent without parachutes",
                    ),
                    clarification_topics=(
                        "Ask 3DOF versus 6DOF when fidelity is not stated.",
                        "Ask whether recovery is required when descent mode is ambiguous.",
                    ),
                ),
            ),
        ),
        components=(
            _component(
                "rocket.fidelity.point_mass_3dof",
                "fidelity",
                "rocketpy",
                (point_mass, point_mass_recovery),
            ),
            _component(
                "rocket.fidelity.rigid_body_6dof",
                "fidelity",
                "rocketpy",
                (rigid_body, rigid_body_recovery),
            ),
            _component(
                "rocket.recovery.none",
                "recovery",
                "rocketpy",
                (point_mass, rigid_body),
            ),
            _component(
                "rocket.recovery.parachute",
                "recovery",
                "rocketpy",
                (point_mass_recovery, rigid_body_recovery),
            ),
            _component(
                "rocket.environment.standard_atmosphere",
                "environment",
                "rocketpy",
                all_tasks,
            ),
            _component(
                "rocket.propulsion.thrust_curve",
                "propulsion",
                "rocketpy",
                all_tasks,
            ),
        ),
        assistant_parameters=_rocket_assistant_parameters(),
    )


def _launch_family() -> TaskFamilySpec:
    task_kind = "two_stage_launch_to_orbit"
    return TaskFamilySpec(
        family_id="launch_to_orbit",
        family_schema="wms.aerospace.family.launch_to_orbit.v1",
        contract_schema=LAUNCH_CONTRACT_SCHEMA,
        backend_ids=("tudatpy",),
        contract_type=LaunchToOrbitConfig,
        default_variant_id="two_stage_220km_reference",
        variants=(
            TaskVariantSpec(
                "two_stage_220km_reference",
                task_kind,
                (
                    "launch.environment.rotating_exponential_earth",
                    "launch.gravity.spherical_harmonic_j2",
                    "launch.guidance.pitch_program",
                    "launch.propagator.coupled_translation_mass",
                    "launch.staging.two_stage",
                ),
                assistant=_assistant_variant(
                    "Two-stage point-mass launch from Earth to a verified 220 km-class near-circular orbit.",
                    "从地球发射并进入经验证的 220 km 级近圆轨道的两级质点运载火箭。",
                    aliases=(
                        "two-stage orbital launch",
                        "launch to orbit",
                        "两级运载火箭入轨",
                        "发射入轨",
                    ),
                    selection_cues=(
                        "The user requests a multistage launch vehicle that reaches orbit.",
                        "The request includes staging, payload delivery, ascent guidance, or orbital insertion.",
                    ),
                    exclusion_cues=(
                        "sounding rocket or suborbital ballistic flight",
                        "orbit propagation from an already established initial orbit",
                    ),
                    clarification_topics=(
                        "Ask for target altitude and payload when non-default values are required.",
                        "Ask whether a prescribed pitch program is acceptable when closed-loop guidance is requested.",
                    ),
                ),
            ),
        ),
        components=(
            _component(
                "launch.environment.rotating_exponential_earth",
                "environment",
                "tudatpy",
                (task_kind,),
            ),
            _component(
                "launch.gravity.spherical_harmonic_j2",
                "gravity",
                "tudatpy",
                (task_kind,),
            ),
            _component(
                "launch.guidance.pitch_program",
                "guidance",
                "tudatpy",
                (task_kind,),
            ),
            _component(
                "launch.propagator.coupled_translation_mass",
                "propagator",
                "tudatpy",
                (task_kind,),
            ),
            _component(
                "launch.staging.two_stage",
                "staging",
                "tudatpy",
                (task_kind,),
            ),
        ),
        assistant_parameters=_launch_assistant_parameters(),
    )


def _orbit_family() -> TaskFamilySpec:
    two_body = "earth_orbit_two_body"
    j2 = "earth_orbit_j2"
    return TaskFamilySpec(
        family_id="orbit_propagation",
        family_schema="wms.aerospace.family.orbit_propagation.v2",
        contract_schema=ORBIT_CONTRACT_SCHEMA,
        backend_ids=("tudatpy",),
        contract_type=OrbitPropagationConfig,
        default_variant_id="earth_two_body",
        variants=(
            TaskVariantSpec(
                "earth_two_body",
                two_body,
                ("orbit.gravity.point_mass", "orbit.propagator.rk4_fixed"),
                (VariantSelector("aerodynamics.enabled", False),),
                assistant=_assistant_variant(
                    "Earth-centered two-body propagation with point-mass gravity only.",
                    "仅使用地球点质量引力的二体轨道传播。",
                    aliases=(
                        "two-body orbit",
                        "Keplerian propagation",
                        "二体轨道",
                        "开普勒轨道传播",
                    ),
                    selection_cues=(
                        "The user explicitly requests two-body, Keplerian, or point-mass gravity.",
                        "The user excludes J2, atmospheric drag, and other perturbations.",
                    ),
                    exclusion_cues=(
                        "J2, oblateness, spherical harmonics",
                        "atmospheric drag, aerodynamic force",
                    ),
                    clarification_topics=(
                        "Ask which force model is required when the user only says orbit propagation.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "earth_j2",
                j2,
                (
                    "orbit.gravity.spherical_harmonic_j2",
                    "orbit.propagator.rk4_fixed",
                ),
                (VariantSelector("aerodynamics.enabled", False),),
                assistant=_assistant_variant(
                    "Earth orbit propagation with J2 oblateness and no atmospheric drag.",
                    "包含地球 J2 扁率项但不含大气阻力的轨道传播。",
                    aliases=(
                        "J2 orbit",
                        "oblateness propagation",
                        "J2 轨道",
                        "地球扁率摄动",
                    ),
                    selection_cues=(
                        "The user explicitly requests J2, Earth oblateness, or second zonal harmonic gravity.",
                        "The user excludes atmospheric or aerodynamic drag.",
                    ),
                    exclusion_cues=(
                        "pure two-body or unperturbed Keplerian propagation",
                        "atmospheric drag, aerodynamic force",
                    ),
                    clarification_topics=(
                        "Ask whether drag is required when J2 is stated for a low Earth orbit.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "earth_j2_aerodynamic_drag",
                j2,
                (
                    "orbit.gravity.spherical_harmonic_j2",
                    "orbit.environment.exponential_atmosphere",
                    "orbit.force.aerodynamic_drag",
                    "orbit.propagator.rk4_fixed",
                ),
                (VariantSelector("aerodynamics.enabled", True),),
                assistant=_assistant_variant(
                    "Low Earth orbit propagation with Earth J2 and exponential-atmosphere drag.",
                    "包含地球 J2 和指数大气阻力的低轨传播。",
                    aliases=(
                        "J2 drag orbit",
                        "LEO with atmospheric drag",
                        "J2 加阻力轨道",
                        "低轨大气阻力传播",
                    ),
                    selection_cues=(
                        "The user explicitly requests both J2 or oblateness and atmospheric drag.",
                        "The request discusses drag area, drag coefficient, or low-orbit decay.",
                    ),
                    exclusion_cues=(
                        "pure two-body propagation",
                        "J2 explicitly without drag",
                    ),
                    clarification_topics=(
                        "Ask for the force model if drag or J2 is not explicit.",
                        "Ask for aerodynamic properties only when non-default values are required.",
                    ),
                ),
            ),
        ),
        components=(
            _component(
                "orbit.gravity.point_mass",
                "gravity",
                "tudatpy",
                (two_body,),
            ),
            _component(
                "orbit.gravity.spherical_harmonic_j2",
                "gravity",
                "tudatpy",
                (j2,),
            ),
            _component(
                "orbit.environment.exponential_atmosphere",
                "environment",
                "tudatpy",
                (j2,),
            ),
            _component(
                "orbit.force.aerodynamic_drag",
                "force",
                "tudatpy",
                (j2,),
                {
                    "type": "object",
                    "required": [
                        "reference_area_m2",
                        "drag_coefficient",
                    ],
                    "properties": {
                        "reference_area_m2": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                        "drag_coefficient": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            _component(
                "orbit.propagator.rk4_fixed",
                "propagator",
                "tudatpy",
                (two_body, j2),
            ),
        ),
        assistant_parameters=_orbit_assistant_parameters(),
    )


def _aircraft_family() -> TaskFamilySpec:
    task_kind = "fixed_wing_trimmed_6dof"
    return TaskFamilySpec(
        family_id="aircraft_flight",
        family_schema="wms.aerospace.family.aircraft_flight.v3",
        contract_schema=AIRCRAFT_CONTRACT_SCHEMA,
        backend_ids=("jsbsim",),
        contract_type=AircraftFlightConfig,
        default_variant_id="c172p_trimmed_6dof",
        variants=(
            TaskVariantSpec(
                "c172p_trimmed_6dof",
                task_kind,
                (
                    "aircraft.model.c172p",
                    "aircraft.trim.longitudinal",
                    "aircraft.control.trim_relative_open_loop",
                ),
                (VariantSelector("aircraft.model_id", "c172p"),),
                assistant=_assistant_variant(
                    "Trimmed JSBSim Cessna 172P nonlinear 6DOF flight.",
                    "JSBSim Cessna 172P 配平非线性六自由度飞行。",
                    aliases=("C172P", "Cessna 172P", "塞斯纳 172P"),
                    selection_cues=("The user explicitly names C172P or Cessna 172P.",),
                    exclusion_cues=("C172R, C182, C310, J3 Cub",),
                    clarification_topics=(
                        "Ask for the exact aircraft model if the user only says Cessna or light aircraft.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "c172r_trimmed_6dof",
                task_kind,
                (
                    "aircraft.model.c172r",
                    "aircraft.trim.longitudinal",
                    "aircraft.control.trim_relative_open_loop",
                ),
                (VariantSelector("aircraft.model_id", "c172r"),),
                assistant=_assistant_variant(
                    "Trimmed JSBSim Cessna 172R nonlinear 6DOF flight.",
                    "JSBSim Cessna 172R 配平非线性六自由度飞行。",
                    aliases=("C172R", "Cessna 172R", "塞斯纳 172R"),
                    selection_cues=("The user explicitly names C172R or Cessna 172R.",),
                    exclusion_cues=("C172P, C182, C310, J3 Cub",),
                    clarification_topics=(
                        "Ask whether C172P or C172R is intended when the user only says C172.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "c182_trimmed_6dof",
                task_kind,
                (
                    "aircraft.model.c182",
                    "aircraft.trim.longitudinal",
                    "aircraft.control.trim_relative_open_loop",
                ),
                (VariantSelector("aircraft.model_id", "c182"),),
                assistant=_assistant_variant(
                    "Trimmed JSBSim Cessna 182 nonlinear 6DOF flight.",
                    "JSBSim Cessna 182 配平非线性六自由度飞行。",
                    aliases=("C182", "Cessna 182", "塞斯纳 182"),
                    selection_cues=("The user explicitly names C182 or Cessna 182.",),
                    exclusion_cues=("C172P, C172R, C310, J3 Cub",),
                    clarification_topics=(
                        "Ask for the exact aircraft model if the user only says Cessna or light aircraft.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "c310_trimmed_6dof",
                task_kind,
                (
                    "aircraft.model.c310",
                    "aircraft.trim.longitudinal",
                    "aircraft.control.trim_relative_open_loop",
                ),
                (VariantSelector("aircraft.model_id", "c310"),),
                assistant=_assistant_variant(
                    "Trimmed JSBSim Cessna 310 twin-engine nonlinear 6DOF flight.",
                    "JSBSim Cessna 310 双发飞机配平非线性六自由度飞行。",
                    aliases=(
                        "C310",
                        "Cessna 310",
                        "Cessna twin",
                        "塞斯纳 310",
                    ),
                    selection_cues=(
                        "The user explicitly names C310, Cessna 310, or the supported Cessna twin-engine model.",
                    ),
                    exclusion_cues=("C172P, C172R, C182, J3 Cub",),
                    clarification_topics=(
                        "Ask for the exact aircraft model when twin-engine Cessna is not explicit.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "j3cub_trimmed_6dof",
                task_kind,
                (
                    "aircraft.model.j3cub",
                    "aircraft.trim.longitudinal",
                    "aircraft.control.trim_relative_open_loop",
                ),
                (VariantSelector("aircraft.model_id", "J3Cub"),),
                assistant=_assistant_variant(
                    "Trimmed JSBSim Piper J-3 Cub nonlinear 6DOF flight.",
                    "JSBSim Piper J-3 Cub 轻型飞机配平非线性六自由度飞行。",
                    aliases=(
                        "J3 Cub",
                        "J-3 Cub",
                        "Piper Cub",
                        "派珀 J-3",
                    ),
                    selection_cues=(
                        "The user explicitly names J3 Cub, J-3 Cub, or Piper Cub.",
                    ),
                    exclusion_cues=("C172P, C172R, C182, C310",),
                    clarification_topics=(
                        "Ask for the exact aircraft model if the user only says light aircraft.",
                    ),
                ),
            ),
        ),
        components=(
            _component(
                "aircraft.model.c172p",
                "vehicle",
                "jsbsim",
                (task_kind,),
            ),
            _component(
                "aircraft.model.c172r",
                "vehicle",
                "jsbsim",
                (task_kind,),
            ),
            _component(
                "aircraft.model.c182",
                "vehicle",
                "jsbsim",
                (task_kind,),
            ),
            _component(
                "aircraft.model.c310",
                "vehicle",
                "jsbsim",
                (task_kind,),
            ),
            _component(
                "aircraft.model.j3cub",
                "vehicle",
                "jsbsim",
                (task_kind,),
            ),
            _component(
                "aircraft.trim.longitudinal",
                "initialization",
                "jsbsim",
                (task_kind,),
            ),
            _component(
                "aircraft.control.trim_relative_open_loop",
                "control",
                "jsbsim",
                (task_kind,),
            ),
        ),
        assistant_parameters=_aircraft_assistant_parameters(),
    )


def _spacecraft_family() -> TaskFamilySpec:
    pointing_task = "spacecraft_inertial_pointing_gnc"
    rate_damping_task = "spacecraft_rate_damping_gnc"
    all_tasks = (pointing_task, rate_damping_task)
    return TaskFamilySpec(
        family_id="spacecraft_gnc",
        family_schema="wms.aerospace.family.spacecraft_gnc.v3",
        contract_schema=ATTITUDE_CONTRACT_SCHEMA,
        backend_ids=("basilisk",),
        contract_type=SpacecraftAttitudeConfig,
        default_variant_id="inertial_pointing_rw",
        variants=(
            TaskVariantSpec(
                "inertial_pointing_rw",
                pointing_task,
                (
                    "spacecraft.navigation.perfect",
                    "spacecraft.guidance.inertial_fixed_mrp",
                    "spacecraft.control.mrp_feedback_pd",
                    "spacecraft.actuator.reaction_wheels_hr16",
                ),
                (VariantSelector("gnc.enabled", True),),
                assistant=_assistant_variant(
                    "Closed-loop inertial attitude pointing with MRP feedback and reaction wheels.",
                    "使用 MRP 反馈和反作用轮的闭环惯性姿态指向。",
                    aliases=(
                        "inertial pointing",
                        "attitude hold",
                        "reaction-wheel pointing",
                        "惯性指向",
                        "姿态保持",
                    ),
                    selection_cues=(
                        "The user requests active attitude pointing, attitude hold, or tracking an inertial MRP reference.",
                        "The objective includes attitude error regulation, not only rate reduction.",
                    ),
                    exclusion_cues=(
                        "uncontrolled or torque-free attitude",
                        "pure angular-rate damping with no attitude objective",
                    ),
                    clarification_topics=(
                        "Ask whether the goal is attitude pointing or only angular-rate damping.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "uncontrolled_attitude_rw",
                pointing_task,
                (
                    "spacecraft.navigation.perfect",
                    "spacecraft.guidance.inertial_fixed_mrp",
                    "spacecraft.control.none",
                    "spacecraft.actuator.reaction_wheels_hr16",
                ),
                (VariantSelector("gnc.enabled", False),),
                assistant=_assistant_variant(
                    "Uncontrolled rigid-spacecraft attitude motion with no commanded wheel torque.",
                    "不施加反作用轮指令力矩的刚性航天器无控姿态运动。",
                    aliases=(
                        "uncontrolled attitude",
                        "free attitude motion",
                        "无控姿态",
                        "自由姿态运动",
                    ),
                    selection_cues=(
                        "The user explicitly requests uncontrolled, open-loop, or no-control attitude motion.",
                    ),
                    exclusion_cues=(
                        "attitude hold, inertial pointing, active control",
                        "angular-rate damping controller",
                    ),
                    clarification_topics=(
                        "Ask whether active control is required when the request only says attitude simulation.",
                    ),
                ),
            ),
            TaskVariantSpec(
                "reaction_wheel_rate_damping",
                rate_damping_task,
                (
                    "spacecraft.navigation.perfect",
                    "spacecraft.guidance.inertial_fixed_mrp",
                    "spacecraft.control.rate_damping",
                    "spacecraft.actuator.reaction_wheels_hr16",
                ),
                (
                    VariantSelector("gnc.enabled", True),
                    VariantSelector("gnc.controller", "rate_damping"),
                ),
                assistant=_assistant_variant(
                    "Reaction-wheel angular-rate damping without an attitude-hold objective.",
                    "使用反作用轮抑制角速度，但不保持指定姿态。",
                    aliases=(
                        "rate damping",
                        "detumbling",
                        "angular velocity damping",
                        "角速度阻尼",
                        "消旋",
                    ),
                    selection_cues=(
                        "The user requests angular-rate damping, body-rate reduction, or detumbling.",
                        "The request does not require holding or tracking a specific attitude.",
                    ),
                    exclusion_cues=(
                        "inertial attitude hold or attitude tracking",
                        "uncontrolled motion with no commanded torque",
                    ),
                    clarification_topics=(
                        "Ask whether an attitude target must also be maintained.",
                    ),
                ),
            ),
        ),
        components=(
            _component(
                "spacecraft.navigation.perfect",
                "navigation",
                "basilisk",
                all_tasks,
            ),
            _component(
                "spacecraft.guidance.inertial_fixed_mrp",
                "guidance",
                "basilisk",
                all_tasks,
            ),
            _component(
                "spacecraft.control.mrp_feedback_pd",
                "control",
                "basilisk",
                (pointing_task,),
            ),
            _component(
                "spacecraft.control.rate_damping",
                "control",
                "basilisk",
                (rate_damping_task,),
            ),
            _component(
                "spacecraft.control.none",
                "control",
                "basilisk",
                (pointing_task,),
            ),
            _component(
                "spacecraft.actuator.reaction_wheels_hr16",
                "actuator",
                "basilisk",
                all_tasks,
            ),
        ),
        assistant_parameters=_spacecraft_assistant_parameters(),
    )
