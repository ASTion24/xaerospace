from __future__ import annotations

import asyncio
from pathlib import Path

from aerospace_simulator.assistant import (
    AssistantDraft,
    AssistantProvenance,
    AssistantProviderError,
    ContractSynthesis,
    IntentIR,
    ParameterPatchProposal,
    RouteDecision,
)
from aerospace_simulator.assistant_evaluation import (
    AssistantEvaluationCase,
    AssistantEvaluationSet,
    ExpectedPatch,
    load_evaluation_set,
    run_evaluation,
)
from aerospace_simulator.cli import build_parser, main
from aerospace_simulator.provider_config import (
    PROVIDER_CONFIG_ENV,
    PROVIDER_PROFILE_ENV,
)

PROJECT_ROOT = Path(__file__).parents[1]


class EvaluationService:
    def __init__(self, *outputs):
        self.outputs = list(outputs)

    async def status(self):
        return {
            "configured": True,
            "available": True,
            "provider_id": "evaluation-test",
            "model": "test-model",
            "health": {"detail": "healthy"},
        }

    async def draft(self, *, prompt: str, locale: str):
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def _draft(
    *,
    status="proposal",
    family_id="rocket_flight",
    variant_id="point_mass_3dof",
    patches=(),
):
    intent = IntentIR(
        task_summary="test intent",
        domain_hints=[],
        entities=[],
        goals=["test"],
        explicit_requirements=[],
        inferred_requirements=[],
        exclusions=[],
        requested_outputs=[],
        ambiguities=[],
    )
    if status == "proposal":
        decision = RouteDecision(
            status="selected",
            family_id=family_id,
            variant_id=variant_id,
            confidence=1.0,
            message="test",
            questions=[],
            decision_basis=["test"],
            capability_gaps=[],
        )
    else:
        decision = RouteDecision(
            status=status,
            family_id=None,
            variant_id=None,
            confidence=1.0,
            message="test",
            questions=["test"] if status == "needs_clarification" else [],
            decision_basis=[],
            capability_gaps=["test"] if status == "unsupported" else [],
        )
    patch_models = [
        ParameterPatchProposal(
            path=path,
            value_json=value_json,
            source_text="test",
        )
        for path, value_json in patches
    ]
    return AssistantDraft(
        status=status,
        message="test",
        family_id=family_id,
        variant_id=variant_id,
        confidence=1.0,
        questions=[],
        assumptions=[],
        patches=patch_models,
        draft_document={} if status == "proposal" else None,
        validation=None,
        intent_ir=intent,
        capability_decision=decision,
        contract_synthesis=(
            ContractSynthesis(
                status="synthesized",
                message="test",
                patches=patch_models,
                assumptions=[],
                questions=[],
                mapped_requirements=["test"],
                unmapped_requirements=[],
            )
            if status == "proposal"
            else None
        ),
        provenance=AssistantProvenance(
            draft_id="test",
            provider_id="evaluation-test",
            model="test-model",
            prompt_version="test",
            latency_ms=1.0,
            stage_latency_ms={"test": 1.0},
            llm_call_count=1,
        ),
    )


def test_default_evaluation_set_covers_every_registered_variant():
    evaluation_set = load_evaluation_set(
        PROJECT_ROOT / "evaluation" / "assistant_cases.json"
    )

    proposal_variants = {
        case.expected_variant_id
        for case in evaluation_set.cases
        if case.expected_status == "proposal"
    }
    assert len(evaluation_set.cases) == 26
    assert len(proposal_variants) == 16
    assert {case.expected_status for case in evaluation_set.cases} == {
        "proposal",
        "needs_clarification",
        "unsupported",
    }
    assert any("adversarial" in case.tags for case in evaluation_set.cases)
    assert any("unit_conversion" in case.tags for case in evaluation_set.cases)


def test_assistant_evaluation_cli_is_explicitly_provider_gated(
    monkeypatch,
    capsys,
):
    parsed = build_parser().parse_args(
        [
            "assistant-eval",
            "--tag",
            "routing",
            "--limit",
            "2",
        ]
    )
    assert parsed.command == "assistant-eval"
    assert parsed.tags == ["routing"]
    monkeypatch.delenv("XAEROSPACE_ASSISTANT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("XAEROSPACE_ASSISTANT_LLM_MODEL", raising=False)
    monkeypatch.delenv(PROVIDER_CONFIG_ENV, raising=False)
    monkeypatch.delenv(PROVIDER_PROFILE_ENV, raising=False)
    monkeypatch.setattr(
        "aerospace_simulator.cli.discover_local_provider_config",
        lambda **_: None,
    )

    result = main(["assistant-eval", "--limit", "1"])

    assert result == 2
    assert "not configured" in capsys.readouterr().err


def test_evaluation_scores_routes_patches_errors_and_false_support():
    cases = AssistantEvaluationSet(
        schema_version=1,
        name="unit-test",
        cases=[
            AssistantEvaluationCase(
                case_id="proposal",
                locale="en",
                prompt="test",
                expected_status="proposal",
                expected_family_id="rocket_flight",
                expected_variant_id="point_mass_3dof",
                expected_patches=[
                    ExpectedPatch(
                        path="launch.heading_deg",
                        value=117.0,
                    )
                ],
                tags=["routing", "parameters"],
            ),
            AssistantEvaluationCase(
                case_id="unsupported",
                locale="en",
                prompt="test",
                expected_status="unsupported",
                tags=["unsupported"],
            ),
            AssistantEvaluationCase(
                case_id="error",
                locale="en",
                prompt="test",
                expected_status="unsupported",
                tags=["provider"],
            ),
            AssistantEvaluationCase(
                case_id="false-support",
                locale="en",
                prompt="test",
                expected_status="unsupported",
                tags=["unsupported"],
            ),
        ],
    )
    service = EvaluationService(
        _draft(patches=(("launch.heading_deg", "117"),)),
        _draft(
            status="unsupported",
            family_id=None,
            variant_id=None,
        ),
        AssistantProviderError("unavailable"),
        _draft(),
    )

    report = asyncio.run(
        run_evaluation(
            service=service,
            evaluation_set=cases,
        )
    )

    assert report.summary.total_cases == 4
    assert report.summary.passed_cases == 2
    assert report.summary.error_count == 1
    assert report.summary.false_support_count == 1
    assert report.summary.proposal_route_accuracy == 1.0
    assert report.summary.proposal_patch_accuracy == 1.0
    assert report.summary.by_tag["unsupported"]["passed"] == 1
