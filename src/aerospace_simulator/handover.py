from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace

import numpy as np

from .config import ScenarioConfig, ScenarioValidationError
from .orbit_config import OrbitPropagationConfig
from .protocol import SimulationRequest, UnifiedSimulationResult

ROCKET_TO_ORBIT_HANDOVER = "rocketpy_to_tudatpy"
SUPPORTED_SOURCE_EVENTS = ("burnout", "apogee")
WGS84_SEMI_MAJOR_AXIS_M = 6_378_137.0
WGS84_FLATTENING = 1.0 / 298.257_223_563
J2000_GREENWICH_ANGLE_RAD = math.radians(280.460_618_37)
MAX_LOCAL_TANGENT_DISTANCE_M = 100_000.0


class HandoverValidationError(ValueError):
    """Raised when two tasks cannot be connected without changing their meaning."""


@dataclass(frozen=True)
class HandoverSpec:
    handover_type: str
    source_task_id: str
    source_event: str
    launch_epoch_s_since_j2000: float

    def __post_init__(self) -> None:
        if self.handover_type != ROCKET_TO_ORBIT_HANDOVER:
            raise HandoverValidationError(
                f"unsupported handover type: {self.handover_type!r}"
            )
        if not self.source_task_id:
            raise HandoverValidationError("handover source_task_id must not be empty")
        if self.source_event not in SUPPORTED_SOURCE_EVENTS:
            choices = ", ".join(SUPPORTED_SOURCE_EVENTS)
            raise HandoverValidationError(
                f"handover source_event must be one of: {choices}"
            )
        if not math.isfinite(self.launch_epoch_s_since_j2000):
            raise HandoverValidationError("handover launch epoch must be finite")

    @classmethod
    def from_mapping(cls, value: object) -> HandoverSpec:
        if not isinstance(value, Mapping):
            raise HandoverValidationError("handover must be an object")
        required = {
            "type",
            "source_task_id",
            "source_event",
            "launch_epoch_s_since_j2000",
        }
        missing = required - set(value)
        unknown = set(value) - required
        if missing:
            raise HandoverValidationError(
                "handover is missing fields: " + ", ".join(sorted(missing))
            )
        if unknown:
            raise HandoverValidationError(
                "handover contains unknown fields: " + ", ".join(sorted(unknown))
            )
        epoch = value["launch_epoch_s_since_j2000"]
        if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
            raise HandoverValidationError(
                "handover launch_epoch_s_since_j2000 must be a number"
            )
        handover_type = value["type"]
        source_task_id = value["source_task_id"]
        source_event = value["source_event"]
        if not all(
            isinstance(item, str)
            for item in (handover_type, source_task_id, source_event)
        ):
            raise HandoverValidationError(
                "handover type, source task, and event must be strings"
            )
        return cls(
            handover_type=handover_type,
            source_task_id=source_task_id,
            source_event=source_event,
            launch_epoch_s_since_j2000=float(epoch),
        )

    def document(self) -> dict[str, object]:
        return {
            "type": self.handover_type,
            "source_task_id": self.source_task_id,
            "source_event": self.source_event,
            "launch_epoch_s_since_j2000": (self.launch_epoch_s_since_j2000),
        }


def compile_handover(
    spec: HandoverSpec,
    *,
    source_result: UnifiedSimulationResult,
    target_request: SimulationRequest,
) -> tuple[SimulationRequest, dict[str, object]]:
    source_contract = source_result.request.contract
    target_contract = target_request.contract
    if source_result.backend.backend_id != "rocketpy" or not isinstance(
        source_contract,
        ScenarioConfig,
    ):
        raise HandoverValidationError(
            "rocket-to-orbit handover requires a RocketPy source result"
        )
    if target_request.backend_preference != "tudatpy" or not isinstance(
        target_contract,
        OrbitPropagationConfig,
    ):
        raise HandoverValidationError(
            "rocket-to-orbit handover requires a TudatPy target contract"
        )

    source_time_s = source_result.event(spec.source_event).time_s
    east_m = _sample_channel(source_result, "x_east", source_time_s, "local_enu")
    north_m = _sample_channel(
        source_result,
        "y_north",
        source_time_s,
        "local_enu",
    )
    up_m = _sample_channel(
        source_result,
        "altitude_agl",
        source_time_s,
        "above_launch_site",
    )
    local_distance = float(np.linalg.norm([east_m, north_m, up_m]))
    if local_distance > MAX_LOCAL_TANGENT_DISTANCE_M:
        raise HandoverValidationError(
            "RocketPy local tangent state exceeds the verified 100 km handover domain"
        )
    velocity_enu_m_s = np.array(
        [
            _sample_channel(source_result, "vx", source_time_s, "local_enu"),
            _sample_channel(source_result, "vy", source_time_s, "local_enu"),
            _sample_channel(source_result, "vz", source_time_s, "local_enu"),
        ],
        dtype=float,
    )
    position_enu_m = np.array([east_m, north_m, up_m], dtype=float)
    target_epoch_s = spec.launch_epoch_s_since_j2000 + source_time_s
    position_j2000_m, velocity_j2000_m_s = local_enu_to_j2000(
        latitude_deg=source_contract.environment.latitude_deg,
        longitude_deg=source_contract.environment.longitude_deg,
        elevation_m=source_contract.environment.elevation_m,
        position_enu_m=position_enu_m,
        velocity_enu_m_s=velocity_enu_m_s,
        epoch_s_since_j2000=target_epoch_s,
        earth_rotation_rate_rad_s=(target_contract.central_body.rotation_rate_rad_s),
    )
    elements = cartesian_to_keplerian(
        position_j2000_m,
        velocity_j2000_m_s,
        gravitational_parameter_m3_s2=(
            target_contract.central_body.gravitational_parameter_m3_s2
        ),
    )
    raw_contract = asdict(target_contract)
    raw_contract["initial_state"] = elements
    propagation = raw_contract["propagation"]
    if not isinstance(propagation, dict):
        raise HandoverValidationError(
            "target propagation contract could not be updated"
        )
    propagation["start_epoch_s_since_j2000"] = target_epoch_s
    try:
        compiled_contract = OrbitPropagationConfig.from_mapping(raw_contract)
    except ScenarioValidationError as exc:
        raise HandoverValidationError(
            f"derived orbit is not valid for TudatPy: {exc}"
        ) from exc
    compiled_request = replace(target_request, contract=compiled_contract)
    report = {
        **spec.document(),
        "status": "applied",
        "source_backend": source_result.backend.backend_id,
        "target_backend": "tudatpy",
        "source_time_s": source_time_s,
        "target_epoch_s_since_j2000": target_epoch_s,
        "position_j2000_m": position_j2000_m.tolist(),
        "velocity_j2000_m_s": velocity_j2000_m_s.tolist(),
        "derived_initial_state": elements,
        "transform_model": ("RocketPy local ENU -> WGS84 ECEF -> rotating-Earth J2000"),
    }
    return compiled_request, report


def local_enu_to_j2000(
    *,
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float,
    position_enu_m: np.ndarray,
    velocity_enu_m_s: np.ndarray,
    epoch_s_since_j2000: float,
    earth_rotation_rate_rad_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    position_enu = _vector3(position_enu_m, "position_enu_m")
    velocity_enu = _vector3(velocity_enu_m_s, "velocity_enu_m_s")
    values = (
        latitude_deg,
        longitude_deg,
        elevation_m,
        epoch_s_since_j2000,
        earth_rotation_rate_rad_s,
    )
    if not all(math.isfinite(value) for value in values):
        raise HandoverValidationError("handover transform inputs must be finite")
    latitude = math.radians(latitude_deg)
    longitude = math.radians(longitude_deg)
    launch_ecef = _geodetic_to_ecef(latitude, longitude, elevation_m)
    enu_to_ecef = np.array(
        [
            [
                -math.sin(longitude),
                -math.sin(latitude) * math.cos(longitude),
                math.cos(latitude) * math.cos(longitude),
            ],
            [
                math.cos(longitude),
                -math.sin(latitude) * math.sin(longitude),
                math.cos(latitude) * math.sin(longitude),
            ],
            [0.0, math.cos(latitude), math.sin(latitude)],
        ],
        dtype=float,
    )
    position_ecef = launch_ecef + enu_to_ecef @ position_enu
    velocity_ecef = enu_to_ecef @ velocity_enu
    earth_rate = np.array([0.0, 0.0, earth_rotation_rate_rad_s])
    inertial_velocity_ecef_axes = velocity_ecef + np.cross(
        earth_rate,
        position_ecef,
    )
    angle = J2000_GREENWICH_ANGLE_RAD + earth_rotation_rate_rad_s * epoch_s_since_j2000
    rotation = np.array(
        [
            [math.cos(angle), -math.sin(angle), 0.0],
            [math.sin(angle), math.cos(angle), 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=float,
    )
    return (
        rotation @ position_ecef,
        rotation @ inertial_velocity_ecef_axes,
    )


def cartesian_to_keplerian(
    position_m: np.ndarray,
    velocity_m_s: np.ndarray,
    *,
    gravitational_parameter_m3_s2: float,
) -> dict[str, float]:
    position = _vector3(position_m, "position_m")
    velocity = _vector3(velocity_m_s, "velocity_m_s")
    mu = float(gravitational_parameter_m3_s2)
    if not math.isfinite(mu) or mu <= 0:
        raise HandoverValidationError(
            "gravitational parameter must be finite and positive"
        )
    radius = float(np.linalg.norm(position))
    speed_squared = float(np.dot(velocity, velocity))
    angular_momentum = np.cross(position, velocity)
    angular_momentum_norm = float(np.linalg.norm(angular_momentum))
    if radius <= 0 or angular_momentum_norm <= 0:
        raise HandoverValidationError("handover state has degenerate geometry")
    specific_energy = speed_squared / 2.0 - mu / radius
    if specific_energy >= 0:
        raise HandoverValidationError("handover state is not a bound elliptic orbit")
    semi_major_axis = -mu / (2.0 * specific_energy)
    eccentricity_vector = np.cross(velocity, angular_momentum) / mu - position / radius
    eccentricity = float(np.linalg.norm(eccentricity_vector))
    if eccentricity < 1e-6:
        raise HandoverValidationError(
            "handover orbit is too circular for the current orbit contract"
        )
    if eccentricity >= 1:
        raise HandoverValidationError("handover state is not an elliptic orbit")
    inclination = math.acos(
        float(np.clip(angular_momentum[2] / angular_momentum_norm, -1.0, 1.0))
    )
    node = np.cross(np.array([0.0, 0.0, 1.0]), angular_momentum)
    node_norm = float(np.linalg.norm(node))
    if node_norm <= 1e-12:
        raise HandoverValidationError(
            "handover orbit is equatorial and lacks a defined ascending node"
        )
    raan = math.atan2(node[1], node[0]) % (2.0 * math.pi)
    argument_of_periapsis = math.atan2(
        np.dot(np.cross(node, eccentricity_vector), angular_momentum)
        / (node_norm * eccentricity * angular_momentum_norm),
        np.dot(node, eccentricity_vector) / (node_norm * eccentricity),
    ) % (2.0 * math.pi)
    true_anomaly = math.atan2(
        np.dot(
            np.cross(eccentricity_vector, position),
            angular_momentum,
        )
        / (eccentricity * radius * angular_momentum_norm),
        np.dot(eccentricity_vector, position) / (eccentricity * radius),
    ) % (2.0 * math.pi)
    return {
        "semi_major_axis_m": semi_major_axis,
        "eccentricity": eccentricity,
        "inclination_deg": math.degrees(inclination),
        "argument_of_periapsis_deg": math.degrees(argument_of_periapsis),
        "raan_deg": math.degrees(raan),
        "true_anomaly_deg": math.degrees(true_anomaly),
    }


def _sample_channel(
    result: UnifiedSimulationResult,
    name: str,
    time_s: float,
    expected_frame: str,
) -> float:
    try:
        channel = result.channel(name)
    except KeyError as exc:
        raise HandoverValidationError(
            f"source result is missing required channel {name!r}"
        ) from exc
    if channel.frame != expected_frame:
        raise HandoverValidationError(
            f"source channel {name!r} uses frame {channel.frame!r}, "
            f"expected {expected_frame!r}"
        )
    if not result.time_s[0] <= time_s <= result.time_s[-1]:
        raise HandoverValidationError(
            f"handover event {time_s:g} s lies outside the source time axis"
        )
    return float(np.interp(time_s, result.time_s, channel.values))


def _geodetic_to_ecef(
    latitude_rad: float,
    longitude_rad: float,
    elevation_m: float,
) -> np.ndarray:
    eccentricity_squared = WGS84_FLATTENING * (2.0 - WGS84_FLATTENING)
    prime_vertical_radius = WGS84_SEMI_MAJOR_AXIS_M / math.sqrt(
        1.0 - eccentricity_squared * math.sin(latitude_rad) ** 2
    )
    return np.array(
        [
            (prime_vertical_radius + elevation_m)
            * math.cos(latitude_rad)
            * math.cos(longitude_rad),
            (prime_vertical_radius + elevation_m)
            * math.cos(latitude_rad)
            * math.sin(longitude_rad),
            (prime_vertical_radius * (1.0 - eccentricity_squared) + elevation_m)
            * math.sin(latitude_rad),
        ],
        dtype=float,
    )


def _vector3(value: np.ndarray, path: str) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.all(np.isfinite(result)):
        raise HandoverValidationError(f"{path} must contain three finite values")
    return result
