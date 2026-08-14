from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from typing import Protocol, runtime_checkable

import numpy as np

PROTOCOL_VERSION = 1


class ProtocolValidationError(ValueError):
    """Raised when a request or normalized result violates the public protocol."""


@dataclass(frozen=True)
class SimulationRequest:
    protocol_version: int
    request_id: str
    label: str
    description: str
    task_kind: str
    contract_schema: str
    backend_preference: str | None
    contract: object

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolValidationError(
                f"protocol_version must be {PROTOCOL_VERSION}"
            )
        for name, value in (
            ("request_id", self.request_id),
            ("label", self.label),
            ("task_kind", self.task_kind),
            ("contract_schema", self.contract_schema),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ProtocolValidationError(f"{name} must not be empty")
        if not isinstance(self.description, str):
            raise ProtocolValidationError("description must be a string")
        if self.backend_preference is not None and (
            not isinstance(self.backend_preference, str)
            or not self.backend_preference.strip()
        ):
            raise ProtocolValidationError(
                "backend_preference must be null or a non-empty backend id"
            )

    def document(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "label": self.label,
            "description": self.description,
            "task_kind": self.task_kind,
            "contract_schema": self.contract_schema,
            "backend_preference": self.backend_preference,
            "contract": _json_value(self.contract),
        }


@dataclass(frozen=True)
class BackendCapabilities:
    backend_id: str
    backend_name: str
    backend_version: str
    supported_task_kinds: tuple[str, ...]
    supported_contract_schemas: tuple[str, ...]
    supported_family_ids: tuple[str, ...] = ()
    supported_component_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identity = (self.backend_id, self.backend_name, self.backend_version)
        if any(not isinstance(value, str) or not value for value in identity):
            raise ProtocolValidationError("backend identity fields must not be empty")
        if not self.supported_task_kinds:
            raise ProtocolValidationError("backend must declare supported task kinds")
        if len(set(self.supported_task_kinds)) != len(self.supported_task_kinds):
            raise ProtocolValidationError("backend task kinds must be unique")
        if not self.supported_contract_schemas:
            raise ProtocolValidationError(
                "backend must declare supported contract schemas"
            )
        if len(set(self.supported_contract_schemas)) != len(
            self.supported_contract_schemas
        ):
            raise ProtocolValidationError("backend contract schemas must be unique")
        if len(set(self.supported_family_ids)) != len(self.supported_family_ids):
            raise ProtocolValidationError("backend family ids must be unique")
        if len(set(self.supported_component_ids)) != len(self.supported_component_ids):
            raise ProtocolValidationError("backend component ids must be unique")
        if self.supported_component_ids and not self.supported_family_ids:
            raise ProtocolValidationError(
                "backend components require at least one task family"
            )

    def supports(self, request: SimulationRequest) -> bool:
        return (
            request.task_kind in self.supported_task_kinds
            and request.contract_schema in self.supported_contract_schemas
        )

    def document(self) -> dict[str, object]:
        return _json_value(self)


@runtime_checkable
class SimulationBackend(Protocol):
    @property
    def capabilities(self) -> BackendCapabilities: ...

    def run(self, request: SimulationRequest) -> UnifiedSimulationResult: ...


@dataclass(frozen=True)
class ResultChannel:
    name: str
    quantity: str
    unit: str
    frame: str
    values: np.ndarray

    def __post_init__(self) -> None:
        metadata = (self.name, self.quantity, self.unit, self.frame)
        if any(not isinstance(value, str) or not value for value in metadata):
            raise ProtocolValidationError(
                "channel name, quantity, unit, and frame must not be empty"
            )
        values = np.asarray(self.values, dtype=float)
        if values.ndim != 1 or values.size == 0:
            raise ProtocolValidationError(
                f"channel {self.name!r} must be a non-empty one-dimensional array"
            )
        if not np.all(np.isfinite(values)):
            raise ProtocolValidationError(
                f"channel {self.name!r} contains non-finite values"
            )
        object.__setattr__(self, "values", values)

    def document(self) -> dict[str, object]:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "unit": self.unit,
            "frame": self.frame,
            "values": self.values.tolist(),
        }


@dataclass(frozen=True)
class SimulationEvent:
    name: str
    time_s: float
    attributes: Mapping[str, float | str | bool]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ProtocolValidationError("event name must not be empty")
        if not np.isfinite(self.time_s):
            raise ProtocolValidationError("event time must be finite")
        for key, value in self.attributes.items():
            if not isinstance(key, str) or not key:
                raise ProtocolValidationError(
                    "event attribute names must be non-empty strings"
                )
            if isinstance(value, (int, float, np.number)) and not isinstance(
                value, bool
            ):
                if not np.isfinite(value):
                    raise ProtocolValidationError(
                        f"event attribute {key!r} must be finite"
                    )
            elif not isinstance(value, (str, bool)):
                raise ProtocolValidationError(
                    f"event attribute {key!r} has an unsupported value type"
                )

    def document(self) -> dict[str, object]:
        return {
            "name": self.name,
            "time_s": self.time_s,
            "attributes": _json_value(dict(self.attributes)),
        }


@dataclass(frozen=True)
class ResultMetric:
    name: str
    value: float
    unit: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value for value in (self.name, self.unit)
        ):
            raise ProtocolValidationError("metric name and unit must not be empty")
        if not np.isfinite(self.value):
            raise ProtocolValidationError(f"metric {self.name!r} must be finite")

    def document(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Diagnostic:
    level: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.level not in {"info", "warning", "error"}:
            raise ProtocolValidationError(
                "diagnostic level must be info, warning, or error"
            )
        if not self.code or not self.message:
            raise ProtocolValidationError(
                "diagnostic code and message must not be empty"
            )


@dataclass(frozen=True)
class UnifiedSimulationResult:
    protocol_version: int
    request: SimulationRequest
    backend: BackendCapabilities
    time_s: np.ndarray
    channels: tuple[ResultChannel, ...]
    events: tuple[SimulationEvent, ...]
    metrics: tuple[ResultMetric, ...]
    model_manifest: object
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolValidationError(
                f"protocol_version must be {PROTOCOL_VERSION}"
            )
        time_s = np.asarray(self.time_s, dtype=float)
        if time_s.ndim != 1 or time_s.size == 0:
            raise ProtocolValidationError(
                "time_s must be a non-empty one-dimensional array"
            )
        if not np.all(np.isfinite(time_s)):
            raise ProtocolValidationError("time_s contains non-finite values")
        if np.any(np.diff(time_s) <= 0):
            raise ProtocolValidationError("time_s must be strictly increasing")
        object.__setattr__(self, "time_s", time_s)

        channel_names = [channel.name for channel in self.channels]
        if not channel_names:
            raise ProtocolValidationError("result must contain at least one channel")
        if len(set(channel_names)) != len(channel_names):
            raise ProtocolValidationError("result channel names must be unique")
        if any(len(channel.values) != len(time_s) for channel in self.channels):
            raise ProtocolValidationError(
                "all result channels must match the time axis length"
            )
        if any(
            event.time_s < time_s[0] or event.time_s > time_s[-1]
            for event in self.events
        ):
            raise ProtocolValidationError(
                "result event times must lie within the shared time axis"
            )

        metric_names = [metric.name for metric in self.metrics]
        if len(set(metric_names)) != len(metric_names):
            raise ProtocolValidationError("result metric names must be unique")
        if not self.backend.supports(self.request):
            raise ProtocolValidationError(
                "backend provenance does not support the result request"
            )
        _json_value(self.model_manifest)

    def channel(self, name: str) -> ResultChannel:
        for channel in self.channels:
            if channel.name == name:
                return channel
        raise KeyError(f"result channel not found: {name}")

    def metric(self, name: str) -> ResultMetric:
        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise KeyError(f"result metric not found: {name}")

    def event(self, name: str) -> SimulationEvent:
        for event in self.events:
            if event.name == name:
                return event
        raise KeyError(f"result event not found: {name}")

    def document(self, *, include_samples: bool = True) -> dict[str, object]:
        channels: list[dict[str, object]] = []
        for channel in self.channels:
            document = channel.document()
            if not include_samples:
                document.pop("values")
                document["sample_count"] = len(channel.values)
            channels.append(document)
        time_document: dict[str, object] = {
            "name": "time",
            "unit": "s",
            "sample_count": len(self.time_s),
        }
        if include_samples:
            time_document["values"] = self.time_s.tolist()
        return {
            "protocol_version": self.protocol_version,
            "request": self.request.document(),
            "backend": self.backend.document(),
            "time": time_document,
            "channels": channels,
            "events": [event.document() for event in self.events],
            "metrics": [metric.document() for metric in self.metrics],
            "model_manifest": _json_value(self.model_manifest),
            "diagnostics": [_json_value(diagnostic) for diagnostic in self.diagnostics],
        }


def _json_value(value: object) -> object:
    protocol_document = getattr(value, "protocol_document", None)
    if callable(protocol_document):
        return _json_value(protocol_document())
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ProtocolValidationError(
        f"value of type {type(value).__name__} is not protocol-serializable"
    )
