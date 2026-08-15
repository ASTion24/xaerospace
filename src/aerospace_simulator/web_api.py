from __future__ import annotations

import json
import sysconfig
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ._version import __version__
from .assistant import (
    AssistantCompileError,
    AssistantOutputError,
    AssistantProviderError,
    AssistantService,
    AssistantUnavailableError,
    StructuredLLMProvider,
    provider_from_configuration,
)
from .assistant_sessions import (
    DraftSessionCapacityError,
    DraftSessionConflictError,
    DraftSessionManager,
    DraftSessionNotFoundError,
)
from .config import ScenarioValidationError
from .parameter_definitions import parameter_catalog
from .paths import default_runs_root, source_project_root
from .protocol import PROTOCOL_VERSION, ProtocolValidationError
from .registry import BackendRegistry, BackendRegistryError
from .request_io import request_from_document
from .simulation import create_default_registry
from .task_families import (
    TaskFamilyNotFoundError,
    TaskFamilyRegistry,
    create_default_task_family_registry,
)
from .workflows import (
    MAX_WORKFLOW_TASKS,
    WorkflowArtifactNotFoundError,
    WorkflowConflictError,
    WorkflowNotFoundError,
    WorkflowStore,
    WorkflowValidationError,
)


class ValidateRequestPayload(BaseModel):
    document: dict[str, Any]


class AssistantDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4_000)
    locale: Literal["zh-CN", "en"] = "zh-CN"


class AssistantSessionTurnPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4_000)
    expected_revision: int = Field(ge=1)


class AssistantExecutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    confirmed: Literal[True]


class WorkflowHandoverPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    type: Literal["rocketpy_to_tudatpy"]
    source_task_id: str = Field(min_length=1, max_length=64)
    source_event: Literal["burnout", "apogee"]
    launch_epoch_s_since_j2000: float


class WorkflowTaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    document: dict[str, Any]
    handover: WorkflowHandoverPayload | None = None


class WorkflowSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    tasks: list[WorkflowTaskPayload] = Field(
        min_length=1,
        max_length=MAX_WORKFLOW_TASKS,
    )


class WorkflowExportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_schema: Literal["wms.aerospace.workflow.v1"]
    source_workflow_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    provenance: dict[str, JsonValue] | None = None
    tasks: list[WorkflowTaskPayload] = Field(
        min_length=1,
        max_length=MAX_WORKFLOW_TASKS,
    )


class WorkflowReplayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: WorkflowExportDocument
    confirmed: Literal[True]


def create_app(
    *,
    workflow_store: WorkflowStore | None = None,
    scenarios_directory: str | Path | None = None,
    web_directory: str | Path | None = None,
    assistant_provider: StructuredLLMProvider | None = None,
) -> FastAPI:
    project_root = source_project_root() or Path(__file__).resolve().parents[2]
    scenarios_dir = _resource_directory(
        "scenarios",
        explicit=scenarios_directory,
        project_root=project_root,
    )
    web_dir = _resource_directory(
        "web",
        explicit=web_directory,
        project_root=project_root,
    )
    runs_root = default_runs_root()
    active_store = workflow_store or WorkflowStore(runs_root)
    registry = create_default_registry()
    family_registry = create_default_task_family_registry()
    family_registry.validate_backend_capabilities(registry.capabilities())
    scenario_catalog = _load_scenario_catalog(scenarios_dir)
    task_family_catalog = _build_task_family_catalog(
        scenario_catalog,
        family_registry,
        registry,
    )
    active_assistant = AssistantService(
        task_family_catalog=task_family_catalog,
        family_registry=family_registry,
        backend_registry=registry,
        provider=(
            assistant_provider
            if assistant_provider is not None
            else provider_from_configuration()
        ),
    )
    assistant_sessions = DraftSessionManager(active_assistant)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await active_assistant.aclose()
            active_store.close()

    application = FastAPI(
        title="Xaerospace Studio",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.workflow_store = active_store
    application.state.scenario_catalog = scenario_catalog
    application.state.task_family_registry = family_registry
    application.state.task_family_catalog = task_family_catalog
    application.state.assistant_service = active_assistant
    application.state.assistant_sessions = assistant_sessions
    application.mount(
        "/assets",
        StaticFiles(directory=web_dir),
        name="aerospace-web-assets",
    )

    @application.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_dir / "index.html", media_type="text/html")

    @application.get("/api/health")
    def health() -> dict[str, object]:
        result = {
            "status": "ok",
            "application": "Xaerospace Studio",
            "version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "execution_mode": "single_worker_fail_closed",
        }
        return result

    @application.get("/api/capabilities")
    def capabilities() -> dict[str, object]:
        result = {
            "protocol_version": PROTOCOL_VERSION,
            "backends": [
                capability.document() for capability in registry.capabilities()
            ],
        }
        return result

    @application.get("/api/parameter-definitions")
    def parameter_definitions() -> dict[str, object]:
        return parameter_catalog().document()

    @application.get("/api/assistant/status")
    async def assistant_status(refresh: bool = False) -> dict[str, object]:
        return await active_assistant.status(refresh=refresh)

    @application.post("/api/assistant/drafts")
    async def assistant_draft(
        payload: AssistantDraftPayload,
    ) -> dict[str, object]:
        try:
            result = await active_assistant.draft(
                prompt=payload.prompt,
                locale=payload.locale,
            )
        except AssistantUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except AssistantProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        except (AssistantOutputError, AssistantCompileError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        return result.model_dump(mode="json")

    @application.post(
        "/api/assistant/sessions",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_assistant_session(
        payload: AssistantDraftPayload,
    ) -> dict[str, object]:
        try:
            result = await assistant_sessions.create(
                prompt=payload.prompt,
                locale=payload.locale,
            )
        except Exception as exc:
            raise _assistant_http_exception(exc) from exc
        return result.model_dump(mode="json")

    @application.get("/api/assistant/sessions/{session_id}")
    async def get_assistant_session(session_id: str) -> dict[str, object]:
        try:
            result = await assistant_sessions.get(session_id)
        except Exception as exc:
            raise _assistant_http_exception(exc) from exc
        return result.model_dump(mode="json")

    @application.post("/api/assistant/sessions/{session_id}/turns")
    async def continue_assistant_session(
        session_id: str,
        payload: AssistantSessionTurnPayload,
    ) -> dict[str, object]:
        try:
            result = await assistant_sessions.continue_session(
                session_id,
                message=payload.message,
                expected_revision=payload.expected_revision,
            )
        except Exception as exc:
            raise _assistant_http_exception(exc) from exc
        return result.model_dump(mode="json")

    @application.post(
        "/api/assistant/sessions/{session_id}/executions",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def execute_assistant_session(
        session_id: str,
        payload: AssistantExecutionPayload,
    ) -> dict[str, object]:
        try:
            session = await assistant_sessions.begin_execution(
                session_id,
                expected_revision=payload.expected_revision,
            )
        except Exception as exc:
            raise _assistant_http_exception(exc) from exc

        draft = session.draft
        document = draft.draft_document
        if document is None:
            await assistant_sessions.abort_execution(
                session_id,
                expected_revision=payload.expected_revision,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="validated assistant proposal has no draft document",
            )
        task_id = f"assistant-{draft.provenance.draft_id[:16]}"
        provenance = {
            "origin": "assistant_confirmed",
            "assistant_session_id": session.session_id,
            "assistant_revision": session.revision,
            "assistant_draft_id": draft.provenance.draft_id,
            "family_id": draft.family_id,
            "variant_id": draft.variant_id,
            "provider_id": draft.provenance.provider_id,
            "model": draft.provenance.model,
            "prompt_version": draft.provenance.prompt_version,
        }
        try:
            workflow = active_store.submit(
                f"AI: {draft.family_id}/{draft.variant_id}",
                [{"task_id": task_id, "document": document}],
                provenance=provenance,
            )
            confirmed_session = await assistant_sessions.finish_execution(
                session_id,
                expected_revision=payload.expected_revision,
                workflow_id=str(workflow["workflow_id"]),
                task_id=task_id,
            )
        except (
            ScenarioValidationError,
            ProtocolValidationError,
            BackendRegistryError,
            WorkflowValidationError,
        ) as exc:
            await assistant_sessions.abort_execution(
                session_id,
                expected_revision=payload.expected_revision,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            await assistant_sessions.abort_execution(
                session_id,
                expected_revision=payload.expected_revision,
            )
            raise _assistant_http_exception(exc) from exc
        return {
            "session": confirmed_session.model_dump(mode="json"),
            "workflow": workflow,
        }

    @application.delete(
        "/api/assistant/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_assistant_session(
        session_id: str,
        expected_revision: int,
    ) -> Response:
        try:
            await assistant_sessions.delete(
                session_id,
                expected_revision=expected_revision,
            )
        except Exception as exc:
            raise _assistant_http_exception(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.get("/api/task-families")
    def task_families() -> dict[str, object]:
        result = {
            "task_families": [
                _task_family_summary(item) for item in task_family_catalog.values()
            ]
        }
        return result

    @application.get("/api/task-families/{family_id}/schema")
    def task_family_schema(family_id: str) -> dict[str, object]:
        try:
            result = family_registry.get(family_id).schema_document()
        except TaskFamilyNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        return result

    @application.get("/api/task-families/{family_id}")
    def task_family(family_id: str) -> dict[str, object]:
        try:
            result = task_family_catalog[family_id]
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"task family not found: {family_id}",
            ) from exc
        return result

    @application.get("/api/scenarios")
    def scenarios() -> dict[str, object]:
        result = {
            "scenarios": [
                {key: value for key, value in item.items() if key != "document"}
                for item in scenario_catalog.values()
            ]
        }
        return result

    @application.get("/api/scenarios/{scenario_id}")
    def scenario(scenario_id: str) -> dict[str, object]:
        try:
            catalog_item = scenario_catalog[scenario_id]
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"scenario not found: {scenario_id}",
            ) from exc
        result = catalog_item
        return result

    @application.post("/api/validate")
    def validate(payload: ValidateRequestPayload) -> dict[str, object]:
        try:
            request = request_from_document(payload.document)
            backend = registry.select(request).capabilities
        except (
            ScenarioValidationError,
            ProtocolValidationError,
            BackendRegistryError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        result = {
            "valid": True,
            "request": request.document(),
            "backend": backend.document(),
            "family": family_registry.describe_request(request),
        }
        return result

    @application.post(
        "/api/workflows",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_workflow(payload: WorkflowSubmitPayload) -> dict[str, object]:
        task_documents = _workflow_task_documents(payload.tasks)
        try:
            result = active_store.submit(payload.name, task_documents)
        except (
            ScenarioValidationError,
            ProtocolValidationError,
            BackendRegistryError,
            WorkflowValidationError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        return result

    @application.get("/api/workflows")
    def workflow_history(
        limit: int = 50,
        offset: int = 0,
        status_filter: str | None = Query(default=None, alias="status"),
    ) -> dict[str, object]:
        try:
            return active_store.list(
                limit=limit,
                offset=offset,
                status=status_filter,
            )
        except WorkflowValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    @application.post(
        "/api/workflow-replays",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def replay_workflow(payload: WorkflowReplayPayload) -> dict[str, object]:
        exported = payload.workflow
        provenance = {
            "origin": "workflow_replay",
            "workflow_schema": exported.workflow_schema,
            "source_workflow_id": exported.source_workflow_id,
            "source_provenance": exported.provenance,
        }
        try:
            result = active_store.submit(
                exported.name,
                _workflow_task_documents(exported.tasks),
                provenance=provenance,
            )
        except (
            ScenarioValidationError,
            ProtocolValidationError,
            BackendRegistryError,
            WorkflowValidationError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        return result

    @application.get("/api/workflows/{workflow_id}/export")
    def export_workflow(workflow_id: str) -> Response:
        try:
            document = active_store.export_document(workflow_id)
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        return Response(
            content=json.dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            + "\n",
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="workflow-{workflow_id}.json"'
                )
            },
        )

    @application.get("/api/workflows/{workflow_id}")
    def workflow(workflow_id: str) -> dict[str, object]:
        try:
            result = active_store.get(workflow_id)
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        return result

    @application.delete(
        "/api/workflows/{workflow_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_workflow(workflow_id: str) -> Response:
        try:
            active_store.delete(workflow_id)
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.get(
        "/api/workflows/{workflow_id}/tasks/{task_id}/artifacts/{artifact_name}"
    )
    def artifact(
        workflow_id: str,
        task_id: str,
        artifact_name: str,
    ) -> FileResponse:
        try:
            path = active_store.artifact_path(
                workflow_id,
                task_id,
                artifact_name,
            )
        except (
            WorkflowNotFoundError,
            WorkflowArtifactNotFoundError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        return FileResponse(path, media_type=_media_type(path))

    return application


def _workflow_task_documents(
    tasks: list[WorkflowTaskPayload],
) -> list[dict[str, object]]:
    return [
        {
            "task_id": task.task_id,
            "document": task.document,
            "handover": (
                task.handover.model_dump(mode="json")
                if task.handover is not None
                else None
            ),
        }
        for task in tasks
    ]


def _resource_directory(
    name: str,
    *,
    explicit: str | Path | None,
    project_root: Path,
) -> Path:
    if explicit is not None:
        result = Path(explicit)
    else:
        source_directory = project_root / name
        installed_directory = (
            Path(sysconfig.get_path("data")) / "share" / "wms-aerospace" / name
        )
        result = source_directory if source_directory.is_dir() else installed_directory
    if not result.is_dir():
        raise RuntimeError(f"{name} directory does not exist: {result}")
    return result


def _assistant_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, AssistantUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    if isinstance(exc, AssistantProviderError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )
    if isinstance(exc, DraftSessionNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    if isinstance(exc, DraftSessionConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, DraftSessionCapacityError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    if isinstance(
        exc,
        (AssistantOutputError, AssistantCompileError, TypeError, ValueError),
    ):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    raise exc


def _load_scenario_catalog(
    scenarios_directory: Path,
) -> dict[str, dict[str, object]]:
    catalog: dict[str, dict[str, object]] = {}
    for path in sorted(scenarios_directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"bundled scenario is not valid JSON: {path.name}"
            ) from exc
        request = request_from_document(raw)
        scenario_id = path.stem
        catalog[scenario_id] = {
            "scenario_id": scenario_id,
            "label": request.label,
            "description": request.description,
            "task_kind": request.task_kind,
            "contract_schema": request.contract_schema,
            "backend_id": request.backend_preference,
            "document": raw,
        }
    if not catalog:
        raise RuntimeError(
            f"scenario directory contains no JSON scenarios: {scenarios_directory}"
        )
    return catalog


def _build_task_family_catalog(
    scenario_catalog: dict[str, dict[str, object]],
    family_registry: TaskFamilyRegistry,
    backend_registry: BackendRegistry,
) -> dict[str, dict[str, object]]:
    capabilities = {item.backend_id: item for item in backend_registry.capabilities()}
    scenario_variants: dict[str, tuple[str, str]] = {}
    for scenario_id, item in scenario_catalog.items():
        document = item["document"]
        request = request_from_document(document)
        description = family_registry.describe_request(request)
        scenario_variants[scenario_id] = (
            str(description["family_id"]),
            str(description["variant_id"]),
        )
    catalog: dict[str, dict[str, object]] = {}
    for family in family_registry.families():
        variants: list[dict[str, object]] = []
        for variant in family.variants:
            candidates = [
                item
                for item in scenario_catalog.values()
                if item["backend_id"] in family.backend_ids
                and item["contract_schema"] == family.contract_schema
                and scenario_variants[str(item["scenario_id"])]
                == (family.family_id, variant.variant_id)
            ]
            if not candidates:
                raise RuntimeError(
                    f"family {family.family_id!r} variant "
                    f"{variant.variant_id!r} has no starter scenario"
                )
            candidates.sort(key=lambda item: str(item["scenario_id"]))
            starter = candidates[0]
            document = starter["document"]
            if not isinstance(document, dict):
                raise TypeError(
                    f"starter document is not an object: {starter['scenario_id']}"
                )
            variants.append(
                {
                    **variant.document(),
                    "example_scenario_ids": [
                        str(item["scenario_id"]) for item in candidates
                    ],
                    "starter_document": document,
                }
            )
        backend_documents = [
            capabilities[backend_id].document() for backend_id in family.backend_ids
        ]
        catalog[family.family_id] = {
            **family.document(),
            "backends": backend_documents,
            "variants": variants,
            "schema_url": (f"/api/task-families/{family.family_id}/schema"),
        }
    return catalog


def _task_family_summary(
    item: dict[str, object],
) -> dict[str, object]:
    variants = item["variants"]
    if not isinstance(variants, list):
        raise TypeError("task-family variants must be a list")
    return {
        key: value
        for key, value in item.items()
        if key not in {"components", "variants"}
    } | {
        "variants": [
            {key: value for key, value in variant.items() if key != "starter_document"}
            for variant in variants
            if isinstance(variant, dict)
        ]
    }


def _media_type(path: Path) -> str:
    media_types = {
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".png": "image/png",
    }
    result = media_types.get(path.suffix.lower(), "application/octet-stream")
    return result


app = create_app()
