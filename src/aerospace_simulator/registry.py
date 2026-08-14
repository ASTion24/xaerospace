from __future__ import annotations

from .protocol import (
    BackendCapabilities,
    SimulationBackend,
    SimulationRequest,
    UnifiedSimulationResult,
)


class BackendRegistryError(RuntimeError):
    """Base class for backend registration and selection failures."""


class BackendRegistrationError(BackendRegistryError):
    """Raised when a backend cannot be registered safely."""


class BackendSelectionError(BackendRegistryError):
    """Raised when an explicit or automatic backend choice is ambiguous."""


class UnsupportedTaskError(BackendRegistryError):
    """Raised when no backend implements the requested contract."""


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, SimulationBackend] = {}

    def register(self, backend: SimulationBackend) -> None:
        backend_id = backend.capabilities.backend_id
        if backend_id in self._backends:
            raise BackendRegistrationError(
                f"backend id is already registered: {backend_id}"
            )
        self._backends[backend_id] = backend

    def capabilities(self) -> tuple[BackendCapabilities, ...]:
        return tuple(
            backend.capabilities for _, backend in sorted(self._backends.items())
        )

    def select(self, request: SimulationRequest) -> SimulationBackend:
        preference = request.backend_preference
        if preference not in {None, "auto"}:
            try:
                backend = self._backends[preference]
            except KeyError as exc:
                raise BackendSelectionError(
                    f"requested backend is not registered: {preference}"
                ) from exc
            if not backend.capabilities.supports(request):
                raise UnsupportedTaskError(
                    f"backend {preference!r} does not support task "
                    f"{request.task_kind!r} with contract "
                    f"{request.contract_schema!r}"
                )
            return backend

        candidates = [
            backend
            for backend in self._backends.values()
            if backend.capabilities.supports(request)
        ]
        if not candidates:
            raise UnsupportedTaskError(
                f"no backend supports task {request.task_kind!r} with "
                f"contract {request.contract_schema!r}"
            )
        if len(candidates) > 1:
            backend_ids = ", ".join(
                sorted(backend.capabilities.backend_id for backend in candidates)
            )
            raise BackendSelectionError(
                "multiple backends support the request; set backend_preference "
                f"explicitly: {backend_ids}"
            )
        return candidates[0]

    def run(self, request: SimulationRequest) -> UnifiedSimulationResult:
        backend = self.select(request)
        result = backend.run(request)
        if result.backend.backend_id != backend.capabilities.backend_id:
            raise BackendRegistryError(
                "backend returned provenance for a different backend id"
            )
        return result
