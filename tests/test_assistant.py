from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeVar

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from aerospace_simulator.assistant import (
    AssistantProviderError,
    AssistantUnavailableError,
    ContractSynthesis,
    IntentIR,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ProviderHealth,
    RouteDecision,
    StructuredLLMProvider,
    _llama_cpp_schema,
)
from aerospace_simulator.provider_config import (
    PROVIDER_CONFIG_ENV,
    PROVIDER_PROFILE_ENV,
)
from aerospace_simulator.web_api import create_app
from aerospace_simulator.workflows import WorkflowStore

PROJECT_ROOT = Path(__file__).parents[1]
SCENARIO_DIR = PROJECT_ROOT / "scenarios"
WEB_DIR = PROJECT_ROOT / "web"
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class ScriptedProvider(StructuredLLMProvider):
    def __init__(
        self,
        *outputs: Mapping[str, object],
        intent_output: Mapping[str, object] | None = None,
    ) -> None:
        self._outputs = list(outputs)
        self._intent_output = intent_output or _intent_ir()
        self.calls: list[dict[str, object]] = []

    @property
    def provider_id(self) -> str:
        return "scripted"

    @property
    def model_id(self) -> str:
        return "scripted-test-model"

    async def complete(
        self,
        *,
        schema_name: str,
        response_model: type[ResponseModel],
        messages: Sequence[Mapping[str, str]],
    ) -> ResponseModel:
        self.calls.append(
            {
                "schema_name": schema_name,
                "messages": [dict(message) for message in messages],
            }
        )
        if schema_name == "assistant_intent_ir":
            return response_model.model_validate(self._intent_output)
        if not self._outputs:
            raise AssertionError("scripted provider received an unexpected call")
        output = self._outputs.pop(0)
        if schema_name == "assistant_contract_synthesis" and "status" not in output:
            output = _synthesized(
                patches=list(output.get("patches", [])),
                assumptions=list(output.get("assumptions", [])),
            )
        return response_model.model_validate(output)

    async def health(self, *, force: bool = False) -> ProviderHealth:
        return ProviderHealth(
            available=True,
            reachable=True,
            model_available=True,
            latency_ms=0.1,
            detail="scripted provider is healthy",
        )

    async def aclose(self) -> None:
        return None


def _client(tmp_path, provider=None):
    store = WorkflowStore(tmp_path / "runs")
    app = create_app(
        workflow_store=store,
        scenarios_directory=SCENARIO_DIR,
        web_directory=WEB_DIR,
        assistant_provider=provider,
    )
    return TestClient(app)


def _selected_route(
    *,
    family_id: str = "rocket_flight",
    variant_id: str = "point_mass_3dof_recovery",
) -> dict[str, object]:
    return {
        "status": "selected",
        "family_id": family_id,
        "variant_id": variant_id,
        "confidence": 0.98,
        "message": "已生成受约束的合同草案。",
        "questions": [],
        "decision_basis": ["所选变体完整覆盖用户需求。"],
        "capability_gaps": [],
    }


def _intent_ir() -> dict[str, object]:
    return {
        "task_summary": "执行用户描述的航空航天仿真任务。",
        "domain_hints": [],
        "entities": [],
        "goals": ["生成可信仿真合同"],
        "explicit_requirements": [],
        "inferred_requirements": [],
        "exclusions": [],
        "requested_outputs": [],
        "ambiguities": [],
    }


def _synthesized(
    *,
    patches: list[dict[str, object]] | None = None,
    assumptions: list[str] | None = None,
) -> dict[str, object]:
    return {
        "status": "synthesized",
        "message": "已将全部需求映射到受约束合同。",
        "patches": patches or [],
        "assumptions": assumptions or [],
        "questions": [],
        "mapped_requirements": ["用户任务目标"],
        "unmapped_requirements": [],
    }


def test_openai_provider_requires_complete_configuration_and_strict_json_schema(
    monkeypatch,
):
    monkeypatch.setenv("XAEROSPACE_ASSISTANT_LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.delenv("XAEROSPACE_ASSISTANT_LLM_MODEL", raising=False)
    with pytest.raises(AssistantUnavailableError, match="configured together"):
        OpenAICompatibleConfig.from_environment()
    monkeypatch.setenv("XAEROSPACE_ASSISTANT_LLM_MODEL", "test-model")
    monkeypatch.setenv(
        "XAEROSPACE_ASSISTANT_LLM_COMPATIBILITY_MODE",
        "invalid",
    )
    with pytest.raises(AssistantUnavailableError, match="COMPATIBILITY_MODE"):
        OpenAICompatibleConfig.from_environment()

    async def exercise_provider():
        requests: list[httpx2.Request] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if request.url.path.endswith("/models"):
                return httpx2.Response(
                    200,
                    json={"data": [{"id": "test-model"}]},
                )
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

        client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler),
        )
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url="http://llm.test/v1",
                model="test-model",
                api_key="secret",
                timeout_s=12.0,
            ),
            client=client,
        )
        result = await provider.complete(
            schema_name="assistant_capability_decision",
            response_model=RouteDecision,
            messages=[{"role": "user", "content": "test"}],
        )
        health = await provider.health()
        cached_health = await provider.health()
        payload = json.loads(requests[0].content)

        compatible_provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url="http://llm.test/v1",
                model="test-model",
                api_key="",
                timeout_s=12.0,
                compatibility_mode="llama_cpp",
            ),
            client=client,
        )
        await compatible_provider.complete(
            schema_name="assistant_capability_decision",
            response_model=RouteDecision,
            messages=[{"role": "user", "content": "test"}],
        )
        compatible_payload = json.loads(requests[-1].content)
        await client.aclose()
        return (
            result,
            health,
            cached_health,
            requests,
            payload,
            compatible_payload,
        )

    result, health, cached_health, requests, payload, compatible_payload = asyncio.run(
        exercise_provider()
    )
    compatible_schema = _llama_cpp_schema(ContractSynthesis.model_json_schema())
    intent_schema = _llama_cpp_schema(IntentIR.model_json_schema())

    assert result.status == "unsupported"
    assert health.available is True
    assert cached_health == health
    assert sum(request.url.path.endswith("/models") for request in requests) == 1
    assert str(requests[0].url) == "http://llm.test/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer secret"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 768
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert compatible_payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert (
        "Return exactly one JSON object"
        in compatible_payload["messages"][-1]["content"]
    )
    assert "$defs" not in json.dumps(compatible_schema)
    assert "$defs" not in json.dumps(intent_schema)
    assert (
        compatible_schema["properties"]["patches"]["items"]["properties"]["path"][
            "type"
        ]
        == "string"
    )


def test_removed_wms_provider_environment_is_ignored(monkeypatch):
    monkeypatch.delenv("XAEROSPACE_ASSISTANT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("XAEROSPACE_ASSISTANT_LLM_MODEL", raising=False)
    monkeypatch.setenv("WMS_ASSISTANT_LLM_BASE_URL", "http://removed.test/v1")
    monkeypatch.setenv("WMS_ASSISTANT_LLM_MODEL", "removed-model")

    assert OpenAICompatibleConfig.from_environment() is None


def test_openai_provider_bounds_concurrency_and_supports_cancellation():
    async def exercise():
        active = 0
        maximum_active = 0

        async def handler(_: httpx2.Request) -> httpx2.Response:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                await asyncio.sleep(0.05)
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
                                        "capability_gaps": [
                                            "No registered capability."
                                        ],
                                    }
                                }
                            }
                        ]
                    },
                )
            finally:
                active -= 1

        client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler),
        )
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url="http://llm.test/v1",
                model="test-model",
                api_key="",
                timeout_s=12.0,
                max_concurrency=2,
            ),
            client=client,
        )

        async def complete():
            return await provider.complete(
                schema_name="assistant_capability_decision",
                response_model=RouteDecision,
                messages=[{"role": "user", "content": "test"}],
            )

        await asyncio.gather(complete(), complete(), complete())
        task = asyncio.create_task(complete())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await client.aclose()
        return maximum_active

    assert asyncio.run(exercise()) == 2


def test_openai_provider_opens_circuit_after_repeated_transport_failures():
    async def exercise():
        request_count = 0

        async def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal request_count
            request_count += 1
            raise httpx2.ConnectError("offline", request=request)

        client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler),
        )
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url="http://llm.test/v1",
                model="test-model",
                api_key="",
                timeout_s=12.0,
                circuit_failure_threshold=2,
                circuit_cooldown_s=60.0,
            ),
            client=client,
        )

        async def complete():
            return await provider.complete(
                schema_name="assistant_capability_decision",
                response_model=RouteDecision,
                messages=[{"role": "user", "content": "test"}],
            )

        with pytest.raises(AssistantProviderError, match="ConnectError"):
            await complete()
        with pytest.raises(AssistantProviderError, match="ConnectError"):
            await complete()
        with pytest.raises(AssistantProviderError, match="circuit is open"):
            await complete()
        health = await provider.health()
        await client.aclose()
        return request_count, health

    request_count, health = asyncio.run(exercise())
    assert request_count == 2
    assert health.available is False
    assert "circuit is open" in health.detail


def test_assistant_status_is_explicitly_unavailable_without_provider(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("XAEROSPACE_ASSISTANT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("XAEROSPACE_ASSISTANT_LLM_MODEL", raising=False)
    monkeypatch.delenv(PROVIDER_CONFIG_ENV, raising=False)
    monkeypatch.delenv(PROVIDER_PROFILE_ENV, raising=False)
    with _client(tmp_path) as client:
        status = client.get("/api/assistant/status")
        draft = client.post(
            "/api/assistant/drafts",
            json={"prompt": "模拟一枚火箭", "locale": "zh-CN"},
        )

    assert status.status_code == 200
    assert status.json() == {
        "configured": False,
        "available": False,
        "provider_id": None,
        "model": None,
        "health": None,
        "prompt_version": "llm-draft-session-v1",
        "capability_mode": "llm_draft_sessions",
        "automatic_execution": False,
        "confirmed_execution": True,
    }
    assert draft.status_code == 503
    assert "not configured" in draft.json()["detail"]


def test_assistant_compiles_parameter_patches_into_valid_typed_contract(tmp_path):
    provider = ScriptedProvider(
        _selected_route(),
        {
            "patches": [
                {
                    "path": "launch.heading_deg",
                    "value_json": "117.0",
                    "source_text": "航向 117 度",
                },
                {
                    "path": "launch.inclination_deg",
                    "value_json": "82.0",
                    "source_text": "发射倾角 82 度",
                },
            ],
            "assumptions": ["未指定的参数沿用已验证示例默认值。"],
            "questions": [],
        },
    )
    with _client(tmp_path, provider) as client:
        status = client.get("/api/assistant/status")
        response = client.post(
            "/api/assistant/drafts",
            json={
                "prompt": ("生成三自由度双伞回收火箭，发射倾角 82 度，航向 117 度"),
                "locale": "zh-CN",
            },
        )

    assert status.json()["available"] is True
    assert status.json()["configured"] is True
    assert status.json()["health"]["reachable"] is True
    assert response.status_code == 200
    draft = response.json()
    assert draft["status"] == "proposal"
    assert draft["family_id"] == "rocket_flight"
    assert draft["variant_id"] == "point_mass_3dof_recovery"
    assert draft["draft_document"]["dynamics"] == (
        "single_stage_point_mass_3dof_recovery"
    )
    assert draft["draft_document"]["launch"]["heading_deg"] == 117.0
    assert draft["draft_document"]["launch"]["inclination_deg"] == 82.0
    assert draft["validation"]["valid"] is True
    assert draft["validation"]["backend"]["backend_id"] == "rocketpy"
    assert draft["validation"]["family"]["variant_id"] == ("point_mass_3dof_recovery")
    assert draft["intent_ir"]["goals"] == ["生成可信仿真合同"]
    assert draft["capability_decision"]["status"] == "selected"
    assert draft["contract_synthesis"]["status"] == "synthesized"
    assert draft["provenance"]["provider_id"] == "scripted"
    assert draft["provenance"]["llm_call_count"] == 3
    assert set(draft["provenance"]["stage_latency_ms"]) == {
        "intent_interpretation",
        "capability_matching",
        "contract_synthesis",
    }
    assert [call["schema_name"] for call in provider.calls] == [
        "assistant_intent_ir",
        "assistant_capability_decision",
        "assistant_contract_synthesis",
    ]
    assert "spacecraft_gnc" in provider.calls[1]["messages"][1]["content"]
    assert "IntentIR" in provider.calls[2]["messages"][1]["content"]


def test_assistant_returns_clarification_without_compiling_a_contract(tmp_path):
    provider = ScriptedProvider(
        {
            "status": "needs_clarification",
            "family_id": None,
            "variant_id": None,
            "confidence": 0.45,
            "message": "需要确认采用三自由度还是六自由度。",
            "questions": ["是否需要模拟姿态与气动力矩？"],
            "decision_basis": ["火箭任务族中存在多个精度变体。"],
            "capability_gaps": [],
        }
    )
    with _client(tmp_path, provider) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "模拟火箭飞行", "locale": "zh-CN"},
        )

    assert response.status_code == 200
    draft = response.json()
    assert draft["status"] == "needs_clarification"
    assert draft["draft_document"] is None
    assert draft["questions"] == ["是否需要模拟姿态与气动力矩？"]
    assert draft["intent_ir"]["task_summary"]
    assert len(provider.calls) == 2


def test_assistant_reports_unsupported_request_without_fallback(tmp_path):
    provider = ScriptedProvider(
        {
            "status": "unsupported",
            "family_id": None,
            "variant_id": None,
            "confidence": 1.0,
            "message": "当前注册能力不支持多级月球着陆。",
            "questions": [],
            "decision_basis": [],
            "capability_gaps": ["未注册多级火箭与月球着陆能力。"],
        }
    )
    with _client(tmp_path, provider) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "模拟完整多级月球着陆任务", "locale": "zh-CN"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "unsupported"
    assert response.json()["draft_document"] is None
    assert response.json()["family_id"] is None


def test_assistant_rejects_locked_or_unknown_patch_paths(tmp_path):
    provider = ScriptedProvider(
        _selected_route(),
        {
            "patches": [
                {
                    "path": "dynamics",
                    "value_json": '"single_stage_point_mass_3dof"',
                    "source_text": "换成普通三自由度",
                }
            ],
            "assumptions": [],
            "questions": [],
        },
    )
    with _client(tmp_path, provider) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "生成一个回收火箭", "locale": "zh-CN"},
        )

    assert response.status_code == 422
    assert "locked or unknown field 'dynamics'" in response.json()["detail"]


def test_assistant_rejects_patches_that_fail_contract_validation(tmp_path):
    provider = ScriptedProvider(
        _selected_route(),
        {
            "patches": [
                {
                    "path": "launch.inclination_deg",
                    "value_json": "120",
                    "source_text": "倾角 120 度",
                }
            ],
            "assumptions": [],
            "questions": [],
        },
    )
    with _client(tmp_path, provider) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "回收火箭，倾角 120 度", "locale": "zh-CN"},
        )

    assert response.status_code == 422
    assert "did not compile into a valid contract" in response.json()["detail"]
    assert "inclination_deg" in response.json()["detail"]


def test_assistant_rejects_untraceable_or_nonfinite_parameter_values(tmp_path):
    untraceable = ScriptedProvider(
        _selected_route(),
        {
            "patches": [
                {
                    "path": "launch.heading_deg",
                    "value_json": "90",
                    "source_text": "用户没有说过的航向",
                }
            ],
            "assumptions": [],
            "questions": [],
        },
    )
    with _client(tmp_path / "untraceable", untraceable) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "生成一个回收火箭", "locale": "zh-CN"},
        )
    assert response.status_code == 422
    assert "not a user-request quote" in response.json()["detail"]

    nonfinite = ScriptedProvider(
        _selected_route(),
        {
            "patches": [
                {
                    "path": "launch.heading_deg",
                    "value_json": "NaN",
                    "source_text": "航向不是有限值",
                }
            ],
            "assumptions": [],
            "questions": [],
        },
    )
    with _client(tmp_path / "nonfinite", nonfinite) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "航向不是有限值", "locale": "zh-CN"},
        )
    assert response.status_code == 422
    assert "non-JSON value" in response.json()["detail"]


def test_assistant_rejects_unknown_variant_selected_by_llm(tmp_path):
    provider = ScriptedProvider(
        _selected_route(variant_id="invented_variant"),
    )
    with _client(tmp_path, provider) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "生成不存在的任务", "locale": "zh-CN"},
        )

    assert response.status_code == 422
    assert "unknown variant" in response.json()["detail"]


def test_assistant_rejects_intent_evidence_not_grounded_in_user_request(tmp_path):
    intent = _intent_ir()
    intent["explicit_requirements"] = [
        {
            "concept": "六自由度",
            "value_json": None,
            "unit": None,
            "source_text": "用户没有提出六自由度",
        }
    ]
    provider = ScriptedProvider(_selected_route(), intent_output=intent)

    with _client(tmp_path, provider) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "模拟一枚火箭", "locale": "zh-CN"},
        )

    assert response.status_code == 422
    assert "intent evidence '六自由度'" in response.json()["detail"]
    assert len(provider.calls) == 1


def test_contract_synthesizer_can_request_parameter_clarification(tmp_path):
    provider = ScriptedProvider(
        _selected_route(),
        {
            "status": "needs_clarification",
            "message": "需要明确发射倾角。",
            "patches": [],
            "assumptions": [],
            "questions": ["发射倾角是多少度？"],
            "mapped_requirements": ["三自由度双伞回收"],
            "unmapped_requirements": [],
        },
    )

    with _client(tmp_path, provider) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={"prompt": "生成三自由度双伞回收火箭", "locale": "zh-CN"},
        )

    draft = response.json()
    assert response.status_code == 200
    assert draft["status"] == "needs_clarification"
    assert draft["family_id"] == "rocket_flight"
    assert draft["questions"] == ["发射倾角是多少度？"]
    assert draft["draft_document"] is None
    assert draft["provenance"]["llm_call_count"] == 3


def test_contract_synthesizer_blocks_unmapped_requirements_without_fallback(tmp_path):
    provider = ScriptedProvider(
        _selected_route(),
        {
            "status": "unsupported",
            "message": "所选变体不能表达主动制导需求。",
            "patches": [],
            "assumptions": [],
            "questions": [],
            "mapped_requirements": ["三自由度火箭飞行"],
            "unmapped_requirements": ["主动制导"],
        },
    )

    with _client(tmp_path, provider) as client:
        response = client.post(
            "/api/assistant/drafts",
            json={
                "prompt": "生成带主动制导的三自由度回收火箭",
                "locale": "zh-CN",
            },
        )

    draft = response.json()
    assert response.status_code == 200
    assert draft["status"] == "unsupported"
    assert draft["family_id"] is None
    assert draft["draft_document"] is None
    assert draft["contract_synthesis"]["unmapped_requirements"] == ["主动制导"]
