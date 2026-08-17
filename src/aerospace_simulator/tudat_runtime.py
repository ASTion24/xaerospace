from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .paths import default_tudat_runtime, runtime_working_directory

TUDATPY_VERSION = "1.0.0"
_REQUIRED_RESOURCE_FILES = (
    "quadrature/gaussianNodes.txt",
    "quadrature/gaussianWeights.txt",
    "station_locations/glo.sit",
    "station_locations/glo.vel",
    "station_locations/ns_codes.dat",
)


class TudatRuntimeUnavailableError(RuntimeError):
    """Raised when the isolated TudatPy runtime is unavailable."""


def project_root() -> Path:
    return runtime_working_directory(module_file=__file__)


def runtime_paths() -> tuple[Path, Path]:
    return default_tudat_runtime(environ=os.environ, module_file=__file__)


def runtime_environment(
    runtime_home: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if environ is None else environ)
    environment["HOME"] = str(runtime_home)
    environment["PYTHONNOUSERSITE"] = "1"
    if os.name == "nt":
        environment["USERPROFILE"] = str(runtime_home)
    return environment


def validate_runtime(python_executable: Path, runtime_home: Path) -> None:
    if not python_executable.is_file():
        raise TudatRuntimeUnavailableError(
            "TudatPy Python runtime not found at "
            f"{python_executable}. Set XAEROSPACE_TUDATPY_PYTHON or run the "
            "documented TudatPy environment setup."
        )
    resource_root = runtime_home / ".tudat" / "resource"
    missing = [
        relative_path
        for relative_path in _REQUIRED_RESOURCE_FILES
        if not (resource_root / relative_path).is_file()
    ]
    if missing:
        raise TudatRuntimeUnavailableError(
            "TudatPy minimal resources are missing under "
            f"{resource_root}: {', '.join(missing)}"
        )
