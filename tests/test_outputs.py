import csv
import json
from pathlib import Path

import numpy as np

from aerospace_simulator.config import load_scenario
from aerospace_simulator.models import ModelManifest
from aerospace_simulator.outputs import write_outputs
from aerospace_simulator.protocol import (
    BackendCapabilities,
    ResultChannel,
    ResultMetric,
    SimulationRequest,
    UnifiedSimulationResult,
)
from aerospace_simulator.simulation import simulate

SCENARIO_PATH = Path(__file__).parents[1] / "scenarios" / "single_stage_demo.json"


def test_write_outputs_creates_normalized_and_presentation_artifacts(tmp_path):
    result = simulate(load_scenario(SCENARIO_PATH))
    artifacts = write_outputs(result, tmp_path)

    assert set(artifacts) == {
        "request",
        "result",
        "summary",
        "model_manifest",
        "model_report",
        "trajectory",
        "flight_profile",
        "attitude_profile",
    }
    assert all(
        path.is_file() and path.stat().st_size > 0 for path in artifacts.values()
    )

    normalized = json.loads(artifacts["result"].read_text(encoding="utf-8"))
    assert normalized["protocol_version"] == 1
    assert normalized["backend"]["backend_id"] == "rocketpy"
    assert normalized["request"]["task_kind"] == "single_stage_point_mass_3dof"
    assert len(normalized["time"]["values"]) > 100
    assert {channel["name"] for channel in normalized["channels"]} >= {
        "altitude_agl",
        "quaternion_e0",
        "omega3",
    }

    summary = json.loads(artifacts["summary"].read_text(encoding="utf-8"))
    assert summary["time"]["sample_count"] == len(normalized["time"]["values"])
    assert all("values" not in channel for channel in summary["channels"])

    manifest = json.loads(artifacts["model_manifest"].read_text(encoding="utf-8"))
    assert any(
        equation["id"] == "free_flight_translation"
        for equation in manifest["equations"]
    )
    report = artifacts["model_report"].read_text(encoding="utf-8")
    assert "## Governing Equations" in report

    with artifacts["trajectory"].open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(normalized["time"]["values"])
    assert {row["phase"] for row in rows} == {
        "rail",
        "powered_ascent",
        "coast_ascent",
        "descent",
    }
    assert all(float(row["omega3"]) == 0.0 for row in rows)


def test_generic_outputs_do_not_require_rocket_specific_channels(tmp_path):
    request = SimulationRequest(
        protocol_version=1,
        request_id="generic",
        label="Generic trajectory",
        description="Protocol-only test",
        task_kind="generic_trajectory",
        contract_schema="test.v1",
        backend_preference="test",
        contract={"initial_state": [0.0]},
    )
    backend = BackendCapabilities(
        backend_id="test",
        backend_name="Test backend",
        backend_version="1",
        supported_task_kinds=("generic_trajectory",),
        supported_contract_schemas=("test.v1",),
    )
    manifest = ModelManifest(
        schema_version=1,
        fidelity="test",
        backend_name="Test backend",
        backend_version="1",
        model_name="Generic trajectory",
        dynamics="generic_trajectory",
        coordinate_system=("Generic frame.",),
        state_vector=(),
        initial_state=(),
        equations=(),
        parameters=(),
        input_series=(),
        events=(),
        assumptions=(),
        limitations=(),
        implementation_references=(),
    )
    result = UnifiedSimulationResult(
        protocol_version=1,
        request=request,
        backend=backend,
        time_s=np.asarray([0.0, 1.0]),
        channels=(
            ResultChannel(
                name="state_x",
                quantity="generic_state",
                unit="1",
                frame="generic",
                values=np.asarray([0.0, 2.0]),
            ),
        ),
        events=(),
        metrics=(ResultMetric("final_state_x", 2.0, "1"),),
        model_manifest=manifest,
    )

    artifacts = write_outputs(result, tmp_path)

    assert set(artifacts) == {
        "request",
        "result",
        "summary",
        "model_manifest",
        "model_report",
        "trajectory",
    }
    with artifacts["trajectory"].open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[-1] == {"time_s": "1.0", "state_x": "2.0"}
