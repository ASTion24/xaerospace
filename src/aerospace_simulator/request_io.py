from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .aircraft_config import (
    AIRCRAFT_CONTRACT_SCHEMA,
    AIRCRAFT_TASK_KINDS,
    AircraftFlightConfig,
)
from .attitude_config import (
    ATTITUDE_CONTRACT_SCHEMA,
    ATTITUDE_TASK_KINDS,
    SpacecraftAttitudeConfig,
)
from .config import ScenarioConfig, ScenarioValidationError
from .launch_config import (
    LAUNCH_CONTRACT_SCHEMA,
    LAUNCH_TASK_KINDS,
    LaunchToOrbitConfig,
)
from .orbit_config import (
    ORBIT_CONTRACT_SCHEMA,
    ORBIT_TASK_KINDS,
    OrbitPropagationConfig,
)
from .protocol import PROTOCOL_VERSION, ProtocolValidationError, SimulationRequest

AEROSPACE_CONTRACT_SCHEMA = "xaerospace.scenario.v1"


def request_from_scenario(
    config: ScenarioConfig,
    *,
    request_id: str | None = None,
) -> SimulationRequest:
    return SimulationRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id or config.name,
        label=config.name,
        description=config.description,
        task_kind=config.dynamics,
        contract_schema=AEROSPACE_CONTRACT_SCHEMA,
        backend_preference=config.backend,
        contract=config,
    )


def request_from_orbit_scenario(
    config: OrbitPropagationConfig,
    *,
    request_id: str | None = None,
) -> SimulationRequest:
    return SimulationRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id or config.name,
        label=config.name,
        description=config.description,
        task_kind=config.dynamics,
        contract_schema=ORBIT_CONTRACT_SCHEMA,
        backend_preference=config.backend,
        contract=config,
    )


def request_from_launch_scenario(
    config: LaunchToOrbitConfig,
    *,
    request_id: str | None = None,
) -> SimulationRequest:
    return SimulationRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id or config.name,
        label=config.name,
        description=config.description,
        task_kind=config.dynamics,
        contract_schema=LAUNCH_CONTRACT_SCHEMA,
        backend_preference=config.backend,
        contract=config,
    )


def request_from_aircraft_scenario(
    config: AircraftFlightConfig,
    *,
    request_id: str | None = None,
) -> SimulationRequest:
    return SimulationRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id or config.name,
        label=config.name,
        description=config.description,
        task_kind=config.dynamics,
        contract_schema=AIRCRAFT_CONTRACT_SCHEMA,
        backend_preference=config.backend,
        contract=config,
    )


def request_from_attitude_scenario(
    config: SpacecraftAttitudeConfig,
    *,
    request_id: str | None = None,
) -> SimulationRequest:
    return SimulationRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id or config.name,
        label=config.name,
        description=config.description,
        task_kind=config.dynamics,
        contract_schema=ATTITUDE_CONTRACT_SCHEMA,
        backend_preference=config.backend,
        contract=config,
    )


def load_request(path: str | Path) -> SimulationRequest:
    request_path = Path(path)
    try:
        raw = json.loads(request_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioValidationError(
            f"request file not found: {request_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(f"request is not valid JSON: {exc.msg}") from exc
    request = request_from_document(raw)
    return request


def request_from_document(raw: object) -> SimulationRequest:
    if not isinstance(raw, Mapping):
        raise ScenarioValidationError("request root must be an object")

    if "protocol_version" not in raw:
        if raw.get("dynamics") in ATTITUDE_TASK_KINDS:
            request = request_from_attitude_scenario(
                SpacecraftAttitudeConfig.from_mapping(raw)
            )
        elif raw.get("dynamics") in AIRCRAFT_TASK_KINDS:
            request = request_from_aircraft_scenario(
                AircraftFlightConfig.from_mapping(raw)
            )
        elif raw.get("dynamics") in ORBIT_TASK_KINDS:
            request = request_from_orbit_scenario(
                OrbitPropagationConfig.from_mapping(raw)
            )
        elif raw.get("dynamics") in LAUNCH_TASK_KINDS:
            request = request_from_launch_scenario(
                LaunchToOrbitConfig.from_mapping(raw)
            )
        else:
            request = request_from_scenario(ScenarioConfig.from_mapping(raw))
    else:
        request = _request_from_envelope(raw)
    return request


def _request_from_envelope(raw: Mapping[str, Any]) -> SimulationRequest:
    required = {
        "protocol_version",
        "request_id",
        "label",
        "description",
        "task_kind",
        "contract_schema",
        "backend_preference",
        "contract",
    }
    missing = required - set(raw)
    unknown = set(raw) - required
    if missing:
        raise ProtocolValidationError(
            f"request is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ProtocolValidationError(
            f"request contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if raw["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolValidationError(f"protocol_version must be {PROTOCOL_VERSION}")
    contract_schema = raw["contract_schema"]
    if contract_schema not in {
        AEROSPACE_CONTRACT_SCHEMA,
        AIRCRAFT_CONTRACT_SCHEMA,
        ATTITUDE_CONTRACT_SCHEMA,
        LAUNCH_CONTRACT_SCHEMA,
        ORBIT_CONTRACT_SCHEMA,
    }:
        raise ProtocolValidationError(
            f"unsupported contract_schema: {contract_schema!r}"
        )
    contract = raw["contract"]
    if not isinstance(contract, Mapping):
        raise ProtocolValidationError("request contract must be an object")

    backend_preference = raw["backend_preference"]
    scenario_raw: dict[str, Any] = {
        **contract,
        "name": raw["label"],
        "description": raw["description"],
        "backend": backend_preference or "auto",
        "dynamics": raw["task_kind"],
    }
    typed_contract: object
    if contract_schema == ATTITUDE_CONTRACT_SCHEMA:
        typed_contract = SpacecraftAttitudeConfig.from_mapping(scenario_raw)
    elif contract_schema == AIRCRAFT_CONTRACT_SCHEMA:
        typed_contract = AircraftFlightConfig.from_mapping(scenario_raw)
    elif contract_schema == ORBIT_CONTRACT_SCHEMA:
        typed_contract = OrbitPropagationConfig.from_mapping(scenario_raw)
    elif contract_schema == LAUNCH_CONTRACT_SCHEMA:
        typed_contract = LaunchToOrbitConfig.from_mapping(scenario_raw)
    else:
        typed_contract = ScenarioConfig.from_mapping(scenario_raw)
    request = SimulationRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=_required_string(raw["request_id"], "request_id"),
        label=_required_string(raw["label"], "label"),
        description=_string(raw["description"], "description"),
        task_kind=_required_string(raw["task_kind"], "task_kind"),
        contract_schema=_required_string(
            raw["contract_schema"],
            "contract_schema",
        ),
        backend_preference=(
            _required_string(backend_preference, "backend_preference")
            if backend_preference is not None
            else None
        ),
        contract=typed_contract,
    )
    return request


def _required_string(value: Any, path: str) -> str:
    text = _string(value, path)
    if not text.strip():
        raise ProtocolValidationError(f"{path} must not be empty")
    return text.strip()


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ProtocolValidationError(f"{path} must be a string")
    return value
