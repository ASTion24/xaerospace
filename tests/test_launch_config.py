import copy
import json
from pathlib import Path

import pytest

from aerospace_simulator.config import ScenarioValidationError
from aerospace_simulator.launch_config import (
    LAUNCH_CONTRACT_SCHEMA,
    LaunchToOrbitConfig,
    load_launch_scenario,
)
from aerospace_simulator.request_io import (
    load_request,
    request_from_launch_scenario,
)

SCENARIO_PATH = (
    Path(__file__).parents[1] / "scenarios" / "two_stage_220km_launch_demo.json"
)


def test_launch_contract_loads_as_an_independent_typed_schema():
    config = load_launch_scenario(SCENARIO_PATH)
    request = request_from_launch_scenario(config)

    assert config.dynamics == "two_stage_launch_to_orbit"
    assert len(config.stages) == 2
    assert config.lift_off_mass_kg == pytest.approx(505_000.0)
    assert config.insertion_time_s == pytest.approx(405.0)
    assert request.contract_schema == LAUNCH_CONTRACT_SCHEMA
    assert request.backend_preference == "tudatpy"
    assert request.document()["contract"]["stages"][1]["guidance_pitch_program"][
        -1
    ] == [250.0, -2.0]


def test_public_loader_detects_and_replays_launch_requests(tmp_path):
    request = load_request(SCENARIO_PATH)
    replay_path = tmp_path / "request.json"
    replay_path.write_text(json.dumps(request.document()), encoding="utf-8")

    replayed = load_request(replay_path)

    assert replayed.document() == request.document()
    assert isinstance(replayed.contract, LaunchToOrbitConfig)


def test_launch_contract_rejects_inconsistent_propellant_and_guidance():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    inconsistent = copy.deepcopy(raw)
    inconsistent["stages"][0]["propellant_mass_kg"] += 1.0
    with pytest.raises(ScenarioValidationError, match="must satisfy"):
        LaunchToOrbitConfig.from_mapping(inconsistent)

    nonmonotonic = copy.deepcopy(raw)
    nonmonotonic["stages"][1]["guidance_pitch_program"][2][0] = 50.0
    with pytest.raises(ScenarioValidationError, match="strictly increasing"):
        LaunchToOrbitConfig.from_mapping(nonmonotonic)

    wrong_end = copy.deepcopy(raw)
    wrong_end["stages"][1]["guidance_pitch_program"][-1][0] = 249.0
    with pytest.raises(ScenarioValidationError, match="must end at burn_time_s"):
        LaunchToOrbitConfig.from_mapping(wrong_end)


def test_launch_contract_rejects_non_two_stage_or_ambiguous_sampling():
    raw = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    single_stage = copy.deepcopy(raw)
    single_stage["stages"] = single_stage["stages"][:1]
    with pytest.raises(ScenarioValidationError, match="exactly two"):
        LaunchToOrbitConfig.from_mapping(single_stage)

    ambiguous = copy.deepcopy(raw)
    ambiguous["propagation"]["output_interval_s"] = 4.5
    with pytest.raises(ScenarioValidationError, match="integer multiple"):
        LaunchToOrbitConfig.from_mapping(ambiguous)
