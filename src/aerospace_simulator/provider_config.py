from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .paths import user_config_root

PROVIDER_CONFIG_SCHEMA_VERSION = 1
PROVIDER_CONFIG_ENV = "XAEROSPACE_PROVIDER_CONFIG"
PROVIDER_PROFILE_ENV = "XAEROSPACE_PROVIDER_PROFILE"
DEFAULT_LOCAL_PROVIDER_CONFIG = Path("config/providers.local.json")
_MAX_CONFIG_BYTES = 1_000_000
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ProviderConfigurationError(RuntimeError):
    """Raised when a provider configuration cannot be loaded safely."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenAICompatibleProfile(_StrictModel):
    type: Literal["openai_compatible"]
    base_url: str = Field(min_length=1, max_length=2_048)
    model: str = Field(min_length=1, max_length=1_024)
    api_key_env: str | None = Field(default=None, max_length=128)
    header_env: dict[str, str] = Field(default_factory=dict, max_length=16)
    chat_completions_path: str = Field(
        default="/chat/completions",
        min_length=1,
        max_length=1_024,
    )
    models_path: str = Field(default="/models", min_length=1, max_length=1_024)
    timeout_s: float = Field(default=45.0, ge=1.0, le=600.0)
    compatibility_mode: Literal["strict", "llama_cpp"] = "strict"
    max_concurrency: int = Field(default=1, ge=1, le=8)
    health_timeout_s: float = Field(default=10.0, ge=1.0, le=30.0)
    health_ttl_s: float = Field(default=30.0, ge=0.0, le=600.0)
    max_output_tokens: int = Field(default=1_024, ge=128, le=16_384)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=10)
    circuit_cooldown_s: float = Field(default=60.0, ge=1.0, le=600.0)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "base_url must not contain credentials; use environment-backed auth"
            )
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must not be empty")
        return normalized

    @field_validator("api_key_env")
    @classmethod
    def validate_api_key_environment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _ENVIRONMENT_NAME.fullmatch(normalized):
            raise ValueError("api_key_env must be an environment variable name")
        return normalized

    @field_validator("header_env")
    @classmethod
    def validate_header_environment(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_header, raw_environment in value.items():
            header = raw_header.strip()
            environment = raw_environment.strip()
            if (
                not header
                or any(character in header for character in "\r\n:")
                or header.lower() in {"content-type", "host"}
            ):
                raise ValueError(f"invalid configurable header name: {raw_header!r}")
            if not _ENVIRONMENT_NAME.fullmatch(environment):
                raise ValueError(
                    f"header {header!r} must reference an environment variable"
                )
            result[header] = environment
        return result

    @field_validator("chat_completions_path", "models_path")
    @classmethod
    def validate_endpoint_path(cls, value: str) -> str:
        normalized = value.strip()
        parsed = urlparse(normalized)
        if (
            not normalized.startswith("/")
            or normalized.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
        ):
            raise ValueError("endpoint paths must be absolute URL paths")
        return normalized

    @model_validator(mode="after")
    def validate_authentication(self) -> OpenAICompatibleProfile:
        if self.api_key_env is not None and any(
            header.lower() == "authorization" for header in self.header_env
        ):
            raise ValueError(
                "api_key_env and an Authorization header_env are mutually exclusive"
            )
        return self


class ProviderConfiguration(_StrictModel):
    schema_version: Literal[1]
    active_provider: str = Field(min_length=1, max_length=64)
    providers: dict[str, OpenAICompatibleProfile] = Field(
        min_length=1,
        max_length=32,
    )

    @field_validator("active_provider")
    @classmethod
    def validate_active_provider(cls, value: str) -> str:
        if not _PROFILE_NAME.fullmatch(value):
            raise ValueError("active_provider is not a valid profile name")
        return value

    @field_validator("providers")
    @classmethod
    def validate_provider_names(
        cls,
        value: dict[str, OpenAICompatibleProfile],
    ) -> dict[str, OpenAICompatibleProfile]:
        invalid = [name for name in value if not _PROFILE_NAME.fullmatch(name)]
        if invalid:
            raise ValueError(
                "invalid provider profile names: " + ", ".join(sorted(invalid))
            )
        return value

    @model_validator(mode="after")
    def validate_active_profile(self) -> ProviderConfiguration:
        if self.active_provider not in self.providers:
            raise ValueError(
                f"active_provider {self.active_provider!r} is not declared"
            )
        return self


@dataclass(frozen=True)
class ResolvedProviderProfile:
    name: str
    settings: OpenAICompatibleProfile
    api_key: str
    headers: dict[str, str]
    source: Path


def load_provider_profile(
    path: str | Path,
    *,
    profile_name: str | None = None,
    environ: dict[str, str] | None = None,
) -> ResolvedProviderProfile:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ProviderConfigurationError(
            f"provider configuration file not found: {source}"
        )
    if source.stat().st_size > _MAX_CONFIG_BYTES:
        raise ProviderConfigurationError(
            f"provider configuration exceeds {_MAX_CONFIG_BYTES} bytes"
        )
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderConfigurationError(
            f"provider configuration is not valid UTF-8 JSON: {source}"
        ) from exc
    try:
        configuration = ProviderConfiguration.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise ProviderConfigurationError(
            f"invalid provider configuration {source}: {details}"
        ) from exc

    selected_name = (profile_name or configuration.active_provider).strip()
    if not _PROFILE_NAME.fullmatch(selected_name):
        raise ProviderConfigurationError(
            f"invalid provider profile name: {selected_name!r}"
        )
    try:
        settings = configuration.providers[selected_name]
    except KeyError as exc:
        raise ProviderConfigurationError(
            f"provider profile {selected_name!r} is not declared in {source}"
        ) from exc

    environment = os.environ if environ is None else environ
    api_key = ""
    if settings.api_key_env is not None:
        api_key = environment.get(settings.api_key_env, "").strip()
        if not api_key:
            raise ProviderConfigurationError(
                f"provider profile {selected_name!r} requires environment "
                f"variable {settings.api_key_env}"
            )

    headers: dict[str, str] = {}
    for header, environment_name in settings.header_env.items():
        value = environment.get(environment_name, "").strip()
        if not value:
            raise ProviderConfigurationError(
                f"provider profile {selected_name!r} requires environment "
                f"variable {environment_name} for header {header}"
            )
        headers[header] = value

    return ResolvedProviderProfile(
        name=selected_name,
        settings=settings,
        api_key=api_key,
        headers=headers,
        source=source,
    )


def configured_provider_path() -> Path | None:
    raw_path = os.environ.get(PROVIDER_CONFIG_ENV, "").strip()
    return Path(raw_path).expanduser() if raw_path else None


def configured_provider_profile() -> str | None:
    value = os.environ.get(PROVIDER_PROFILE_ENV, "").strip()
    return value or None


def discover_local_provider_config(
    *,
    project_root: Path | None = None,
    cwd: Path | None = None,
    home_config: Path | None = None,
) -> Path | None:
    candidates = [
        (cwd or Path.cwd()) / DEFAULT_LOCAL_PROVIDER_CONFIG,
    ]
    if project_root is not None:
        candidates.append(project_root / DEFAULT_LOCAL_PROVIDER_CONFIG)
    candidates.append(home_config or user_config_root() / "providers.local.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None
