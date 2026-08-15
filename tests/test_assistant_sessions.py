from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import TypeVar

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from aerospace_simulator.assistant import (
    AssistantProviderError,
    ProviderHealth,
    StructuredLLMProvider,
)
from aerospace_simulator.assistant_sessions import (
    DraftSessionConflictError,
    DraftSessionManager,
    DraftSessionNotFoundError,
)
from aerospace_simulator.web_api import create_app
from aerospace_simulator.workflows import WorkflowStore

PROJECT_ROOT = Path(__file__).parents[1]
SCENARIO_DIR = PROJECT_ROOT / "scenarios"
WEB_DIR = PROJECT_ROOT / "web"
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class SessionProvider(StructuredLLMProvider):
    def __init__(
        self,
        *,
        intents: Sequence[Mapping[str, object] | Exception],
        routes: Sequence[Mapping[str, object] | Exception],
        syntheses: Sequence[Mapping[str, object] | Exception] = (),
    ) -> None:
        self._outputs = {
            "assistant_intent_ir": list(intents),
            "assistant_capability_decision": list(routes),
            "assistant_contract_synthesis": list(syntheses),
        }
        self.calls: list[dict[str, object]] = []

    @property
    def provider_id(self) -> str:
        return "session-scripted"

    @property
    def model_id(self) -> str:
        return "session-test-model"

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
        outputs = self._outputs[schema_name]
        if not outputs:
            raise AssertionError(f"unexpected provider call: {schema_name}")
        output = outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return response_model.model_validate(output)

    async def health(self, *, force: bool = False) -> ProviderHealth:
        return ProviderHealth(
            available=True,
            reachable=True,
            model_available=True,
            latency_ms=0.1,
            detail="session provider is healthy",
        )

    async def aclose(self) -> None:
        return None


class BlockingSessionProvider(SessionProvider):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._intent_calls = 0

    async def complete(
        self,
        *,
        schema_name: str,
        response_model: type[ResponseModel],
        messages: Sequence[Mapping[str, str]],
    ) -> ResponseModel:
        if schema_name == "assistant_intent_ir":
            self._intent_calls += 1
            if self._intent_calls == 2:
                self.started.set()
                await self.release.wait()
        return await super().complete(
            schema_name=schema_name,
            response_model=response_model,
            messages=messages,
        )


def _client(tmp_path, provider):
    app = create_app(
        workflow_store=WorkflowStore(tmp_path / "runs"),
        scenarios_directory=SCENARIO_DIR,
        web_directory=WEB_DIR,
        assistant_provider=provider,
    )
    return TestClient(app)


def _wait_for_workflow(
    client: TestClient,
    workflow_id: str,
) -> dict[str, object]:
    workflow: dict[str, object] = {}
    for _ in range(300):
        workflow = client.get(f"/api/workflows/{workflow_id}").json()
        if workflow["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert workflow["status"] in {"completed", "failed"}
    return workflow


def _intent(summary: str) -> dict[str, object]:
    return {
        "task_summary": summary,
        "domain_hints": ["火箭飞行"],
        "entities": ["火箭"],
        "goals": [summary],
        "explicit_requirements": [],
        "inferred_requirements": [],
        "exclusions": [],
        "requested_outputs": [],
        "ambiguities": [],
    }


def _selected_route(variant_id: str) -> dict[str, object]:
    return {
        "status": "selected",
        "family_id": "rocket_flight",
        "variant_id": variant_id,
        "confidence": 0.99,
        "message": "已选择完整匹配的火箭任务变体。",
        "questions": [],
        "decision_basis": ["当前意图与所选变体的动力学能力一致。"],
        "capability_gaps": [],
    }


def _clarification_route() -> dict[str, object]:
    return {
        "status": "needs_clarification",
        "family_id": None,
        "variant_id": None,
        "confidence": 0.5,
        "message": "需要明确火箭动力学精度。",
        "questions": ["需要三自由度还是六自由度？"],
        "decision_basis": ["当前描述未明确是否需要姿态动力学。"],
        "capability_gaps": [],
    }


def _synthesis(
    *,
    patches: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    return {
        "status": "synthesized",
        "message": "当前完整意图已编译为合同草案。",
        "patches": list(patches),
        "assumptions": [],
        "questions": [],
        "mapped_requirements": ["当前完整用户意图"],
        "unmapped_requirements": [],
    }


def test_draft_session_resolves_clarification_into_a_valid_contract(tmp_path):
    provider = SessionProvider(
        intents=[
            _intent("火箭飞行仿真"),
            _intent("六自由度火箭飞行仿真"),
        ],
        routes=[
            _clarification_route(),
            _selected_route("rigid_body_6dof"),
        ],
        syntheses=[_synthesis()],
    )

    with _client(tmp_path, provider) as client:
        created = client.post(
            "/api/assistant/sessions",
            json={"prompt": "模拟火箭飞行", "locale": "zh-CN"},
        )
        session = created.json()
        continued = client.post(
            f"/api/assistant/sessions/{session['session_id']}/turns",
            json={
                "message": "需要六自由度，包含姿态动力学",
                "expected_revision": session["revision"],
            },
        )
        fetched = client.get(f"/api/assistant/sessions/{session['session_id']}")

    assert created.status_code == 201
    assert session["status"] == "needs_clarification"
    assert session["revision"] == 1
    assert len(session["turns"]) == 2
    assert continued.status_code == 200
    revised = continued.json()
    assert revised["revision"] == 2
    assert revised["status"] == "proposal"
    assert revised["draft"]["variant_id"] == "rigid_body_6dof"
    assert revised["draft"]["draft_document"]["dynamics"] == (
        "single_stage_rigid_body_6dof"
    )
    assert len(revised["turns"]) == 4
    assert fetched.json() == revised
    second_intent_call = [
        call for call in provider.calls if call["schema_name"] == "assistant_intent_ir"
    ][1]
    prompt = second_intent_call["messages"][1]["content"]
    assert "模拟火箭飞行" in prompt
    assert "需要六自由度，包含姿态动力学" in prompt
    assert "Previous compiler state" in prompt


def test_draft_session_rebuilds_active_patches_after_user_modification(tmp_path):
    provider = SessionProvider(
        intents=[
            _intent("三自由度双伞回收火箭，航向 90 度"),
            _intent("三自由度双伞回收火箭，航向改为 117 度"),
        ],
        routes=[
            _selected_route("point_mass_3dof_recovery"),
            _selected_route("point_mass_3dof_recovery"),
        ],
        syntheses=[
            _synthesis(
                patches=[
                    {
                        "path": "launch.heading_deg",
                        "value_json": "90",
                        "source_text": "航向 90 度",
                    }
                ]
            ),
            _synthesis(
                patches=[
                    {
                        "path": "launch.heading_deg",
                        "value_json": "117",
                        "source_text": "航向改为 117 度",
                    }
                ]
            ),
        ],
    )

    with _client(tmp_path, provider) as client:
        created = client.post(
            "/api/assistant/sessions",
            json={
                "prompt": "生成三自由度双伞回收火箭，航向 90 度",
                "locale": "zh-CN",
            },
        ).json()
        revised_response = client.post(
            f"/api/assistant/sessions/{created['session_id']}/turns",
            json={
                "message": "航向改为 117 度",
                "expected_revision": 1,
            },
        )

    revised = revised_response.json()
    assert revised_response.status_code == 200
    assert created["draft"]["draft_document"]["launch"]["heading_deg"] == 90
    assert revised["draft"]["draft_document"]["launch"]["heading_deg"] == 117
    assert revised["draft"]["patches"] == [
        {
            "path": "launch.heading_deg",
            "value_json": "117",
            "source_text": "航向改为 117 度",
        }
    ]
    assert revised["draft"]["draft_document"]["description"] == (
        "生成三自由度双伞回收火箭，航向 90 度\n航向改为 117 度"
    )


def test_draft_session_rejects_stale_revisions_and_supports_explicit_delete(
    tmp_path,
):
    provider = SessionProvider(
        intents=[_intent("三自由度火箭飞行")],
        routes=[_selected_route("point_mass_3dof")],
        syntheses=[_synthesis()],
    )

    with _client(tmp_path, provider) as client:
        created = client.post(
            "/api/assistant/sessions",
            json={"prompt": "生成三自由度火箭", "locale": "zh-CN"},
        ).json()
        call_count = len(provider.calls)
        stale = client.post(
            f"/api/assistant/sessions/{created['session_id']}/turns",
            json={"message": "航向改为 117 度", "expected_revision": 9},
        )
        stale_delete = client.delete(
            f"/api/assistant/sessions/{created['session_id']}",
            params={"expected_revision": 9},
        )
        deleted = client.delete(
            f"/api/assistant/sessions/{created['session_id']}",
            params={"expected_revision": 1},
        )
        missing = client.get(f"/api/assistant/sessions/{created['session_id']}")

    assert stale.status_code == 409
    assert "revision is 1, not 9" in stale.json()["detail"]
    assert stale_delete.status_code == 409
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert len(provider.calls) == call_count


def test_failed_session_turn_releases_busy_state_without_advancing_revision(
    tmp_path,
):
    provider = SessionProvider(
        intents=[
            _intent("三自由度火箭飞行"),
            AssistantProviderError("planned provider failure"),
        ],
        routes=[_selected_route("point_mass_3dof")],
        syntheses=[_synthesis()],
    )

    with _client(tmp_path, provider) as client:
        created = client.post(
            "/api/assistant/sessions",
            json={"prompt": "生成三自由度火箭", "locale": "zh-CN"},
        ).json()
        failed = client.post(
            f"/api/assistant/sessions/{created['session_id']}/turns",
            json={"message": "航向改为 117 度", "expected_revision": 1},
        )
        fetched = client.get(f"/api/assistant/sessions/{created['session_id']}").json()

    assert failed.status_code == 502
    assert fetched["busy"] is False
    assert fetched["revision"] == 1
    assert len(fetched["turns"]) == 2


def test_confirmed_assistant_launch_reaches_verified_orbit_with_tudatpy(
    tmp_path,
):
    prompt = "将 15000 kg 有效载荷通过两级火箭送入 220 km 近圆轨道。"
    provider = SessionProvider(
        intents=[
            {
                "task_summary": "使用两级运载火箭将有效载荷送入近圆轨道。",
                "domain_hints": ["运载火箭", "轨道注入"],
                "entities": ["两级火箭", "有效载荷", "近圆轨道"],
                "goals": ["完成从发射到轨道注入的真实仿真"],
                "explicit_requirements": [
                    {
                        "concept": "有效载荷质量",
                        "value_json": "15000",
                        "unit": "kg",
                        "source_text": "15000 kg 有效载荷",
                    },
                    {
                        "concept": "目标轨道高度",
                        "value_json": "220",
                        "unit": "km",
                        "source_text": "220 km 近圆轨道",
                    },
                ],
                "inferred_requirements": [],
                "exclusions": [],
                "requested_outputs": ["入轨验收证据"],
                "ambiguities": [],
            }
        ],
        routes=[
            {
                "status": "selected",
                "family_id": "launch_to_orbit",
                "variant_id": "two_stage_220km_reference",
                "confidence": 0.99,
                "message": "已选择两级运载火箭发射入轨任务。",
                "questions": [],
                "decision_basis": ["该变体覆盖两级动力上升、分级和近圆轨道验收。"],
                "capability_gaps": [],
            }
        ],
        syntheses=[
            {
                "status": "synthesized",
                "message": "已生成两级火箭发射入轨合同。",
                "patches": [
                    {
                        "path": "vehicle.payload_mass_kg",
                        "value_json": "15000",
                        "source_text": "15000 kg 有效载荷",
                    },
                    {
                        "path": "target_orbit.altitude_m",
                        "value_json": "220000",
                        "source_text": "220 km 近圆轨道",
                    },
                ],
                "assumptions": [],
                "questions": [],
                "mapped_requirements": [
                    "15000 kg 有效载荷",
                    "220 km 近圆轨道",
                ],
                "unmapped_requirements": [],
            }
        ],
    )

    with _client(tmp_path, provider) as client:
        created_response = client.post(
            "/api/assistant/sessions",
            json={"prompt": prompt, "locale": "zh-CN"},
        )
        created = created_response.json()
        execution_url = f"/api/assistant/sessions/{created['session_id']}/executions"
        unconfirmed = client.post(
            execution_url,
            json={"expected_revision": 1, "confirmed": False},
        )
        stale = client.post(
            execution_url,
            json={"expected_revision": 2, "confirmed": True},
        )
        submitted_response = client.post(
            execution_url,
            json={"expected_revision": 1, "confirmed": True},
        )
        submitted = submitted_response.json()
        workflow = submitted["workflow"]
        for _ in range(300):
            workflow = client.get(f"/api/workflows/{workflow['workflow_id']}").json()
            if workflow["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        duplicate = client.post(
            execution_url,
            json={"expected_revision": 1, "confirmed": True},
        )

    task = workflow["tasks"][0]
    metrics = {metric["name"]: metric["value"] for metric in task["summary"]["metrics"]}
    diagnostic_codes = {
        diagnostic["code"] for diagnostic in task["summary"]["diagnostics"]
    }
    provenance_paths = list(
        (tmp_path / "runs" / workflow["workflow_id"]).rglob("assistant_provenance.json")
    )
    assert len(provenance_paths) == 1
    provenance_document = json.loads(provenance_paths[0].read_text(encoding="utf-8"))

    assert created_response.status_code == 201
    assert created["status"] == "proposal"
    assert created["draft"]["family_id"] == "launch_to_orbit"
    assert created["draft"]["variant_id"] == "two_stage_220km_reference"
    assert unconfirmed.status_code == 422
    assert stale.status_code == 409
    assert submitted_response.status_code == 202
    assert submitted["session"]["execution"]["workflow_id"] == (workflow["workflow_id"])
    assert workflow["status"] == "completed"
    assert workflow["provenance"] == {
        "origin": "assistant_confirmed",
        "assistant_session_id": created["session_id"],
        "assistant_revision": 1,
        "assistant_draft_id": created["draft"]["provenance"]["draft_id"],
        "family_id": "launch_to_orbit",
        "variant_id": "two_stage_220km_reference",
        "provider_id": "session-scripted",
        "model": "session-test-model",
        "prompt_version": "llm-draft-session-v1",
    }
    assert task["backend"]["backend_id"] == "tudatpy"
    assert metrics["insertion_periapsis_altitude"] > 180_000.0
    assert metrics["insertion_apoapsis_altitude"] > 180_000.0
    assert metrics["insertion_eccentricity"] < 0.005
    assert abs(metrics["mass_balance_error"]) < 1e-6
    assert "target_orbit_verified" in diagnostic_codes
    assert {artifact["name"] for artifact in task["artifacts"]} >= {
        "assistant_provenance",
        "launch_profile",
        "orbit_profile",
        "model_manifest",
        "result",
    }
    assert provenance_document["schema"] == ("xaerospace.assistant_provenance.v1")
    assert provenance_document["workflow_id"] == workflow["workflow_id"]
    assert provenance_document["task_id"] == task["task_id"]
    assert provenance_document["provenance"] == workflow["provenance"]
    assert len(provenance_document["request_sha256"]) == 64
    assert duplicate.status_code == 409
    assert [call["schema_name"] for call in provider.calls] == [
        "assistant_intent_ir",
        "assistant_capability_decision",
        "assistant_contract_synthesis",
    ]


@pytest.mark.parametrize(
    (
        "family_id",
        "variant_id",
        "prompt",
        "backend_id",
        "metric_name",
        "comparison",
        "threshold",
    ),
    (
        (
            "rocket_flight",
            "point_mass_3dof",
            "模拟一枚无降落伞的质点三自由度火箭完整飞行。",
            "rocketpy",
            "apogee_agl",
            "greater",
            500.0,
        ),
        (
            "orbit_propagation",
            "earth_two_body",
            "使用地球点质量引力传播一条无摄动二体轨道。",
            "tudatpy",
            "max_relative_specific_energy_drift",
            "absolute_less",
            1e-8,
        ),
        (
            "aircraft_flight",
            "c172p_trimmed_6dof",
            "使用 JSBSim 模拟 Cessna 172P 配平后施加副翼脉冲的六自由度响应。",
            "jsbsim",
            "maximum_roll",
            "greater",
            5.0,
        ),
        (
            "spacecraft_gnc",
            "inertial_pointing_rw",
            "使用反作用轮和 MRP 反馈完成航天器惯性姿态指向。",
            "basilisk",
            "final_attitude_error_norm",
            "absolute_less",
            1e-3,
        ),
    ),
    ids=("rocket", "orbit", "aircraft", "spacecraft"),
)
def test_confirmed_assistant_golden_paths_execute_real_family_backend(
    tmp_path,
    family_id,
    variant_id,
    prompt,
    backend_id,
    metric_name,
    comparison,
    threshold,
):
    provider = SessionProvider(
        intents=[
            {
                "task_summary": prompt,
                "domain_hints": [family_id],
                "entities": [],
                "goals": [prompt],
                "explicit_requirements": [],
                "inferred_requirements": [],
                "exclusions": [],
                "requested_outputs": ["物理仿真结果"],
                "ambiguities": [],
            }
        ],
        routes=[
            {
                "status": "selected",
                "family_id": family_id,
                "variant_id": variant_id,
                "confidence": 0.99,
                "message": "已选择完整覆盖当前需求的任务变体。",
                "questions": [],
                "decision_basis": ["所选任务变体完整覆盖用户的一句话需求。"],
                "capability_gaps": [],
            }
        ],
        syntheses=[_synthesis()],
    )

    with _client(tmp_path, provider) as client:
        created_response = client.post(
            "/api/assistant/sessions",
            json={"prompt": prompt, "locale": "zh-CN"},
        )
        created = created_response.json()
        submitted_response = client.post(
            f"/api/assistant/sessions/{created['session_id']}/executions",
            json={"expected_revision": 1, "confirmed": True},
        )
        workflow = _wait_for_workflow(
            client,
            submitted_response.json()["workflow"]["workflow_id"],
        )

    task = workflow["tasks"][0]
    metrics = {metric["name"]: metric["value"] for metric in task["summary"]["metrics"]}
    artifact_names = {artifact["name"] for artifact in task["artifacts"]}

    assert created_response.status_code == 201
    assert created["draft"]["family_id"] == family_id
    assert created["draft"]["variant_id"] == variant_id
    assert submitted_response.status_code == 202
    assert workflow["status"] == "completed"
    assert workflow["provenance"]["origin"] == "assistant_confirmed"
    assert workflow["provenance"]["family_id"] == family_id
    assert workflow["provenance"]["variant_id"] == variant_id
    assert task["backend"]["backend_id"] == backend_id
    assert "assistant_provenance" in artifact_names
    if comparison == "greater":
        assert metrics[metric_name] > threshold
    else:
        assert abs(metrics[metric_name]) < threshold
    assert [call["schema_name"] for call in provider.calls] == [
        "assistant_intent_ir",
        "assistant_capability_decision",
        "assistant_contract_synthesis",
    ]


def test_draft_session_rejects_a_concurrent_turn_while_compilation_is_active(
    tmp_path,
):
    async def exercise():
        provider = BlockingSessionProvider(
            intents=[
                _intent("三自由度火箭飞行"),
                _intent("三自由度火箭航向修改"),
            ],
            routes=[
                _selected_route("point_mass_3dof"),
                _selected_route("point_mass_3dof"),
            ],
            syntheses=[_synthesis(), _synthesis()],
        )
        store = WorkflowStore(tmp_path / "concurrent-runs")
        app = create_app(
            workflow_store=store,
            scenarios_directory=SCENARIO_DIR,
            web_directory=WEB_DIR,
            assistant_provider=provider,
        )
        manager = app.state.assistant_sessions
        created = await manager.create(
            prompt="生成三自由度火箭",
            locale="zh-CN",
        )
        active_turn = asyncio.create_task(
            manager.continue_session(
                created.session_id,
                message="航向改为 117 度",
                expected_revision=1,
            )
        )
        await provider.started.wait()
        busy = await manager.get(created.session_id)
        with pytest.raises(DraftSessionConflictError, match="already compiling"):
            await manager.continue_session(
                created.session_id,
                message="倾角改为 82 度",
                expected_revision=1,
            )
        provider.release.set()
        revised = await active_turn
        await app.state.assistant_service.aclose()
        store.close()
        return busy, revised

    busy, revised = asyncio.run(exercise())
    assert busy.busy is True
    assert busy.revision == 1
    assert revised.busy is False
    assert revised.revision == 2


def test_inactive_draft_sessions_expire(tmp_path):
    async def exercise():
        provider = SessionProvider(
            intents=[_intent("三自由度火箭飞行")],
            routes=[_selected_route("point_mass_3dof")],
            syntheses=[_synthesis()],
        )
        store = WorkflowStore(tmp_path / "expiring-runs")
        app = create_app(
            workflow_store=store,
            scenarios_directory=SCENARIO_DIR,
            web_directory=WEB_DIR,
            assistant_provider=provider,
        )
        manager = DraftSessionManager(
            app.state.assistant_service,
            ttl=timedelta(milliseconds=1),
        )
        created = await manager.create(
            prompt="生成三自由度火箭",
            locale="zh-CN",
        )
        await asyncio.sleep(0.01)
        with pytest.raises(DraftSessionNotFoundError, match="expired"):
            await manager.get(created.session_id)
        await app.state.assistant_service.aclose()
        store.close()

    asyncio.run(exercise())
