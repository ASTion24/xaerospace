from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar
from urllib.parse import urlparse
from uuid import uuid4

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .config import ScenarioValidationError
from .protocol import ProtocolValidationError
from .provider_config import (
    ProviderConfigurationError,
    configured_provider_path,
    configured_provider_profile,
    load_provider_profile,
)
from .registry import BackendRegistry, BackendRegistryError
from .request_io import request_from_document
from .task_families import TaskFamilyRegistry, TaskFamilyRegistryError

ASSISTANT_PROMPT_VERSION = "llm-draft-session-v1"
_LOCKED_ROOT_FIELDS = {"schema_version", "backend", "dynamics"}
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_CONVERSATION_TURNS = 20
_MAX_CONVERSATION_CHARACTERS = 20_000
_LOG = logging.getLogger(__name__)


class AssistantError(RuntimeError):
    """Base class for natural-language contract-drafting failures."""


class AssistantUnavailableError(AssistantError):
    """Raised when no LLM provider is configured."""


class AssistantProviderError(AssistantError):
    """Raised when the configured LLM provider cannot complete a request."""


class AssistantOutputError(AssistantError):
    """Raised when untrusted LLM output violates the assistant protocol."""


class AssistantCompileError(AssistantError):
    """Raised when a proposed patch cannot compile into a valid contract."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentEvidence(_StrictModel):
    concept: str = Field(min_length=1, max_length=160)
    value_json: str | None = Field(default=None, max_length=20_000)
    unit: str | None = Field(default=None, max_length=80)
    source_text: str = Field(min_length=1, max_length=500)


class IntentInference(_StrictModel):
    concept: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)


class IntentIR(_StrictModel):
    task_summary: str = Field(min_length=1, max_length=500)
    domain_hints: list[str] = Field(max_length=8)
    entities: list[str] = Field(max_length=16)
    goals: list[str] = Field(max_length=16)
    explicit_requirements: list[IntentEvidence] = Field(max_length=40)
    inferred_requirements: list[IntentInference] = Field(max_length=24)
    exclusions: list[IntentEvidence] = Field(max_length=16)
    requested_outputs: list[str] = Field(max_length=16)
    ambiguities: list[str] = Field(max_length=12)


class RouteDecision(_StrictModel):
    status: Literal["selected", "needs_clarification", "unsupported"]
    family_id: str | None
    variant_id: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    message: str = Field(min_length=1, max_length=500)
    questions: list[str] = Field(max_length=5)
    decision_basis: list[str] = Field(max_length=12)
    capability_gaps: list[str] = Field(max_length=12)

    @model_validator(mode="after")
    def validate_selection(self) -> RouteDecision:
        selected = self.status == "selected"
        if selected and (not self.family_id or not self.variant_id):
            raise ValueError("selected routes require family_id and variant_id")
        if not selected and (self.family_id is not None or self.variant_id is not None):
            raise ValueError("non-selected routes must not identify a variant")
        if selected and self.questions:
            raise ValueError("selected routes must not contain clarification questions")
        if self.status == "needs_clarification" and not self.questions:
            raise ValueError("clarification routes require at least one question")
        if self.status == "unsupported" and self.questions:
            raise ValueError("unsupported routes must not contain questions")
        if selected and self.capability_gaps:
            raise ValueError("selected routes must not contain capability gaps")
        if self.status != "unsupported" and not self.decision_basis:
            raise ValueError(
                "selected and clarification routes require decision evidence"
            )
        if self.status == "unsupported" and not self.capability_gaps:
            raise ValueError("unsupported routes require at least one capability gap")
        return self


class ParameterPatchProposal(_StrictModel):
    path: str = Field(min_length=1, max_length=160)
    value_json: str = Field(min_length=1, max_length=20_000)
    source_text: str = Field(min_length=1, max_length=500)


class ContractSynthesis(_StrictModel):
    status: Literal["synthesized", "needs_clarification", "unsupported"]
    message: str = Field(min_length=1, max_length=500)
    patches: list[ParameterPatchProposal] = Field(max_length=40)
    assumptions: list[str] = Field(max_length=12)
    questions: list[str] = Field(max_length=5)
    mapped_requirements: list[str] = Field(max_length=40)
    unmapped_requirements: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def validate_synthesis(self) -> ContractSynthesis:
        if self.status == "synthesized":
            if self.questions:
                raise ValueError("synthesized contracts must not contain questions")
            if not self.mapped_requirements:
                raise ValueError(
                    "synthesized contracts require at least one mapped requirement"
                )
            if self.unmapped_requirements:
                raise ValueError(
                    "synthesized contracts must not contain unmapped requirements"
                )
        elif self.status == "needs_clarification":
            if not self.questions:
                raise ValueError(
                    "clarification synthesis requires at least one question"
                )
            if self.patches:
                raise ValueError("clarification synthesis must not contain patches")
            if self.unmapped_requirements:
                raise ValueError(
                    "clarification synthesis must not contain capability gaps"
                )
        else:
            if self.questions:
                raise ValueError("unsupported synthesis must not contain questions")
            if self.patches:
                raise ValueError("unsupported synthesis must not contain patches")
            if not self.unmapped_requirements:
                raise ValueError("unsupported synthesis requires unmapped requirements")
        return self


class AssistantValidation(_StrictModel):
    valid: Literal[True]
    backend: dict[str, object]
    family: dict[str, object]


class AssistantProvenance(_StrictModel):
    draft_id: str
    provider_id: str
    model: str
    prompt_version: str
    latency_ms: float
    stage_latency_ms: dict[str, float]
    llm_call_count: int = Field(ge=1, le=3)


class AssistantDraft(_StrictModel):
    status: Literal["proposal", "needs_clarification", "unsupported"]
    message: str
    family_id: str | None
    variant_id: str | None
    confidence: float
    questions: list[str]
    assumptions: list[str]
    patches: list[ParameterPatchProposal]
    draft_document: dict[str, object] | None
    validation: AssistantValidation | None
    intent_ir: IntentIR
    capability_decision: RouteDecision
    contract_synthesis: ContractSynthesis | None
    provenance: AssistantProvenance


class ProviderHealth(_StrictModel):
    available: bool
    reachable: bool
    model_available: bool | None
    latency_ms: float
    detail: str


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class StructuredLLMProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    async def complete(
        self,
        *,
        schema_name: str,
        response_model: type[ResponseModel],
        messages: Sequence[Mapping[str, str]],
    ) -> ResponseModel: ...

    async def health(self, *, force: bool = False) -> ProviderHealth: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    model: str
    api_key: str
    timeout_s: float
    profile_name: str | None = None
    compatibility_mode: Literal["strict", "llama_cpp"] = "strict"
    max_concurrency: int = 1
    health_timeout_s: float = 10.0
    health_ttl_s: float = 30.0
    max_output_tokens: int = 1_024
    circuit_failure_threshold: int = 3
    circuit_cooldown_s: float = 60.0
    chat_completions_path: str = "/chat/completions"
    models_path: str = "/models"
    additional_headers: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_environment(cls) -> OpenAICompatibleConfig | None:
        base_url = os.environ.get("WMS_ASSISTANT_LLM_BASE_URL", "").strip()
        model = os.environ.get("WMS_ASSISTANT_LLM_MODEL", "").strip()
        if not base_url and not model:
            return None
        if not base_url or not model:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_BASE_URL and WMS_ASSISTANT_LLM_MODEL "
                "must be configured together"
            )
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_BASE_URL must be an absolute HTTP(S) URL"
            )
        try:
            timeout_s = float(
                os.environ.get("WMS_ASSISTANT_LLM_TIMEOUT_S", "45").strip()
            )
        except ValueError as exc:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_TIMEOUT_S must be a number"
            ) from exc
        if not 1.0 <= timeout_s <= 120.0:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_TIMEOUT_S must be in [1, 120]"
            )
        compatibility_mode = os.environ.get(
            "WMS_ASSISTANT_LLM_COMPATIBILITY_MODE",
            "strict",
        ).strip()
        if compatibility_mode not in {"strict", "llama_cpp"}:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_COMPATIBILITY_MODE must be 'strict' or 'llama_cpp'"
            )
        try:
            max_concurrency = int(
                os.environ.get(
                    "WMS_ASSISTANT_LLM_MAX_CONCURRENCY",
                    "1",
                ).strip()
            )
        except ValueError as exc:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_MAX_CONCURRENCY must be an integer"
            ) from exc
        if not 1 <= max_concurrency <= 8:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_MAX_CONCURRENCY must be in [1, 8]"
            )
        try:
            health_timeout_s = float(
                os.environ.get(
                    "WMS_ASSISTANT_LLM_HEALTH_TIMEOUT_S",
                    "10",
                ).strip()
            )
        except ValueError as exc:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_HEALTH_TIMEOUT_S must be a number"
            ) from exc
        if not 1.0 <= health_timeout_s <= 30.0:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_HEALTH_TIMEOUT_S must be in [1, 30]"
            )
        try:
            max_output_tokens = int(
                os.environ.get(
                    "WMS_ASSISTANT_LLM_MAX_OUTPUT_TOKENS",
                    "1024",
                ).strip()
            )
        except ValueError as exc:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_MAX_OUTPUT_TOKENS must be an integer"
            ) from exc
        if not 128 <= max_output_tokens <= 4_096:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_MAX_OUTPUT_TOKENS must be in [128, 4096]"
            )
        try:
            circuit_failure_threshold = int(
                os.environ.get(
                    "WMS_ASSISTANT_LLM_CIRCUIT_FAILURE_THRESHOLD",
                    "3",
                ).strip()
            )
            circuit_cooldown_s = float(
                os.environ.get(
                    "WMS_ASSISTANT_LLM_CIRCUIT_COOLDOWN_S",
                    "60",
                ).strip()
            )
        except ValueError as exc:
            raise AssistantUnavailableError(
                "assistant circuit-breaker settings must be numeric"
            ) from exc
        if not 1 <= circuit_failure_threshold <= 10:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_CIRCUIT_FAILURE_THRESHOLD must be in [1, 10]"
            )
        if not 1.0 <= circuit_cooldown_s <= 600.0:
            raise AssistantUnavailableError(
                "WMS_ASSISTANT_LLM_CIRCUIT_COOLDOWN_S must be in [1, 600]"
            )
        return cls(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=os.environ.get("WMS_ASSISTANT_LLM_API_KEY", "").strip(),
            timeout_s=timeout_s,
            compatibility_mode=compatibility_mode,
            max_concurrency=max_concurrency,
            health_timeout_s=health_timeout_s,
            max_output_tokens=max_output_tokens,
            circuit_failure_threshold=circuit_failure_threshold,
            circuit_cooldown_s=circuit_cooldown_s,
        )


class OpenAICompatibleProvider:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or httpx2.AsyncClient(
            timeout=config.timeout_s,
            limits=httpx2.Limits(
                max_connections=config.max_concurrency,
                max_keepalive_connections=config.max_concurrency,
            ),
            trust_env=False,
        )
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._health_lock = asyncio.Lock()
        self._cached_health: tuple[float, ProviderHealth] | None = None
        self._failure_lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    @property
    def provider_id(self) -> str:
        identity = self._config.profile_name or self._config.compatibility_mode
        return f"openai_compatible:{identity}"

    @property
    def model_id(self) -> str:
        return self._config.model

    async def complete(
        self,
        *,
        schema_name: str,
        response_model: type[ResponseModel],
        messages: Sequence[Mapping[str, str]],
    ) -> ResponseModel:
        await self._ensure_circuit_closed()
        response_schema = response_model.model_json_schema()
        request_messages = [dict(message) for message in messages]
        if self._config.compatibility_mode == "llama_cpp":
            response_schema = _llama_cpp_schema(response_schema)
            request_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object matching this JSON "
                        "Schema. Do not include Markdown or analysis.\n"
                        f"{json.dumps(response_schema, ensure_ascii=False)}"
                    ),
                }
            )
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": request_messages,
            "temperature": 0,
            "max_tokens": (
                min(self._config.max_output_tokens, 768)
                if schema_name == "assistant_capability_decision"
                else self._config.max_output_tokens
            ),
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            },
        }
        if self._config.compatibility_mode == "llama_cpp":
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {
            "Content-Type": "application/json",
            **dict(self._config.additional_headers),
        }
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        try:
            async with (
                self._semaphore,
                self._client.stream(
                    "POST",
                    self._endpoint(self._config.chat_completions_path),
                    json=payload,
                    headers=headers,
                    timeout=self._config.timeout_s,
                ) as response,
            ):
                if response.status_code >= 400:
                    detail = (
                        await _read_http_body(
                            response,
                            limit=2_000,
                            truncate=True,
                        )
                    ).decode("utf-8", errors="replace")
                    raise AssistantProviderError(
                        f"LLM endpoint returned HTTP {response.status_code}: {detail}"
                    )
                response_body = await _read_http_body(
                    response,
                    limit=_MAX_RESPONSE_BYTES,
                    truncate=False,
                )
        except AssistantProviderError:
            await self._record_failure()
            raise
        except (httpx2.RequestError, TimeoutError) as exc:
            await self._record_failure()
            raise AssistantProviderError(
                f"LLM request failed ({type(exc).__name__}): {exc}"
            ) from exc
        try:
            envelope = json.loads(response_body)
            message = envelope["choices"][0]["message"]
            raw_output = message.get("parsed")
            if raw_output is None:
                content = message.get("content")
                if not isinstance(content, str):
                    raise TypeError("message content is not a string")
                raw_output = json.loads(content)
            result = response_model.model_validate(raw_output)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AssistantOutputError(
                "LLM response did not contain one strict JSON object"
            ) from exc
        except ValidationError as exc:
            raise AssistantOutputError(
                f"LLM response violated the {schema_name} schema: {exc}"
            ) from exc
        await self._record_success()
        return result

    async def health(self, *, force: bool = False) -> ProviderHealth:
        circuit_remaining = await self._circuit_remaining_s()
        if circuit_remaining > 0:
            return ProviderHealth(
                available=False,
                reachable=True,
                model_available=None,
                latency_ms=0.0,
                detail=(
                    f"chat circuit is open for another {circuit_remaining:.1f} seconds"
                ),
            )
        now = time.monotonic()
        cached = self._cached_health
        if (
            not force
            and cached is not None
            and now - cached[0] < self._config.health_ttl_s
        ):
            return cached[1]

        async with self._health_lock:
            now = time.monotonic()
            cached = self._cached_health
            if (
                not force
                and cached is not None
                and now - cached[0] < self._config.health_ttl_s
            ):
                return cached[1]
            started = time.perf_counter()
            try:
                async with self._semaphore:
                    response = await self._client.get(
                        self._endpoint(self._config.models_path),
                        headers=self._authorization_headers(),
                        timeout=self._config.health_timeout_s,
                    )
                latency_ms = (time.perf_counter() - started) * 1_000.0
                if response.status_code in {404, 405}:
                    result = ProviderHealth(
                        available=True,
                        reachable=True,
                        model_available=None,
                        latency_ms=latency_ms,
                        detail="models endpoint is unavailable; chat will be attempted",
                    )
                elif response.status_code >= 400:
                    result = ProviderHealth(
                        available=False,
                        reachable=True,
                        model_available=None,
                        latency_ms=latency_ms,
                        detail=f"models endpoint returned HTTP {response.status_code}",
                    )
                else:
                    model_ids = _model_ids(response)
                    model_available = _model_is_advertised(
                        self._config.model,
                        model_ids,
                    )
                    result = ProviderHealth(
                        available=model_available,
                        reachable=True,
                        model_available=model_available,
                        latency_ms=latency_ms,
                        detail=(
                            "configured model is available"
                            if model_available
                            else "configured model is not advertised"
                        ),
                    )
            except (httpx2.RequestError, TimeoutError) as exc:
                result = ProviderHealth(
                    available=False,
                    reachable=False,
                    model_available=None,
                    latency_ms=(time.perf_counter() - started) * 1_000.0,
                    detail=f"health check failed: {exc}",
                )
            self._cached_health = (time.monotonic(), result)
            return result

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _ensure_circuit_closed(self) -> None:
        remaining = await self._circuit_remaining_s()
        if remaining > 0:
            raise AssistantProviderError(
                f"LLM circuit is open for another {remaining:.1f} seconds"
            )

    async def _circuit_remaining_s(self) -> float:
        async with self._failure_lock:
            return max(0.0, self._circuit_open_until - time.monotonic())

    async def _record_failure(self) -> None:
        async with self._failure_lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._config.circuit_failure_threshold:
                self._circuit_open_until = (
                    time.monotonic() + self._config.circuit_cooldown_s
                )

    async def _record_success(self) -> None:
        async with self._failure_lock:
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0

    def _authorization_headers(self) -> dict[str, str]:
        headers = dict(self._config.additional_headers)
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _endpoint(self, path: str) -> str:
        return f"{self._config.base_url}{path}"


async def _read_http_body(
    response: httpx2.Response,
    *,
    limit: int,
    truncate: bool,
) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        remaining = limit - len(body)
        if len(chunk) > remaining:
            if truncate:
                body.extend(chunk[:remaining])
                return bytes(body)
            raise AssistantProviderError("LLM response exceeded the size limit")
        body.extend(chunk)
    return bytes(body)


def _model_ids(response: httpx2.Response) -> tuple[str, ...]:
    try:
        payload = response.json()
    except ValueError:
        return ()
    raw_models = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(raw_models, list):
        return ()
    return tuple(
        str(item.get("id", item.get("name")))
        for item in raw_models
        if isinstance(item, Mapping)
        and isinstance(item.get("id", item.get("name")), str)
    )


def _model_is_advertised(model: str, model_ids: tuple[str, ...]) -> bool:
    if model in model_ids:
        return True
    model_name = model.rsplit("/", 1)[-1]
    return any(
        candidate.rsplit("/", 1)[-1] == model_name
        or model_name in candidate.rsplit("/", 1)[-1]
        for candidate in model_ids
    )


def _llama_cpp_schema(schema: Mapping[str, object]) -> dict[str, object]:
    """Inline Pydantic references and retain llama.cpp grammar keywords."""

    raw_definitions = schema.get("$defs")
    definitions = raw_definitions if isinstance(raw_definitions, Mapping) else {}
    supported = {
        "additionalProperties",
        "anyOf",
        "const",
        "enum",
        "items",
        "properties",
        "required",
        "type",
    }

    def visit(value: object) -> object:
        if isinstance(value, Mapping):
            reference = value.get("$ref")
            if isinstance(reference, str):
                prefix = "#/$defs/"
                if not reference.startswith(prefix):
                    raise AssistantCompileError(
                        f"unsupported JSON Schema reference: {reference}"
                    )
                definition = definitions.get(reference[len(prefix) :])
                if not isinstance(definition, Mapping):
                    raise AssistantCompileError(
                        f"unresolved JSON Schema reference: {reference}"
                    )
                return visit(definition)
            result: dict[str, object] = {}
            for key, child in value.items():
                if key not in supported:
                    continue
                if key == "properties":
                    if not isinstance(child, Mapping):
                        raise AssistantCompileError(
                            "JSON Schema properties must be an object"
                        )
                    result[key] = {
                        str(name): visit(property_schema)
                        for name, property_schema in child.items()
                    }
                else:
                    result[key] = visit(child)
            return result
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    result = visit(schema)
    if not isinstance(result, dict):
        raise AssistantCompileError("JSON Schema root must be an object")
    return result


def provider_from_configuration() -> StructuredLLMProvider | None:
    config_path = configured_provider_path()
    profile_name = configured_provider_profile()
    if config_path is not None:
        try:
            resolved = load_provider_profile(
                config_path,
                profile_name=profile_name,
            )
        except ProviderConfigurationError as exc:
            raise AssistantUnavailableError(str(exc)) from exc
        settings = resolved.settings
        config = OpenAICompatibleConfig(
            base_url=settings.base_url,
            model=settings.model,
            api_key=resolved.api_key,
            timeout_s=settings.timeout_s,
            profile_name=resolved.name,
            compatibility_mode=settings.compatibility_mode,
            max_concurrency=settings.max_concurrency,
            health_timeout_s=settings.health_timeout_s,
            health_ttl_s=settings.health_ttl_s,
            max_output_tokens=settings.max_output_tokens,
            circuit_failure_threshold=settings.circuit_failure_threshold,
            circuit_cooldown_s=settings.circuit_cooldown_s,
            chat_completions_path=settings.chat_completions_path,
            models_path=settings.models_path,
            additional_headers=tuple(resolved.headers.items()),
        )
        return OpenAICompatibleProvider(config)
    if profile_name is not None:
        raise AssistantUnavailableError(
            "WMS_ASSISTANT_PROVIDER_PROFILE requires WMS_ASSISTANT_PROVIDER_CONFIG"
        )
    config = OpenAICompatibleConfig.from_environment()
    return OpenAICompatibleProvider(config) if config is not None else None


def provider_from_environment() -> StructuredLLMProvider | None:
    return provider_from_configuration()


class AssistantService:
    def __init__(
        self,
        *,
        task_family_catalog: Mapping[str, Mapping[str, object]],
        family_registry: TaskFamilyRegistry,
        backend_registry: BackendRegistry,
        provider: StructuredLLMProvider | None,
    ) -> None:
        self._catalog = task_family_catalog
        self._family_registry = family_registry
        self._backend_registry = backend_registry
        self._provider = provider

    async def status(self, *, refresh: bool = False) -> dict[str, object]:
        provider = self._provider
        health = await provider.health(force=refresh) if provider is not None else None
        return {
            "configured": provider is not None,
            "available": health.available if health is not None else False,
            "provider_id": provider.provider_id if provider is not None else None,
            "model": provider.model_id if provider is not None else None,
            "health": health.model_dump(mode="json") if health is not None else None,
            "prompt_version": ASSISTANT_PROMPT_VERSION,
            "capability_mode": "llm_draft_sessions",
            "automatic_execution": False,
            "confirmed_execution": True,
        }

    async def draft(self, *, prompt: str, locale: str) -> AssistantDraft:
        return await self.draft_conversation(
            user_messages=[prompt],
            locale=locale,
            previous_draft=None,
        )

    async def draft_conversation(
        self,
        *,
        user_messages: Sequence[str],
        locale: str,
        previous_draft: AssistantDraft | None = None,
    ) -> AssistantDraft:
        provider = self._provider
        if provider is None:
            raise AssistantUnavailableError(
                "natural-language drafting is not configured; load a Provider "
                "configuration or set the legacy LLM environment variables"
            )
        normalized_messages = _normalize_user_messages(user_messages)
        authoritative_request = _render_user_conversation(normalized_messages)

        draft_id = uuid4().hex
        started = time.perf_counter()
        stage_latency_ms: dict[str, float] = {}
        intent_started = time.perf_counter()
        intent = await provider.complete(
            schema_name="assistant_intent_ir",
            response_model=IntentIR,
            messages=_intent_messages(
                user_messages=normalized_messages,
                locale=locale,
                previous_draft=previous_draft,
            ),
        )
        stage_latency_ms["intent_interpretation"] = (
            time.perf_counter() - intent_started
        ) * 1_000.0
        _validate_intent_evidence(intent, user_messages=normalized_messages)

        route_started = time.perf_counter()
        route = await provider.complete(
            schema_name="assistant_capability_decision",
            response_model=RouteDecision,
            messages=_route_messages(
                prompt=authoritative_request,
                locale=locale,
                intent=intent,
                capability_catalog=_compact_capability_catalog(
                    self._catalog,
                    locale=locale,
                ),
            ),
        )
        stage_latency_ms["capability_matching"] = (
            time.perf_counter() - route_started
        ) * 1_000.0
        if route.status != "selected":
            result = _non_proposal_draft(
                intent=intent,
                route=route,
                provider=provider,
                draft_id=draft_id,
                started=started,
                stage_latency_ms=stage_latency_ms,
            )
        else:
            result = await self._compile_selected_route(
                intent=intent,
                route=route,
                prompt=authoritative_request,
                source_messages=normalized_messages,
                description="\n".join(normalized_messages),
                locale=locale,
                provider=provider,
                draft_id=draft_id,
                started=started,
                stage_latency_ms=stage_latency_ms,
                previous_draft=previous_draft,
            )
        _LOG.info(
            "assistant_draft_completed draft_id=%s status=%s family=%s "
            "variant=%s model=%s latency_ms=%.3f",
            result.provenance.draft_id,
            result.status,
            result.family_id,
            result.variant_id,
            result.provenance.model,
            result.provenance.latency_ms,
        )
        return result

    async def _compile_selected_route(
        self,
        *,
        intent: IntentIR,
        route: RouteDecision,
        prompt: str,
        source_messages: Sequence[str],
        description: str,
        locale: str,
        provider: StructuredLLMProvider,
        draft_id: str,
        started: float,
        stage_latency_ms: dict[str, float],
        previous_draft: AssistantDraft | None,
    ) -> AssistantDraft:
        family_id = route.family_id
        variant_id = route.variant_id
        if family_id is None or variant_id is None:
            raise AssistantOutputError("selected route omitted its variant identity")
        family, variant = _catalog_variant(
            self._catalog,
            family_id=family_id,
            variant_id=variant_id,
        )
        starter = variant.get("starter_document")
        if not isinstance(starter, Mapping):
            raise AssistantCompileError(
                f"variant {variant_id!r} has no starter document"
            )
        document = copy.deepcopy(dict(starter))
        document["name"] = f"ai_{family_id}_{variant_id}_{draft_id[:8]}"
        document["description"] = description
        locked_paths = _locked_paths(variant)
        allowed_paths = _mutable_leaf_paths(document, locked_paths=locked_paths)
        synthesis_started = time.perf_counter()
        synthesis = await provider.complete(
            schema_name="assistant_contract_synthesis",
            response_model=ContractSynthesis,
            messages=_synthesis_messages(
                prompt=prompt,
                locale=locale,
                intent=intent,
                route=route,
                family=family,
                variant=variant,
                starter=document,
                allowed_paths=allowed_paths,
                previous_draft=previous_draft,
            ),
        )
        stage_latency_ms["contract_synthesis"] = (
            time.perf_counter() - synthesis_started
        ) * 1_000.0
        if synthesis.status == "needs_clarification":
            return AssistantDraft(
                status="needs_clarification",
                message=synthesis.message,
                family_id=family_id,
                variant_id=variant_id,
                confidence=route.confidence,
                questions=synthesis.questions,
                assumptions=synthesis.assumptions,
                patches=[],
                draft_document=None,
                validation=None,
                intent_ir=intent,
                capability_decision=route,
                contract_synthesis=synthesis,
                provenance=_provenance(
                    provider=provider,
                    draft_id=draft_id,
                    started=started,
                    stage_latency_ms=stage_latency_ms,
                ),
            )
        if synthesis.status == "unsupported":
            return AssistantDraft(
                status="unsupported",
                message=synthesis.message,
                family_id=None,
                variant_id=None,
                confidence=route.confidence,
                questions=[],
                assumptions=synthesis.assumptions,
                patches=[],
                draft_document=None,
                validation=None,
                intent_ir=intent,
                capability_decision=route,
                contract_synthesis=synthesis,
                provenance=_provenance(
                    provider=provider,
                    draft_id=draft_id,
                    started=started,
                    stage_latency_ms=stage_latency_ms,
                ),
            )

        applied_paths: set[str] = set()
        for patch in synthesis.patches:
            if patch.path in applied_paths:
                raise AssistantOutputError(
                    f"LLM returned duplicate patch path {patch.path!r}"
                )
            if patch.path not in allowed_paths:
                raise AssistantOutputError(
                    f"LLM attempted to patch locked or unknown field {patch.path!r}"
                )
            source_quote = patch.source_text.strip()
            if not _quote_is_grounded(source_quote, source_messages):
                raise AssistantOutputError(
                    f"patch {patch.path!r} source_text is not a user-request quote"
                )
            value = _decode_patch_value(patch)
            _replace_existing_value(document, patch.path, value)
            applied_paths.add(patch.path)

        try:
            request = request_from_document(document)
            backend = self._backend_registry.select(request).capabilities
            family_description = self._family_registry.describe_request(request)
        except (
            ScenarioValidationError,
            ProtocolValidationError,
            BackendRegistryError,
            TaskFamilyRegistryError,
        ) as exc:
            raise AssistantCompileError(
                f"LLM parameter patches did not compile into a valid contract: {exc}"
            ) from exc
        if (
            family_description["family_id"] != family_id
            or family_description["variant_id"] != variant_id
        ):
            raise AssistantCompileError(
                "compiled contract changed the selected task-family variant"
            )
        return AssistantDraft(
            status="proposal",
            message=synthesis.message,
            family_id=family_id,
            variant_id=variant_id,
            confidence=route.confidence,
            questions=[],
            assumptions=[
                *synthesis.assumptions,
                _starter_default_assumption(locale),
            ],
            patches=synthesis.patches,
            draft_document=document,
            validation=AssistantValidation(
                valid=True,
                backend=backend.document(),
                family=family_description,
            ),
            intent_ir=intent,
            capability_decision=route,
            contract_synthesis=synthesis,
            provenance=_provenance(
                provider=provider,
                draft_id=draft_id,
                started=started,
                stage_latency_ms=stage_latency_ms,
            ),
        )

    async def aclose(self) -> None:
        if self._provider is not None:
            await self._provider.aclose()


def _non_proposal_draft(
    *,
    intent: IntentIR,
    route: RouteDecision,
    provider: StructuredLLMProvider,
    draft_id: str,
    started: float,
    stage_latency_ms: Mapping[str, float],
) -> AssistantDraft:
    status = (
        "needs_clarification"
        if route.status == "needs_clarification"
        else "unsupported"
    )
    return AssistantDraft(
        status=status,
        message=route.message,
        family_id=None,
        variant_id=None,
        confidence=route.confidence,
        questions=route.questions,
        assumptions=[],
        patches=[],
        draft_document=None,
        validation=None,
        intent_ir=intent,
        capability_decision=route,
        contract_synthesis=None,
        provenance=_provenance(
            provider=provider,
            draft_id=draft_id,
            started=started,
            stage_latency_ms=stage_latency_ms,
        ),
    )


def _provenance(
    *,
    provider: StructuredLLMProvider,
    draft_id: str,
    started: float,
    stage_latency_ms: Mapping[str, float],
) -> AssistantProvenance:
    return AssistantProvenance(
        draft_id=draft_id,
        provider_id=provider.provider_id,
        model=provider.model_id,
        prompt_version=ASSISTANT_PROMPT_VERSION,
        latency_ms=(time.perf_counter() - started) * 1_000.0,
        stage_latency_ms=dict(stage_latency_ms),
        llm_call_count=len(stage_latency_ms),
    )


def _compact_capability_catalog(
    catalog: Mapping[str, Mapping[str, object]],
    *,
    locale: str,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for family_id in sorted(catalog):
        family = catalog[family_id]
        raw_variants = family.get("variants")
        if not isinstance(raw_variants, list):
            raise AssistantCompileError(
                f"family {family_id!r} has an invalid variant catalog"
            )
        variants: list[dict[str, object]] = []
        for raw_variant in raw_variants:
            if not isinstance(raw_variant, Mapping):
                raise AssistantCompileError(
                    f"family {family_id!r} contains an invalid variant"
                )
            starter = raw_variant.get("starter_document")
            if not isinstance(starter, Mapping):
                raise AssistantCompileError(
                    f"variant {raw_variant.get('variant_id')!r} has no starter"
                )
            assistant = raw_variant.get("assistant")
            if not isinstance(assistant, Mapping):
                raise AssistantCompileError(
                    f"variant {raw_variant.get('variant_id')!r} has no "
                    "assistant metadata"
                )
            localized_assistant = {
                "summary": assistant.get(
                    "summary_zh" if locale == "zh-CN" else "summary_en"
                ),
                "aliases": assistant.get("aliases"),
                "selection_cues": assistant.get("selection_cues"),
                "exclusion_cues": assistant.get("exclusion_cues"),
                "clarification_topics": assistant.get("clarification_topics"),
            }
            variants.append(
                {
                    "variant_id": raw_variant.get("variant_id"),
                    "assistant": localized_assistant,
                }
            )
        result.append(
            {
                "family_id": family_id,
                "backend_ids": family.get("backend_ids"),
                "variants": variants,
            }
        )
    return result


def _catalog_variant(
    catalog: Mapping[str, Mapping[str, object]],
    *,
    family_id: str,
    variant_id: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    try:
        family = catalog[family_id]
    except KeyError as exc:
        raise AssistantOutputError(
            f"LLM selected unknown family {family_id!r}"
        ) from exc
    raw_variants = family.get("variants")
    if not isinstance(raw_variants, list):
        raise AssistantCompileError(
            f"family {family_id!r} has an invalid variant catalog"
        )
    variant = next(
        (
            item
            for item in raw_variants
            if isinstance(item, Mapping) and item.get("variant_id") == variant_id
        ),
        None,
    )
    if variant is None:
        raise AssistantOutputError(
            f"LLM selected unknown variant {family_id!r}/{variant_id!r}"
        )
    return family, variant


def _locked_paths(variant: Mapping[str, object]) -> set[str]:
    result = set(_LOCKED_ROOT_FIELDS)
    selectors = variant.get("selectors")
    if isinstance(selectors, list):
        for selector in selectors:
            if isinstance(selector, Mapping):
                path = selector.get("path")
                if isinstance(path, str):
                    result.add(path)
    return result


def _mutable_leaf_paths(
    document: Mapping[str, object],
    *,
    locked_paths: set[str],
) -> set[str]:
    result: set[str] = set()

    def visit(value: object, path: tuple[str, ...]) -> None:
        dotted = ".".join(path)
        if dotted in locked_paths or not path:
            if not path and isinstance(value, Mapping):
                for key, child in value.items():
                    visit(child, (str(key),))
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(child, (*path, str(key)))
        elif isinstance(value, str):
            return
        else:
            result.add(dotted)

    visit(document, ())
    return result


def _decode_patch_value(patch: ParameterPatchProposal) -> object:
    try:
        value = json.loads(patch.value_json)
    except json.JSONDecodeError as exc:
        raise AssistantOutputError(
            f"patch {patch.path!r} value_json is invalid JSON"
        ) from exc
    if not _is_json_value(value):
        raise AssistantOutputError(f"patch {patch.path!r} contains a non-JSON value")
    return value


def _replace_existing_value(
    document: dict[str, object],
    path: str,
    value: object,
) -> None:
    keys = path.split(".")
    target: object = document
    for key in keys[:-1]:
        if not isinstance(target, dict) or key not in target:
            raise AssistantOutputError(f"patch path does not exist: {path!r}")
        target = target[key]
    final_key = keys[-1]
    if not isinstance(target, dict) or final_key not in target:
        raise AssistantOutputError(f"patch path does not exist: {path!r}")
    existing = target[final_key]
    if isinstance(existing, bool):
        compatible = isinstance(value, bool)
    elif isinstance(existing, (int, float)) and not isinstance(existing, bool):
        compatible = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif isinstance(existing, list):
        compatible = isinstance(value, list)
    elif existing is None:
        compatible = value is None or isinstance(value, (int, float, str, bool))
    else:
        compatible = type(value) is type(existing)
    if not compatible:
        raise AssistantOutputError(
            f"patch {path!r} changed value type from "
            f"{type(existing).__name__} to {type(value).__name__}"
        )
    target[final_key] = value


def _is_json_value(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if value is None or isinstance(value, (str, int, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def _normalize_user_messages(user_messages: Sequence[str]) -> tuple[str, ...]:
    if isinstance(user_messages, (str, bytes)):
        raise AssistantCompileError("user_messages must be a sequence of turns")
    if not user_messages:
        raise AssistantCompileError("assistant conversation must not be empty")
    if len(user_messages) > _MAX_CONVERSATION_TURNS:
        raise AssistantCompileError(
            f"assistant conversation exceeds {_MAX_CONVERSATION_TURNS} user turns"
        )
    normalized: list[str] = []
    for message in user_messages:
        if not isinstance(message, str):
            raise AssistantCompileError("assistant user turns must be strings")
        content = message.strip()
        if not content:
            raise AssistantCompileError("assistant user turns must not be empty")
        if len(content) > 4_000:
            raise AssistantCompileError(
                "individual assistant user turns must not exceed 4000 characters"
            )
        normalized.append(content)
    if sum(len(message) for message in normalized) > _MAX_CONVERSATION_CHARACTERS:
        raise AssistantCompileError(
            "assistant conversation exceeds the total character limit"
        )
    return tuple(normalized)


def _render_user_conversation(user_messages: Sequence[str]) -> str:
    return json.dumps(
        [
            {
                "turn": index,
                "content": message,
            }
            for index, message in enumerate(user_messages, start=1)
        ],
        ensure_ascii=False,
    )


def _quote_is_grounded(quote: str, user_messages: Sequence[str]) -> bool:
    return bool(quote) and any(quote in message for message in user_messages)


def _validate_intent_evidence(
    intent: IntentIR,
    *,
    user_messages: Sequence[str],
) -> None:
    for evidence in (*intent.explicit_requirements, *intent.exclusions):
        source_quote = evidence.source_text.strip()
        if not _quote_is_grounded(source_quote, user_messages):
            raise AssistantOutputError(
                f"intent evidence {evidence.concept!r} is not a user-request quote"
            )
        if evidence.value_json is None:
            continue
        try:
            value = json.loads(evidence.value_json)
        except json.JSONDecodeError as exc:
            raise AssistantOutputError(
                f"intent evidence {evidence.concept!r} has invalid value_json"
            ) from exc
        if not _is_json_value(value):
            raise AssistantOutputError(
                f"intent evidence {evidence.concept!r} contains a non-JSON value"
            )


def _previous_draft_context(
    previous_draft: AssistantDraft | None,
) -> dict[str, object] | None:
    if previous_draft is None:
        return None
    return {
        "intent_ir": previous_draft.intent_ir.model_dump(mode="json"),
        "family_id": previous_draft.family_id,
        "variant_id": previous_draft.variant_id,
        "patches": [patch.model_dump(mode="json") for patch in previous_draft.patches],
        "status": previous_draft.status,
    }


def _intent_messages(
    *,
    user_messages: Sequence[str],
    locale: str,
    previous_draft: AssistantDraft | None,
) -> list[dict[str, str]]:
    language = "Chinese" if locale == "zh-CN" else "English"
    previous_context = _previous_draft_context(previous_draft)
    return [
        {
            "role": "system",
            "content": (
                "You are the semantic intent interpreter for an aerospace "
                "simulation system. Reconstruct the user's current complete "
                "intent from the chronological conversation before any capability "
                "is selected. Later user turns supersede conflicting earlier "
                "instructions; do not keep superseded requirements or report "
                "resolved ambiguities. Capture current goals, entities, explicit "
                "physical requirements, exclusions, requested outputs, and "
                "genuine remaining ambiguities. Separate explicit requirements "
                "from reasonable domain inferences. Explicit evidence source_text "
                "must be an exact quote from one user turn. If an explicit value "
                "exists, encode its normalized value as one JSON value in "
                "value_json and preserve its unit; otherwise use null. Inferred "
                "requirements must include a concise rationale and confidence. "
                "Previous compiler state is context, not user evidence. "
                "Do not choose a registered task, backend, model, component, "
                "equation, or default. Do not treat missing optional parameters "
                "as ambiguities. Do not invent facts. "
                f"Write semantic text in {language}."
            ),
        },
        {
            "role": "user",
            "content": (
                "Chronological user conversation:\n"
                f"{_render_user_conversation(user_messages)}\n\n"
                "Previous compiler state:\n"
                f"{json.dumps(previous_context, ensure_ascii=False)}"
            ),
        },
    ]


def _route_messages(
    *,
    prompt: str,
    locale: str,
    intent: IntentIR,
    capability_catalog: list[dict[str, object]],
) -> list[dict[str, str]]:
    language = "Chinese" if locale == "zh-CN" else "English"
    return [
        {
            "role": "system",
            "content": (
                "You are the LLM capability matcher for a fail-closed aerospace "
                "simulation product. Semantically compare the complete IntentIR "
                "against every registered variant. Select exactly one variant "
                "only when it faithfully represents all material user goals, "
                "explicit requirements, exclusions, and justified inferences. "
                "The chronological conversation is authoritative if the IntentIR "
                "conflicts with it. Never invent a task, model, component, backend, "
                "equation, or approximation. Never choose the nearest variant "
                "when a material requirement is unsupported. "
                "If several variants remain plausible, request clarification. "
                "If no variant can represent the request, return unsupported. "
                "For selected, set both ids and return no questions. For "
                "needs_clarification, set both ids to null and ask at least one "
                "question. For unsupported, set both ids to null and return no "
                "questions and list concrete capability_gaps. Record concise, "
                "user-visible decision_basis entries. A selected route must have "
                "no capability_gaps. "
                f"Write messages, questions, and decision evidence in {language}."
            ),
        },
        {
            "role": "user",
            "content": (
                "Interpreted IntentIR:\n"
                f"{intent.model_dump_json()}\n\n"
                "Registered capability catalog:\n"
                f"{json.dumps(capability_catalog, ensure_ascii=False)}\n\n"
                "Authoritative chronological user conversation:\n"
                f"{prompt}"
            ),
        },
    ]


def _synthesis_messages(
    *,
    prompt: str,
    locale: str,
    intent: IntentIR,
    route: RouteDecision,
    family: Mapping[str, object],
    variant: Mapping[str, object],
    starter: Mapping[str, object],
    allowed_paths: set[str],
    previous_draft: AssistantDraft | None,
) -> list[dict[str, str]]:
    language = "Chinese" if locale == "zh-CN" else "English"
    parameter_metadata = _assistant_parameters_for_paths(
        family,
        allowed_paths=allowed_paths,
    )
    return [
        {
            "role": "system",
            "content": (
                "You are the ContractSynthesizer for one LLM-selected aerospace "
                "simulation variant. Semantically map the full IntentIR into the "
                "registered starter contract. Audit every material explicit and "
                "inferred requirement: list represented requirements in "
                "mapped_requirements and requirements that the selected variant "
                "cannot represent in unmapped_requirements. Never silently omit "
                "or weaken a requirement. If any material requirement is "
                "unrepresentable, return unsupported with no patches. If a "
                "necessary ambiguity prevents a reliable contract, return "
                "needs_clarification with questions and no patches. Otherwise "
                "return synthesized and no unmapped requirements. "
                "For synthesized results, return patches only for values stated "
                "by the user or unambiguously converted to declared contract "
                "units. Do not patch unspecified values; the starter contract "
                "supplies defaults. Do not invent physics, values, or identifiers. "
                "Each value_json must be one valid JSON value. source_text must "
                "quote one authoritative user turn exactly. Every currently "
                "active explicit numeric physical parameter matching an editable "
                "parameter must produce one patch, including values retained from "
                "earlier turns. Do not emit superseded values. Convert units "
                "deterministically. Previous compiler state is context only; "
                "rebuild the complete patch set against the registered starter. "
                f"Write all user-visible text in {language}."
            ),
        },
        {
            "role": "user",
            "content": (
                "IntentIR:\n"
                f"{intent.model_dump_json()}\n\n"
                "Capability decision:\n"
                f"{route.model_dump_json()}\n\n"
                "Previous compiler state:\n"
                f"{json.dumps(_previous_draft_context(previous_draft), ensure_ascii=False)}\n\n"
                f"Selected family: {family.get('family_id')}\n"
                f"Selected variant: {variant.get('variant_id')}\n"
                "Variant semantics:\n"
                f"{json.dumps(variant.get('assistant'), ensure_ascii=False)}\n\n"
                "Editable parameter semantics:\n"
                f"{json.dumps(parameter_metadata, ensure_ascii=False)}\n\n"
                "Allowed patch paths:\n"
                f"{json.dumps(sorted(allowed_paths), ensure_ascii=False)}\n\n"
                "Starter contract:\n"
                f"{json.dumps(starter, ensure_ascii=False)}\n\n"
                "Authoritative chronological user conversation:\n"
                f"{prompt}"
            ),
        },
    ]


def _assistant_parameters_for_paths(
    family: Mapping[str, object],
    *,
    allowed_paths: set[str],
) -> list[dict[str, object]]:
    raw_parameters = family.get("assistant_parameters")
    if not isinstance(raw_parameters, list):
        raise AssistantCompileError("task family has no assistant parameter metadata")
    result: list[dict[str, object]] = []
    for parameter in raw_parameters:
        if not isinstance(parameter, Mapping):
            raise AssistantCompileError(
                "task family contains invalid assistant parameter metadata"
            )
        path = parameter.get("path")
        if isinstance(path, str) and path in allowed_paths:
            result.append(dict(parameter))
    return result


def _starter_default_assumption(locale: str) -> str:
    if locale == "zh-CN":
        return "未出现在参数补丁中的字段沿用已验证 starter contract 默认值。"
    return (
        "Fields not listed in the parameter patches retain validated starter "
        "contract defaults."
    )
