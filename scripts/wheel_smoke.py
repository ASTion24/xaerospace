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
    environment.pop("WMS_AEROSPACE_HOME", None)
    environment.pop("WMS_AEROSPACE_RUN_DIR", None)
    environment.pop("WMS_TUDATPY_PYTHON", None)
    environment.pop("WMS_TUDAT_HOME", None)
    return environment


SMOKE_PROGRAM = r"""
import json
from pathlib import Path

import aerospace_simulator
from fastapi.testclient import TestClient

from aerospace_simulator.cli import _bundled_resource, build_parser
from aerospace_simulator.tudat_runtime import runtime_paths
from aerospace_simulator.web_api import create_app

package_path = Path(aerospace_simulator.__file__).resolve()
assert "site-packages" in str(package_path), package_path
assert build_parser().parse_args(["web", "--no-browser"]).port == 8000
assert build_parser().parse_args(["setup-tudatpy"]).command == "setup-tudatpy"
scenarios = _bundled_resource("scenarios")
setup_script = _bundled_resource("scripts", "setup_tudatpy_macos_arm64.sh")
assert setup_script.is_file()
app = create_app()
with TestClient(app) as client:
    health = client.get("/api/health")
    catalog = client.get("/api/scenarios")
    assert client.get("/").status_code == 200
result = {
    "package_path": str(package_path),
    "version": aerospace_simulator.__version__,
    "health_status": health.json()["status"],
    "scenario_count": len(catalog.json()["scenarios"]),
    "scenarios_path": str(scenarios),
    "runs_root": str(app.state.workflow_store.runs_root),
    "tudat_python": str(runtime_paths()[0]),
}
print(json.dumps(result))
"""


if __name__ == "__main__":
    raise SystemExit(main())
