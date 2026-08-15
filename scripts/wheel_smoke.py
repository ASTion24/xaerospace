from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import site
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install a wheel temporarily and smoke-test its public surfaces."
    )
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        raise RuntimeError(f"wheel does not exist: {wheel}")

    with tempfile.TemporaryDirectory(prefix="xaerospace-wheel-smoke-") as name:
        root = Path(name)
        environment = root / "venv"
        data_root = root / "data"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "venv",
                "--system-site-packages",
                str(environment),
            ],
            check=True,
        )
        python = environment / "bin" / "python"
        adjacent_uv = Path(sys.executable).with_name("uv")
        uv = shutil.which("uv") or (str(adjacent_uv) if adjacent_uv.is_file() else None)
        if uv is None:
            raise RuntimeError("uv is required to install the wheel smoke target")
        subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                "--reinstall",
                str(wheel),
            ],
            check=True,
        )
        smoke_environment = _smoke_environment(data_root)
        completed = subprocess.run(
            [str(python), "-c", SMOKE_PROGRAM],
            check=True,
            capture_output=True,
            text=True,
            env=smoke_environment,
        )
        result = json.loads(completed.stdout)

    expected_version = runpy.run_path(
        str(PROJECT_ROOT / "src" / "aerospace_simulator" / "_version.py")
    )["__version__"]
    if result["version"] != expected_version:
        raise RuntimeError(f"wheel reports unexpected version: {result['version']}")
    if result["scenario_count"] != 17:
        raise RuntimeError("installed wheel does not expose 17 scenarios")
    if result["health_status"] != "ok":
        raise RuntimeError("installed wheel health endpoint is not healthy")
    if result["runs_root"] != str((data_root / "runs").resolve()):
        raise RuntimeError("installed wheel writes runs outside its user data root")
    if result["workspace_path"] != str((data_root / "workspace.sqlite3").resolve()):
        raise RuntimeError("installed wheel writes its workspace outside user data")
    if result["restored_status"] != "completed":
        raise RuntimeError("installed wheel did not restore the completed workflow")
    if result["verified_integrity"] != "ok":
        raise RuntimeError("installed wheel did not verify the original artifact")
    if result["tampered_integrity"] != "corrupt":
        raise RuntimeError("installed wheel did not detect the tampered artifact")
    if result["tampered_download_status"] != 404:
        raise RuntimeError("installed wheel served a corrupt artifact")
    if result["remaining_workflows"] != 0:
        raise RuntimeError("installed wheel did not persist workflow deletion")
    if "site-packages" in result["tudat_python"]:
        raise RuntimeError("installed wheel derives TudatPy under site-packages")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _smoke_environment(data_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    dependency_paths = [
        Path(item).resolve() for item in site.getsitepackages() if Path(item).is_dir()
    ]
    for item in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if item:
            dependency_paths.append(Path(item).resolve())
    source_root = (PROJECT_ROOT / "src").resolve()
    filtered = [
        str(path)
        for path in dict.fromkeys(dependency_paths)
        if path != source_root and path != PROJECT_ROOT
    ]
    environment["PYTHONPATH"] = os.pathsep.join(filtered)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["XAEROSPACE_HOME"] = str(data_root)
    environment.pop("XAEROSPACE_RUN_DIR", None)
    environment.pop("XAEROSPACE_TUDATPY_PYTHON", None)
    environment.pop("XAEROSPACE_TUDAT_HOME", None)
    return environment


SMOKE_PROGRAM = r"""
import json
import os
import time
from pathlib import Path

data_root = Path(os.environ["XAEROSPACE_HOME"])
workspace_path = data_root / "workspace.sqlite3"
assert not data_root.exists()

import aerospace_simulator
from aerospace_simulator.cli import _bundled_resource, build_parser
from aerospace_simulator.tudat_runtime import runtime_paths
from aerospace_simulator.web_api import create_app
from fastapi.testclient import TestClient

assert not data_root.exists()

package_path = Path(aerospace_simulator.__file__).resolve()
assert "site-packages" in str(package_path), package_path
assert build_parser().parse_args(["web", "--no-browser"]).port == 8000
assert build_parser().parse_args(["setup-tudatpy"]).command == "setup-tudatpy"
scenarios = _bundled_resource("scenarios")
setup_script = _bundled_resource("scripts", "setup_tudatpy_macos_arm64.sh")
assert setup_script.is_file()

first_app = create_app()
with TestClient(first_app) as client:
    health = client.get("/api/health")
    catalog = client.get("/api/scenarios")
    assert client.get("/").status_code == 200
    scenario = client.get("/api/scenarios/single_stage_demo").json()
    submitted = client.post(
        "/api/workflows",
        json={
            "name": "Installed wheel persistence smoke",
            "tasks": [
                {
                    "task_id": "rocket",
                    "document": scenario["document"],
                }
            ],
        },
    )
    assert submitted.status_code == 202, submitted.text
    workflow_id = submitted.json()["workflow_id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        workflow = client.get(f"/api/workflows/{workflow_id}").json()
        if workflow["status"] in {"completed", "failed", "interrupted"}:
            break
        time.sleep(0.1)
    assert workflow["status"] == "completed", workflow

assert workspace_path.is_file()
runs_root = str(first_app.state.workflow_store.runs_root)

second_app = create_app()
with TestClient(second_app) as client:
    history = client.get("/api/workflows?limit=10").json()
    assert history["total"] == 1
    restored = client.get(f"/api/workflows/{workflow_id}").json()
    result_artifact = next(
        item
        for item in restored["tasks"][0]["artifacts"]
        if item["name"] == "result"
    )
    verified_integrity = result_artifact["integrity"]
    assert client.get(result_artifact["url"]).status_code == 200

result_path = next((data_root / "runs" / workflow_id).rglob("result.json"))
result_path.write_text("tampered\n", encoding="utf-8")

third_app = create_app()
with TestClient(third_app) as client:
    tampered = client.get(f"/api/workflows/{workflow_id}").json()
    tampered_artifact = next(
        item
        for item in tampered["tasks"][0]["artifacts"]
        if item["name"] == "result"
    )
    tampered_download_status = client.get(tampered_artifact["url"]).status_code
    assert client.delete(f"/api/workflows/{workflow_id}").status_code == 204

fourth_app = create_app()
with TestClient(fourth_app) as client:
    remaining_workflows = client.get("/api/workflows?limit=10").json()["total"]

result = {
    "package_path": str(package_path),
    "version": aerospace_simulator.__version__,
    "health_status": health.json()["status"],
    "scenario_count": len(catalog.json()["scenarios"]),
    "scenarios_path": str(scenarios),
    "runs_root": runs_root,
    "workspace_path": str(workspace_path.resolve()),
    "restored_status": restored["status"],
    "verified_integrity": verified_integrity,
    "tampered_integrity": tampered_artifact["integrity"],
    "tampered_download_status": tampered_download_status,
    "remaining_workflows": remaining_workflows,
    "tudat_python": str(runtime_paths()[0]),
}
print(json.dumps(result))
"""


if __name__ == "__main__":
    raise SystemExit(main())
