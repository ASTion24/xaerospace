from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .assistant import (
    ASSISTANT_PROMPT_VERSION,
    AssistantDraft,
    AssistantError,
    AssistantService,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedPatch(_StrictModel):
    path: str = Field(min_length=1)
    value: Any


class AssistantEvaluationCase(_StrictModel):
    case_id: str = Field(min_length=1)
    locale: Literal["zh-CN", "en"]
    prompt: str = Field(min_length=1)
    expected_status: Literal[
        "proposal",
        "needs_clarification",
        "unsupported",
    ]
    expected_family_id: str | None = None
    expected_variant_id: str | None = None
    expected_patches: list[ExpectedPatch] = Field(default_factory=list)
    forbidden_patch_paths: list[str] = Field(default_factory=list)
    allow_additional_patches: bool = False
    tags: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_expected_route(self) -> AssistantEvaluationCase:
        if self.expected_status == "proposal" and (
            not self.expected_family_id or not self.expected_variant_id
        ):
            raise ValueError("proposal cases require expected family and variant ids")
        if self.expected_status != "proposal" and (
            self.expected_family_id is not None
            or self.expected_variant_id is not None
            or self.expected_patches
        ):
            raise ValueError("non-proposal cases must not declare a route or patches")
        return self


class AssistantEvaluationSet(_StrictModel):
    schema_version: Literal[1]
    name: str
    cases: list[AssistantEvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> AssistantEvaluationSet:
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("evaluation case ids must be unique")
        return self


class AssistantCaseResult(_StrictModel):
    case_id: str
    passed: bool
    failures: list[str]
    expected_status: str
    actual_status: str | None
    expected_family_id: str | None
    actual_family_id: str | None
    expected_variant_id: str | None
    actual_variant_id: str | None
    expected_patches: dict[str, Any]
    actual_patches: dict[str, Any]
    false_support: bool
    latency_ms: float
    intent_summary: str | None
    inferred_requirements: list[str]
    stage_latency_ms: dict[str, float]
    error_type: str | None
    error: str | None
    tags: list[str]


class EvaluationSummary(_StrictModel):
    total_cases: int
    passed_cases: int
    pass_rate: float
    status_accuracy: float
    proposal_route_accuracy: float
    proposal_patch_accuracy: float
    false_support_count: int
    error_count: int
    errors_by_type: dict[str, int]
    latency_p50_ms: float
    latency_p95_ms: float
    stage_latency_mean_ms: dict[str, float]
    by_tag: dict[str, dict[str, float | int]]


class AssistantEvaluationReport(_StrictModel):
    evaluation_id: str
    evaluation_set: str
    started_at: str
    completed_at: str
    provider_id: str | None
    model: str | None
    prompt_version: str
    summary: EvaluationSummary
    cases: list[AssistantCaseResult]


def load_evaluation_set(path: str | Path) -> AssistantEvaluationSet:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to load evaluation set {source}: {exc}") from exc
    return AssistantEvaluationSet.model_validate(payload)


async def run_evaluation(
    *,
    service: AssistantService,
    evaluation_set: AssistantEvaluationSet,
    concurrency: int = 1,
    limit: int | None = None,
    tags: set[str] | None = None,
    on_result: (Callable[[AssistantCaseResult], Awaitable[None]] | None) = None,
) -> AssistantEvaluationReport:
    if not 1 <= concurrency <= 8:
        raise ValueError("evaluation concurrency must be in [1, 8]")
    selected = [
        case
        for case in evaluation_set.cases
        if not tags or tags.intersection(case.tags)
    ]
    if limit is not None:
        if limit < 1:
            raise ValueError("evaluation limit must be positive")
        selected = selected[:limit]
    if not selected:
        raise ValueError("evaluation selection contains no cases")

    status = await service.status()
    if not status["configured"]:
        raise ValueError("assistant provider is not configured")
    if not status["available"]:
        health = status.get("health")
        detail = health.get("detail") if isinstance(health, dict) else "unknown"
        raise ValueError(f"assistant provider is unavailable: {detail}")

    started_at = datetime.now(timezone.utc)
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate(case: AssistantEvaluationCase) -> AssistantCaseResult:
        async with semaphore:
            result = await _evaluate_case(service, case)
            if on_result is not None:
                await on_result(result)
            return result

    case_results = await asyncio.gather(*(evaluate(case) for case in selected))
    completed_at = datetime.now(timezone.utc)
    return AssistantEvaluationReport(
        evaluation_id=uuid4().hex,
        evaluation_set=evaluation_set.name,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        provider_id=(
            str(status["provider_id"])
            if status.get("provider_id") is not None
            else None
        ),
        model=(str(status["model"]) if status.get("model") is not None else None),
        prompt_version=ASSISTANT_PROMPT_VERSION,
        summary=_summarize(case_results),
        cases=case_results,
    )


async def _evaluate_case(
    service: AssistantService,
    case: AssistantEvaluationCase,
) -> AssistantCaseResult:
    started = time.perf_counter()
    try:
        draft = await service.draft(
            prompt=case.prompt,
            locale=case.locale,
        )
    except AssistantError as exc:
        return AssistantCaseResult(
            case_id=case.case_id,
            passed=False,
            failures=["assistant request raised an error"],
            expected_status=case.expected_status,
            actual_status=None,
            expected_family_id=case.expected_family_id,
            actual_family_id=None,
            expected_variant_id=case.expected_variant_id,
            actual_variant_id=None,
            expected_patches={
                patch.path: patch.value for patch in case.expected_patches
            },
            actual_patches={},
            false_support=False,
            latency_ms=(time.perf_counter() - started) * 1_000.0,
            intent_summary=None,
            inferred_requirements=[],
            stage_latency_ms={},
            error_type=type(exc).__name__,
            error=f"{type(exc).__name__}: {exc}",
            tags=case.tags,
        )
    return _score_draft(
        case,
        draft,
        latency_ms=(time.perf_counter() - started) * 1_000.0,
    )


def _score_draft(
    case: AssistantEvaluationCase,
    draft: AssistantDraft,
    *,
    latency_ms: float,
) -> AssistantCaseResult:
    failures: list[str] = []
    if draft.status != case.expected_status:
        failures.append(
            f"status expected {case.expected_status!r}, got {draft.status!r}"
        )
    if (
        case.expected_family_id is not None
        and draft.family_id != case.expected_family_id
    ):
        failures.append(
            f"family expected {case.expected_family_id!r}, got {draft.family_id!r}"
        )
    if (
        case.expected_variant_id is not None
        and draft.variant_id != case.expected_variant_id
    ):
        failures.append(
            f"variant expected {case.expected_variant_id!r}, got {draft.variant_id!r}"
        )

    expected_patches = {patch.path: patch.value for patch in case.expected_patches}
    actual_patches: dict[str, Any] = {}
    for patch in draft.patches:
        try:
            actual_patches[patch.path] = json.loads(patch.value_json)
        except json.JSONDecodeError:
            failures.append(f"patch {patch.path!r} is not valid JSON")
    for path, expected in expected_patches.items():
        if path not in actual_patches:
            failures.append(f"missing expected patch {path!r}")
        elif not _values_equal(actual_patches[path], expected):
            failures.append(
                f"patch {path!r} expected {expected!r}, got {actual_patches[path]!r}"
            )
    if not case.allow_additional_patches:
        unexpected = set(actual_patches) - set(expected_patches)
        if unexpected:
            failures.append("unexpected patches: " + ", ".join(sorted(unexpected)))
    forbidden = set(case.forbidden_patch_paths).intersection(actual_patches)
    if forbidden:
        failures.append("forbidden patches: " + ", ".join(sorted(forbidden)))

    false_support = (
        case.expected_status in {"needs_clarification", "unsupported"}
        and draft.status == "proposal"
    )
    return AssistantCaseResult(
        case_id=case.case_id,
        passed=not failures,
        failures=failures,
        expected_status=case.expected_status,
        actual_status=draft.status,
        expected_family_id=case.expected_family_id,
        actual_family_id=draft.family_id,
        expected_variant_id=case.expected_variant_id,
        actual_variant_id=draft.variant_id,
        expected_patches=expected_patches,
        actual_patches=actual_patches,
        false_support=false_support,
        latency_ms=latency_ms,
        intent_summary=draft.intent_ir.task_summary,
        inferred_requirements=[
            item.concept for item in draft.intent_ir.inferred_requirements
        ],
        stage_latency_ms=draft.provenance.stage_latency_ms,
        error_type=None,
        error=None,
        tags=case.tags,
    )


def _values_equal(actual: Any, expected: Any) -> bool:
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=1e-6,
            abs_tol=1e-9,
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _values_equal(left, right)
            for left, right in zip(actual, expected, strict=True)
        )
    if isinstance(actual, dict) and isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _values_equal(actual[key], expected[key]) for key in actual
        )
    return actual == expected


def _summarize(results: list[AssistantCaseResult]) -> EvaluationSummary:
    total = len(results)
    passed = sum(result.passed for result in results)
    status_correct = sum(
        result.actual_status == result.expected_status for result in results
    )
    proposal_results = [
        result for result in results if result.expected_status == "proposal"
    ]
    route_correct = sum(
        result.actual_family_id == result.expected_family_id
        and result.actual_variant_id == result.expected_variant_id
        for result in proposal_results
    )
    patch_correct = sum(
        result.actual_patches == result.expected_patches for result in proposal_results
    )
    latencies = sorted(result.latency_ms for result in results)
    tags = sorted({tag for result in results for tag in result.tags})
    error_types = sorted(
        {result.error_type for result in results if result.error_type is not None}
    )
    stage_names = sorted(
        {stage for result in results for stage in result.stage_latency_ms}
    )
    by_tag: dict[str, dict[str, float | int]] = {}
    for tag in tags:
        tagged = [result for result in results if tag in result.tags]
        tagged_passed = sum(result.passed for result in tagged)
        by_tag[tag] = {
            "total": len(tagged),
            "passed": tagged_passed,
            "pass_rate": tagged_passed / len(tagged),
        }
    return EvaluationSummary(
        total_cases=total,
        passed_cases=passed,
        pass_rate=passed / total,
        status_accuracy=status_correct / total,
        proposal_route_accuracy=(
            route_correct / len(proposal_results) if proposal_results else 1.0
        ),
        proposal_patch_accuracy=(
            patch_correct / len(proposal_results) if proposal_results else 1.0
        ),
        false_support_count=sum(result.false_support for result in results),
        error_count=sum(result.error is not None for result in results),
        errors_by_type={
            error_type: sum(result.error_type == error_type for result in results)
            for error_type in error_types
        },
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        stage_latency_mean_ms={
            stage: sum(
                result.stage_latency_ms[stage]
                for result in results
                if stage in result.stage_latency_ms
            )
            / sum(stage in result.stage_latency_ms for result in results)
            for stage in stage_names
        },
        by_tag=by_tag,
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = math.ceil(quantile * len(values)) - 1
    return values[max(0, min(index, len(values) - 1))]
