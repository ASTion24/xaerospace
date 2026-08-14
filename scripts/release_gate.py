from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATE_EPOCH = "1704067200"
BUILD_REQUIREMENTS = {
    "setuptools": "80.9.0",
    "wheel": "0.45.1",
}
REQUIRED_WHEEL_SUFFIXES = (
    "aerospace_simulator/_version.py",
    "aerospace_simulator/parameter_definitions.json",
    "share/wms-aerospace/config/providers.example.json",
    "share/wms-aerospace/config/tudatpy-macos-arm64-lock.txt",
    "share/wms-aerospace/scripts/setup_tudatpy_macos_arm64.sh",
    "share/wms-aerospace/web/app.js",
    "share/wms-aerospace/web/i18n.js",
    "share/wms-aerospace/web/index.html",
    "share/wms-aerospace/web/styles.css",
)


def main() -> int:
    _check_build_environment()
    _run([sys.executable, "-m", "ruff", "check", "."])
    _run([sys.executable, "-m", "ruff", "format", "--check", "."])
    _run([sys.executable, "-m", "pytest", "-q"])

    with (
        tempfile.TemporaryDirectory(prefix="wms-release-a-") as first_dir,
        tempfile.TemporaryDirectory(prefix="wms-release-b-") as second_dir,
    ):
        first = _build_wheel(Path(first_dir))
        second = _build_wheel(Path(second_dir))
        first_digest = _sha256(first)
        second_digest = _sha256(second)
        if first_digest != second_digest:
            raise RuntimeError(
                "wheel builds are not reproducible under SOURCE_DATE_EPOCH"
            )
        _inspect_wheel(first)
        _smoke_test_wheel(first)
        output_directory = PROJECT_ROOT / "dist"
        output_directory.mkdir(exist_ok=True)
        output = output_directory / first.name
        shutil.copy2(first, output)

    print(f"release gate passed: {output}")
    print(f"sha256: {first_digest}")
    return 0


def _check_build_environment() -> None:
    for package, expected in BUILD_REQUIREMENTS.items():
        try:
            actual = version(package)
        except PackageNotFoundError as exc:
            raise RuntimeError(
                f"missing release dependency {package}=={expected}; "
                "install this project with the release extra"
            ) from exc
        if actual != expected:
            raise RuntimeError(
                f"release dependency {package} must be {expected}, found {actual}"
            )


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )


def _build_wheel(destination: Path) -> Path:
    adjacent_uv = Path(sys.executable).with_name("uv")
    uv = shutil.which("uv") or (str(adjacent_uv) if adjacent_uv.is_file() else None)
    if uv is None:
        raise RuntimeError("uv is required to build the release wheel")
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
        }
    )
    _run(
        [
            uv,
            "build",
            "--wheel",
            "--out-dir",
            str(destination),
        ],
        env=environment,
    )
    wheels = list(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {len(wheels)}")
    return wheels[0]


def _inspect_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    missing = [
        suffix
        for suffix in REQUIRED_WHEEL_SUFFIXES
        if not any(name.endswith(suffix) for name in names)
    ]
    if missing:
        raise RuntimeError("wheel is missing required resources: " + ", ".join(missing))
    if any(name.endswith("parameter-guide.js") for name in names):
        raise RuntimeError("wheel still contains the removed parameter-guide.js")
    if any(name.endswith("providers.local.json") for name in names):
        raise RuntimeError("wheel contains a local Provider configuration")
    scenario_count = sum(
        "/share/wms-aerospace/scenarios/" in f"/{name}" and name.endswith(".json")
        for name in names
    )
    if scenario_count != 17:
        raise RuntimeError(
            f"wheel must contain 17 bundled scenarios, found {scenario_count}"
        )


def _smoke_test_wheel(path: Path) -> None:
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "wheel_smoke.py"),
            str(path),
        ]
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
