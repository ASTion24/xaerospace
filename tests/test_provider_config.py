from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx2
import pytest

from aerospace_simulator.assistant import (
    AssistantUnavailableError,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    RouteDecision,
    provider_from_configuration,
)
from aerospace_simulator.cli import _configure_provider_environment, build_parser
from aerospace_simulator.provider_config import (
    PROVIDER_CONFIG_ENV,
    PROVIDER_PROFILE_ENV,
    ProviderConfigurationError,
    discover_local_provider_config,
    load_provider_profile,
)


def _write_configuration(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_provider": "local",
                "providers": {
                    "local": {
                        "type": "openai_compatible",
                        "base_url": "http://local.test/v1/",
                        "model": "local-model",
                        "compatibility_mode": "llama_cpp",
                        "timeout_s": 120,
                    },
                    "cloud": {
                        "type": "openai_compatible",
                        "base_url": "https://cloud.test/api",
                        "model": "cloud-model",
                        "api_key_env": "TEST_CLOUD_API_KEY",
                        "header_env": {"X-Organization": "TEST_CLOUD_ORGANIZATION"},
                        "chat_completions_path": "/structured/chat",
                        "models_path": "/catalog/models?api-version=1",
                        "timeout_s": 30,
                        "max_concurrency": 2,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_provider_configuration_selects_profiles_and_resolves_secrets(tmp_path):
    path = _write_configuration(tmp_path / "providers.json")

    active = load_provider_profile(path, environ={})
    selected = load_provider_profile(
        path,
        profile_name="cloud",
        environ={
            "TEST_CLOUD_API_KEY": "secret-token",
            "TEST_CLOUD_ORGANIZATION": "test-org",
        },
    )

    assert active.name == "local"
    assert active.settings.base_url == "http://local.test/v1"
    assert active.settings.compatibility_mode == "llama_cpp"
    assert active.api_key == ""
    assert selected.name == "cloud"
    assert selected.api_key == "secret-token"
    assert selected.headers == {"X-Organization": "test-org"}
    assert selected.settings.chat_completions_path == "/structured/chat"
    assert selected.settings.models_path == "/catalog/models?api-version=1"


def test_provider_configuration_rejects_missing_or_literal_secrets(tmp_path):
    path = _write_configuration(tmp_path / "providers.json")
    with pytest.raises(ProviderConfigurationError, match="TEST_CLOUD_API_KEY"):
        load_provider_profile(path, profile_name="cloud", environ={})
    with pytest.raises(ProviderConfigurationError, match="not declared"):
        load_provider_profile(path, profile_name="missing", environ={})

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["providers"]["local"]["api_key"] = "must-not-be-stored-here"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ProviderConfigurationError, match="api_key") as error:
        load_provider_profile(path, environ={})
    assert "must-not-be-stored-here" not in str(error.value)


def test_provider_factory_uses_named_profile_without_exposing_endpoint(
    tmp_path,
    monkeypatch,
):
    path = _write_configuration(tmp_path / "providers.json")
    monkeypatch.setenv(PROVIDER_CONFIG_ENV, str(path))
    monkeypatch.setenv(PROVIDER_PROFILE_ENV, "cloud")
    monkeypatch.setenv("TEST_CLOUD_API_KEY", "secret-token")
    monkeypatch.setenv("TEST_CLOUD_ORGANIZATION", "test-org")
    monkeypatch.setenv(
        "XAEROSPACE_ASSISTANT_LLM_BASE_URL",
        "http://direct-environment.test/v1",
    )
    monkeypatch.setenv("XAEROSPACE_ASSISTANT_LLM_MODEL", "direct-model")

    provider = provider_from_configuration()

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_id == "openai_compatible:cloud"
    assert provider.model_id == "cloud-model"
    assert provider._config.base_url == "https://cloud.test/api"
    assert provider._config.api_key == "secret-token"
    assert provider._config.additional_headers == (("X-Organization", "test-org"),)
    asyncio.run(provider.aclose())


def test_configured_provider_uses_custom_paths_and_authentication_headers(tmp_path):
    path = _write_configuration(tmp_path / "providers.json")
    resolved = load_provider_profile(
        path,
        profile_name="cloud",
        environ={
            "TEST_CLOUD_API_KEY": "secret-token",
            "TEST_CLOUD_ORGANIZATION": "test-org",
        },
    )
    requests: list[httpx2.Request] = []

    async def exercise():
        async def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx2.Response(200, json={"data": [{"id": "cloud-model"}]})
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "parsed": {
                                    "status": "unsupported",
                                    "family_id": None,
                                    "variant_id": None,
                                    "confidence": 1.0,
                                    "message": "Unsupported.",
                                    "questions": [],
                                    "decision_basis": [],
                                    "capability_gaps": ["No registered capability."],
                                }
                            }
                        }
                    ]
                },
            )

        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        settings = resolved.settings
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=settings.base_url,
                model=settings.model,
                api_key=resolved.api_key,
                timeout_s=settings.timeout_s,
                profile_name=resolved.name,
                chat_completions_path=settings.chat_completions_path,
                models_path=settings.models_path,
                additional_headers=tuple(resolved.headers.items()),
            ),
            client=client,
        )
        await provider.complete(
            schema_name="assistant_capability_decision",
            response_model=RouteDecision,
            messages=[{"role": "user", "content": "test"}],
        )
        health = await provider.health()
        await client.aclose()
        return health

    health = asyncio.run(exercise())

    assert health.available is True
    assert str(requests[0].url) == "https://cloud.test/api/structured/chat"
    assert str(requests[1].url) == (
        "https://cloud.test/api/catalog/models?api-version=1"
    )
    assert requests[0].headers["Authorization"] == "Bearer secret-token"
    assert requests[0].headers["X-Organization"] == "test-org"
    assert requests[1].headers["Authorization"] == "Bearer secret-token"
    assert requests[1].headers["X-Organization"] == "test-org"


def test_provider_profile_requires_configuration_path(monkeypatch):
    monkeypatch.delenv(PROVIDER_CONFIG_ENV, raising=False)
    monkeypatch.setenv(PROVIDER_PROFILE_ENV, "cloud")
    with pytest.raises(AssistantUnavailableError, match=PROVIDER_CONFIG_ENV):
        provider_from_configuration()


def test_cli_provider_arguments_and_local_discovery(tmp_path, monkeypatch):
    path = _write_configuration(tmp_path / "providers.json")
    parsed = build_parser().parse_args(
        [
            "web",
            "--provider-config",
            str(path),
            "--provider-profile",
            "cloud",
            "--no-browser",
        ]
    )
    monkeypatch.setenv(PROVIDER_CONFIG_ENV, "")
    monkeypatch.setenv(PROVIDER_PROFILE_ENV, "")

    _configure_provider_environment(parsed)

    assert Path(os.environ[PROVIDER_CONFIG_ENV]).resolve() == path.resolve()
    assert os.environ[PROVIDER_PROFILE_ENV] == "cloud"

    local = tmp_path / "config" / "providers.local.json"
    local.parent.mkdir()
    local.write_text("{}", encoding="utf-8")
    assert discover_local_provider_config(cwd=tmp_path) == local.resolve()
