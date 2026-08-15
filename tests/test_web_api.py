import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aerospace_simulator import __version__, web_api
from aerospace_simulator.web_api import create_app
from aerospace_simulator.workflows import WorkflowStore

PROJECT_ROOT = Path(__file__).parents[1]
SCENARIO_DIR = PROJECT_ROOT / "scenarios"
WEB_DIR = PROJECT_ROOT / "web"


@pytest.fixture
def client(tmp_path):
    store = WorkflowStore(tmp_path / "runs")
    app = create_app(
        workflow_store=store,
        scenarios_directory=SCENARIO_DIR,
        web_directory=WEB_DIR,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_web_application_serves_catalog_capabilities_and_static_assets(client):
    health = client.get("/api/health")
    assistant_status = client.get("/api/assistant/status")
    capabilities = client.get("/api/capabilities")
    task_families = client.get("/api/task-families")
    scenarios = client.get("/api/scenarios")
    index = client.get("/")
    javascript = client.get("/assets/app.js")
    translations = client.get("/assets/i18n.js")
    parameter_definitions = client.get("/api/parameter-definitions")

    assert health.status_code == 200
    assert health.json()["version"] == __version__
    assert health.json()["execution_mode"] == "single_worker_fail_closed"
    assert assistant_status.status_code == 200
    assert assistant_status.json()["automatic_execution"] is False
    assert assistant_status.json()["confirmed_execution"] is True
    assert {backend["backend_id"] for backend in capabilities.json()["backends"]} == {
        "basilisk",
        "jsbsim",
        "rocketpy",
        "tudatpy",
    }
    assert {item["family_id"] for item in task_families.json()["task_families"]} == {
        "aircraft_flight",
        "launch_to_orbit",
        "orbit_propagation",
        "rocket_flight",
        "spacecraft_gnc",
    }
    assert (
        sum(item["variant_count"] for item in task_families.json()["task_families"])
        == 16
    )
    assert len(scenarios.json()["scenarios"]) == 17
    assert "任务编排" in index.text
    assert "新建任务" in index.text
    assert "示例模板" in index.text
    assert "参数表单" in index.text
    assert "AI 合同会话" in index.text
    assert 'id="assistantPrompt"' in index.text
    assert 'id="assistantConversation"' in index.text
    assert 'id="newAssistantSession"' in index.text
    assert 'id="applyAssistantDraft"' in index.text
    assert 'id="confirmAndRunAssistantDraft"' in index.text
    assert 'id="sourceViewTabs"' in index.text
    assert 'id="assistantSourcePane"' in index.text
    assert 'id="catalogSourcePane"' in index.text
    assert 'id="workbenchTabs"' in index.text
    assert 'id="editorPane"' in index.text
    assert 'id="inspectorPane"' in index.text
    assert 'id="quickStartGuide"' in index.text
    assert 'id="toggleAdvancedParameters"' in index.text
    assert 'id="handoverSection"' in index.text
    assert 'id="verificationSection"' in index.text
    assert 'id="replayWorkflow"' in index.text
    assert 'id="toggleWorkflowHistory"' in index.text
    assert 'id="workflowHistoryPanel"' in index.text
    assert 'id="workflowHistoryList"' in index.text
    assert 'id="replayWorkflowFile"' in index.text
    assert 'id="exportWorkflow"' in index.text
    assert 'src="/assets/parameter-guide.js"' not in index.text
    assert "导入 JSON" in index.text
    assert 'id="importTaskFile"' in index.text
    assert 'data-locale="zh-CN"' in index.text
    assert 'data-locale="en"' in index.text
    assert "runWorkflow" in javascript.text
    assert 'api("/api/task-families")' in javascript.text
    assert 'api("/api/parameter-definitions")' in javascript.text
    assert "renderParameterForm" in javascript.text
    assert "handoverControls" in javascript.text
    assert "renderTargetOrbitVerification" in javascript.text
    assert "rocketpy_to_tudatpy" in javascript.text
    assert "generateNaturalLanguageDraft" in javascript.text
    assert 'api("/api/assistant/sessions"' in javascript.text
    assert "/executions`" in javascript.text
    assert "expected_revision: session.revision" in javascript.text
    assert "confirmed: true" in javascript.text
    assert "renderAssistantConversation" in javascript.text
    assert "localizedParameterField" in javascript.text
    assert "renderQuickStart" in javascript.text
    assert 'api("/api/validate"' in javascript.text
    assert 'api("/api/workflow-replays"' in javascript.text
    assert "/export`" in javascript.text
    assert "replayWorkflowDocument" in javascript.text
    assert "exportWorkflowDocument" in javascript.text
    assert 'api("/api/workflows?limit=50")' in javascript.text
    assert "restoreActiveWorkflow" in javascript.text
    assert "applyRestoredWorkflow" in javascript.text
    assert "workflowHistoryDeleted" in translations.text
    assert "Workflow History" in translations.text
    assert "setSourceView" in javascript.text
    assert 'setWorkbenchView("results")' in javascript.text
    assert (
        'assistantPrompt.addEventListener("input", () => updateControls())'
        in javascript.text
    )
    assert "setLocale" in javascript.text
    assert "Xaerospace 工作台" in translations.text
    assert "Xaerospace Studio" in translations.text
    assert "单级火箭三自由度" in translations.text
    assert "LLM 意图与合同编译" in translations.text
    assert "Single-Stage Rocket 3DOF" in translations.text
    assert "LLM INTENT & CONTRACT COMPILATION" in translations.text
    assert "parameterRangeMaxExclusive" in translations.text
    assert "运载火箭发射入轨" in translations.text
    assert "Target orbit acceptance passed" in translations.text
    assert "重放工作流" in translations.text
    assert "Assistant Provenance" in translations.text
    assert parameter_definitions.status_code == 200
    definitions = parameter_definitions.json()
    assert definitions["fields"]["launch.inclination_deg"]["zh-CN"]["label"] == (
        "发射仰角"
    )
    assert definitions["fields"]["calibrated_airspeed_m_s"]["zh-CN"]["label"] == (
        "初始校准空速"
    )
    assert definitions["fields"]["mrp_sigma_bn"]["en"]["label"] == (
        "Initial Attitude MRP"
    )
    assert definitions["fields"]["eccentricity"]["max"] == 1
    assert definitions["fields"]["eccentricity"]["maxExclusive"] is True
    assert definitions["fields"]["radius_m"]["minExclusive"] is True
    assert index.headers["x-content-type-options"] == "nosniff"


def test_parameter_definitions_cover_every_supported_starter_field(client):
    fields = client.get("/api/parameter-definitions").json()["fields"]
    family_ids = (
        "aircraft_flight",
        "launch_to_orbit",
        "orbit_propagation",
        "rocket_flight",
        "spacecraft_gnc",
    )

    def leaf_paths(value, prefix=""):
        if isinstance(value, dict):
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else key
                yield from leaf_paths(child, path)
        else:
            yield prefix

    uncovered = set()
    for family_id in family_ids:
        family = client.get(f"/api/task-families/{family_id}").json()
        for variant in family["variants"]:
            for path in leaf_paths(variant["starter_document"]):
                leaf = path.rsplit(".", maxsplit=1)[-1]
                if leaf not in fields and path not in fields:
                    uncovered.add(path)

    assert uncovered == set()


def test_parameter_definitions_cover_every_supported_starter_section(client):
    sections = client.get("/api/parameter-definitions").json()["sections"]
    identity_fields = {
        "schema_version",
        "name",
        "description",
        "backend",
        "dynamics",
        "protocol_version",
        "request_id",
        "label",
        "task_kind",
        "contract_schema",
        "backend_preference",
    }
    uncovered = set()

    for family_id in (
        "aircraft_flight",
        "launch_to_orbit",
        "orbit_propagation",
        "rocket_flight",
        "spacecraft_gnc",
    ):
        family = client.get(f"/api/task-families/{family_id}").json()
        for variant in family["variants"]:
            for key in variant["starter_document"]:
                if key in identity_fields:
                    continue
                if key not in sections:
                    uncovered.add(key)

    assert uncovered == set()


def test_task_family_catalog_exposes_variants_components_and_schema(client):
    response = client.get("/api/task-families/rocket_flight")
    schema_response = client.get("/api/task-families/rocket_flight/schema")

    assert response.status_code == 200
    family = response.json()
    assert family["default_variant_id"] == "point_mass_3dof"
    assert family["backend_ids"] == ["rocketpy"]
    assert {variant["task_kind"] for variant in family["variants"]} == {
        "single_stage_point_mass_3dof",
        "single_stage_point_mass_3dof_recovery",
        "single_stage_rigid_body_6dof",
        "single_stage_rigid_body_6dof_recovery",
    }
    assert all("starter_document" in variant for variant in family["variants"])
    assert {component["component_id"] for component in family["components"]} >= {
        "rocket.fidelity.point_mass_3dof",
        "rocket.fidelity.rigid_body_6dof",
        "rocket.recovery.parachute",
    }

    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert schema["x-wms-family-id"] == "rocket_flight"
    assert schema["x-wms-parameter-definitions-url"] == ("/api/parameter-definitions")
    assert (
        schema["x-wms-parameter-definitions"]["fields"]["radius_m"]["minExclusive"]
        is True
    )
    assert schema["properties"]["dynamics"]["enum"] == [
        "single_stage_point_mass_3dof",
        "single_stage_point_mass_3dof_recovery",
        "single_stage_rigid_body_6dof",
        "single_stage_rigid_body_6dof_recovery",
    ]


def test_task_family_catalog_provides_customizable_starter_contract(client):
    response = client.get("/api/task-families/rocket_flight")

    assert response.status_code == 200
    family = response.json()
    variant = next(
        item for item in family["variants"] if item["variant_id"] == "point_mass_3dof"
    )
    assert variant["example_scenario_ids"] == ["single_stage_demo"]
    document = variant["starter_document"]
    document["name"] = "custom_heading_rocket"
    document["description"] = "A user-configured launch heading."
    document["launch"]["heading_deg"] = 117.0
    validated = client.post("/api/validate", json={"document": document})

    assert validated.status_code == 200
    assert validated.json()["request"]["label"] == "custom_heading_rocket"
    assert validated.json()["request"]["contract"]["launch"]["heading_deg"] == 117.0


def test_removed_task_type_api_is_not_routable(client):
    assert client.get("/api/task-types").status_code == 404
    assert (
        client.get("/api/task-types/rocketpy:single_stage_point_mass_3dof").status_code
        == 404
    )


def test_expanded_family_variants_have_distinct_starter_contracts(client):
    expected = {
        "rocket_flight": {
            "point_mass_3dof",
            "point_mass_3dof_recovery",
            "rigid_body_6dof",
            "rigid_body_6dof_recovery",
        },
        "aircraft_flight": {
            "c172p_trimmed_6dof",
            "c172r_trimmed_6dof",
            "c182_trimmed_6dof",
            "c310_trimmed_6dof",
            "j3cub_trimmed_6dof",
        },
        "orbit_propagation": {
            "earth_two_body",
            "earth_j2",
            "earth_j2_aerodynamic_drag",
        },
        "launch_to_orbit": {"two_stage_220km_reference"},
        "spacecraft_gnc": {
            "inertial_pointing_rw",
            "reaction_wheel_rate_damping",
            "uncontrolled_attitude_rw",
        },
    }
    for family_id, variant_ids in expected.items():
        family = client.get(f"/api/task-families/{family_id}").json()
        assert {item["variant_id"] for item in family["variants"]} == variant_ids
        for variant in family["variants"]:
            validated = client.post(
                "/api/validate",
                json={"document": variant["starter_document"]},
            )
            assert validated.status_code == 200
            assert validated.json()["family"]["variant_id"] == variant["variant_id"]


def test_web_api_validates_documents_and_reports_exact_backend(client):
    scenario = client.get("/api/scenarios/single_stage_demo").json()

    response = client.post(
        "/api/validate",
        json={"document": scenario["document"]},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["backend"]["backend_id"] == "rocketpy"
    assert response.json()["request"]["task_kind"] == ("single_stage_point_mass_3dof")
    assert response.json()["family"] == {
        "family_id": "rocket_flight",
        "family_schema": "wms.aerospace.family.rocket_flight.v3",
        "variant_id": "point_mass_3dof",
        "component_ids": [
            "rocket.fidelity.point_mass_3dof",
            "rocket.recovery.none",
        ],
    }

    invalid = {**scenario["document"], "backend": "missing"}
    rejected = client.post("/api/validate", json={"document": invalid})
    assert rejected.status_code == 422
    assert "not registered" in rejected.json()["detail"]


def test_web_workflow_executes_real_backend_and_serves_artifacts(client):
    scenario = client.get("/api/scenarios/single_stage_demo").json()
    response = client.post(
        "/api/workflows",
        json={
            "name": "API acceptance workflow",
            "tasks": [
                {
                    "task_id": "rocket-task",
                    "document": scenario["document"],
                }
            ],
        },
    )

    assert response.status_code == 202
    workflow = _wait_for_workflow(client, response.json()["workflow_id"])
    assert workflow["status"] == "completed"
    assert workflow["progress"] == {
        "finished": 1,
        "succeeded": 1,
        "total": 1,
        "fraction": 1.0,
    }
    task = workflow["tasks"][0]
    assert task["status"] == "completed"
    assert task["summary"]["backend"]["backend_id"] == "rocketpy"
    assert any(metric["name"] == "apogee_agl" for metric in task["summary"]["metrics"])
    artifact_names = {artifact["name"] for artifact in task["artifacts"]}
    assert {"result", "model_report", "trajectory", "flight_profile"} <= (
        artifact_names
    )

    result_artifact = next(
        artifact for artifact in task["artifacts"] if artifact["name"] == "result"
    )
    downloaded = client.get(result_artifact["url"])
    assert downloaded.status_code == 200
    assert downloaded.json()["backend"]["backend_id"] == "rocketpy"

    image_artifact = next(
        artifact
        for artifact in task["artifacts"]
        if artifact["name"] == "flight_profile"
    )
    image = client.get(image_artifact["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")

    history = client.get("/api/workflows?limit=10&status=completed")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["workflows"][0]["workflow_id"] == workflow["workflow_id"]
    assert history.json()["workflows"][0]["backends"] == ["rocketpy"]

    deleted = client.delete(f"/api/workflows/{workflow['workflow_id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/workflows/{workflow['workflow_id']}").status_code == 404
    assert client.get("/api/workflows").json()["total"] == 0


def test_workflow_history_rejects_invalid_pagination_and_status(client):
    assert client.get("/api/workflows?limit=0").status_code == 422
    assert client.get("/api/workflows?offset=-1").status_code == 422
    assert client.get("/api/workflows?status=unknown").status_code == 422


def test_workflow_export_and_explicit_replay_preserve_contract(client):
    scenario = client.get("/api/scenarios/single_stage_demo").json()["document"]
    submitted = client.post(
        "/api/workflows",
        json={
            "name": "Exportable workflow",
            "tasks": [{"task_id": "rocket-task", "document": scenario}],
        },
    )
    original = _wait_for_workflow(client, submitted.json()["workflow_id"])

    exported_response = client.get(f"/api/workflows/{original['workflow_id']}/export")
    exported = exported_response.json()
    unconfirmed = client.post(
        "/api/workflow-replays",
        json={"workflow": exported, "confirmed": False},
    )
    replay_response = client.post(
        "/api/workflow-replays",
        json={"workflow": exported, "confirmed": True},
    )
    replay = _wait_for_workflow(
        client,
        replay_response.json()["workflow_id"],
    )

    original_metrics = {
        metric["name"]: metric["value"]
        for metric in original["tasks"][0]["summary"]["metrics"]
    }
    replay_metrics = {
        metric["name"]: metric["value"]
        for metric in replay["tasks"][0]["summary"]["metrics"]
    }

    assert exported_response.status_code == 200
    assert exported_response.headers["content-disposition"] == (
        f'attachment; filename="workflow-{original["workflow_id"]}.json"'
    )
    assert exported == {
        "workflow_schema": "wms.aerospace.workflow.v1",
        "source_workflow_id": original["workflow_id"],
        "name": "Exportable workflow",
        "provenance": None,
        "tasks": [
            {
                "task_id": "rocket-task",
                "document": scenario,
                "handover": None,
            }
        ],
    }
    assert unconfirmed.status_code == 422
    assert replay_response.status_code == 202
    assert replay["status"] == "completed"
    assert replay["workflow_id"] != original["workflow_id"]
    assert replay["provenance"] == {
        "origin": "workflow_replay",
        "workflow_schema": "wms.aerospace.workflow.v1",
        "source_workflow_id": original["workflow_id"],
        "source_provenance": None,
    }
    assert replay["tasks"][0]["request"] == original["tasks"][0]["request"]
    assert replay_metrics["apogee_agl"] == pytest.approx(
        original_metrics["apogee_agl"],
        rel=0.0,
        abs=0.0,
    )


def test_web_workflow_executes_verified_launch_to_orbit(client):
    scenario = client.get("/api/scenarios/two_stage_220km_launch_demo").json()
    response = client.post(
        "/api/workflows",
        json={
            "name": "Verified orbital launch",
            "tasks": [
                {
                    "task_id": "orbital-launch",
                    "document": scenario["document"],
                }
            ],
        },
    )

    assert response.status_code == 202
    workflow = _wait_for_workflow(client, response.json()["workflow_id"])
    task = workflow["tasks"][0]
    metrics = {metric["name"]: metric["value"] for metric in task["summary"]["metrics"]}

    assert workflow["status"] == "completed"
    assert task["backend"]["backend_id"] == "tudatpy"
    assert metrics["insertion_periapsis_altitude"] > 180_000.0
    assert metrics["insertion_apoapsis_altitude"] > 180_000.0
    assert metrics["insertion_eccentricity"] < 0.005
    assert abs(metrics["mass_balance_error"]) < 1e-6
    assert {artifact["name"] for artifact in task["artifacts"]} >= {
        "launch_profile",
        "orbit_profile",
        "result",
    }


def test_workflow_failure_stops_later_tasks(tmp_path):
    def failing_runner(_):
        raise RuntimeError("planned backend failure")

    store = WorkflowStore(tmp_path / "failed-runs", runner=failing_runner)
    app = create_app(
        workflow_store=store,
        scenarios_directory=SCENARIO_DIR,
        web_directory=WEB_DIR,
    )
    with TestClient(app) as client:
        scenario = client.get("/api/scenarios/single_stage_demo").json()["document"]
        response = client.post(
            "/api/workflows",
            json={
                "name": "Fail-closed workflow",
                "tasks": [
                    {"task_id": "first", "document": scenario},
                    {"task_id": "second", "document": scenario},
                ],
            },
        )
        workflow = _wait_for_workflow(client, response.json()["workflow_id"])

    assert workflow["status"] == "failed"
    assert workflow["tasks"][0]["status"] == "failed"
    assert workflow["tasks"][0]["error"]["message"] == "planned backend failure"
    assert workflow["tasks"][1]["status"] == "skipped"
    assert workflow["progress"] == {
        "finished": 2,
        "succeeded": 0,
        "total": 2,
        "fraction": 1.0,
    }


def test_workflow_rejects_duplicate_task_ids_before_execution(client):
    scenario = client.get("/api/scenarios/single_stage_demo").json()["document"]
    response = client.post(
        "/api/workflows",
        json={
            "name": "Invalid workflow",
            "tasks": [
                {"task_id": "duplicate", "document": scenario},
                {"task_id": "duplicate", "document": scenario},
            ],
        },
    )

    assert response.status_code == 422
    assert "must be unique" in response.json()["detail"]


def test_workflow_api_rejects_invalid_handover_dependency(client):
    orbit = client.get("/api/scenarios/earth_orbit_two_body_demo").json()["document"]
    response = client.post(
        "/api/workflows",
        json={
            "name": "Invalid handover",
            "tasks": [
                {
                    "task_id": "orbit",
                    "document": orbit,
                    "handover": {
                        "type": "rocketpy_to_tudatpy",
                        "source_task_id": "missing-ascent",
                        "source_event": "burnout",
                        "launch_epoch_s_since_j2000": 0,
                    },
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "source task not found" in response.json()["detail"]


def test_web_resources_fall_back_to_installed_data_directory(
    tmp_path,
    monkeypatch,
):
    installed_root = tmp_path / "installed"
    installed_web = installed_root / "share" / "wms-aerospace" / "web"
    installed_web.mkdir(parents=True)
    monkeypatch.setattr(
        web_api.sysconfig,
        "get_path",
        lambda _: str(installed_root),
    )

    resolved = web_api._resource_directory(
        "web",
        explicit=None,
        project_root=tmp_path / "missing-source-root",
    )

    assert resolved == installed_web


def test_default_workflow_runs_use_user_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XAEROSPACE_HOME", str(tmp_path / "xaerospace-data"))
    app = create_app(
        scenarios_directory=SCENARIO_DIR,
        web_directory=WEB_DIR,
    )

    with TestClient(app):
        assert app.state.workflow_store.runs_root == (
            tmp_path / "xaerospace-data" / "runs"
        )


def _wait_for_workflow(
    client: TestClient,
    workflow_id: str,
) -> dict[str, object]:
    workflow: dict[str, object] = {}
    for _ in range(120):
        response = client.get(f"/api/workflows/{workflow_id}")
        assert response.status_code == 200
        workflow = response.json()
        if workflow["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert workflow["status"] in {"completed", "failed"}
    return workflow
