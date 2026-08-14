from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class FlightEvent:
    name: str
    time_s: float
    altitude_agl_m: float
    horizontal_range_m: float


@dataclass(frozen=True)
class FlightSeries:
    time_s: np.ndarray
    x_east_m: np.ndarray
    y_north_m: np.ndarray
    altitude_agl_m: np.ndarray
    horizontal_range_m: np.ndarray
    vx_m_s: np.ndarray
    vy_m_s: np.ndarray
    vz_m_s: np.ndarray
    speed_m_s: np.ndarray
    acceleration_m_s2: np.ndarray
    mach: np.ndarray
    quaternion_e0: np.ndarray
    quaternion_e1: np.ndarray
    quaternion_e2: np.ndarray
    quaternion_e3: np.ndarray
    omega1_rad_s: np.ndarray
    omega2_rad_s: np.ndarray
    omega3_rad_s: np.ndarray
    angular_rate_rad_s: np.ndarray
    attitude_angle_deg: np.ndarray
    angle_of_attack_deg: np.ndarray
    phase: tuple[str, ...]

    def __post_init__(self) -> None:
        expected = len(self.time_s)
        arrays = (
            self.x_east_m,
            self.y_north_m,
            self.altitude_agl_m,
            self.horizontal_range_m,
            self.vx_m_s,
            self.vy_m_s,
            self.vz_m_s,
            self.speed_m_s,
            self.acceleration_m_s2,
            self.mach,
            self.quaternion_e0,
            self.quaternion_e1,
            self.quaternion_e2,
            self.quaternion_e3,
            self.omega1_rad_s,
            self.omega2_rad_s,
            self.omega3_rad_s,
            self.angular_rate_rad_s,
            self.attitude_angle_deg,
            self.angle_of_attack_deg,
        )
        if expected == 0 or any(len(values) != expected for values in arrays):
            raise ValueError("all flight series must have the same non-zero length")
        if len(self.phase) != expected:
            raise ValueError("phase labels must match the sampled flight series")


@dataclass(frozen=True)
class FlightSummary:
    lift_off_mass_kg: float
    rail_departure_time_s: float
    rail_departure_speed_m_s: float
    burnout_time_s: float
    apogee_time_s: float
    apogee_agl_m: float
    max_speed_m_s: float
    max_mach: float
    max_acceleration_m_s2: float
    max_dynamic_pressure_pa: float
    max_angle_of_attack_deg: float
    max_angular_rate_rad_s: float
    flight_time_s: float
    impact_speed_m_s: float
    impact_horizontal_range_m: float


@dataclass(frozen=True)
class StateVariable:
    symbol: str
    name: str
    unit: str
    role: str


@dataclass(frozen=True)
class ModelEquation:
    id: str
    name: str
    phase: str
    expression: str
    latex: str
    explanation: str
    implementation_reference: str


@dataclass(frozen=True)
class ModelParameter:
    symbol: str
    name: str
    value: float | str
    unit: str
    source: str


@dataclass(frozen=True)
class ModelEvent:
    id: str
    condition: str
    direction: str
    action: str
    implementation_reference: str


@dataclass(frozen=True)
class ModelInputSeries:
    id: str
    name: str
    independent_name: str
    independent_unit: str
    dependent_name: str
    dependent_unit: str
    samples: tuple[tuple[float, float], ...]
    source: str


@dataclass(frozen=True)
class ModelManifest:
    schema_version: int
    fidelity: str
    backend_name: str
    backend_version: str
    model_name: str
    dynamics: str
    coordinate_system: tuple[str, ...]
    state_vector: tuple[StateVariable, ...]
    initial_state: tuple[ModelParameter, ...]
    equations: tuple[ModelEquation, ...]
    parameters: tuple[ModelParameter, ...]
    input_series: tuple[ModelInputSeries, ...]
    events: tuple[ModelEvent, ...]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    implementation_references: tuple[str, ...]

    def document(self) -> dict[str, object]:
        return asdict(self)
