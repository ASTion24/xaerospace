from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

XAEROSPACE_HOME_ENV = "XAEROSPACE_HOME"
LEGACY_HOME_ENV = "WMS_AEROSPACE_HOME"
RUN_DIR_ENV = "WMS_AEROSPACE_RUN_DIR"


def source_project_root(module_file: str | Path | None = None) -> Path | None:
    package_file = Path(module_file or __file__).resolve()
    candidate = package_file.parents[2]
    if (candidate / "pyproject.toml").is_file() and (
        candidate / "src" / "aerospace_simulator"
    ).is_dir():
        return candidate
    return None


def user_data_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    for variable in (XAEROSPACE_HOME_ENV, LEGACY_HOME_ENV):
        configured = environment.get(variable, "").strip()
        if configured:
            return Path(configured).expanduser().resolve()

    user_home = (home or Path.home()).expanduser()
    current_platform = platform or sys.platform
    if current_platform == "darwin":
        return user_home / "Library" / "Application Support" / "Xaerospace"

    xdg_data_home = environment.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home:
        return Path(xdg_data_home).expanduser().resolve() / "xaerospace"
    return user_home / ".local" / "share" / "xaerospace"


def default_runs_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    configured = environment.get(RUN_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        user_data_root(
            environ=environment,
            home=home,
            platform=platform,
        )
        / "runs"
    )


def default_tudat_runtime(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
    module_file: str | Path | None = None,
) -> tuple[Path, Path]:
    environment = os.environ if environ is None else environ
    python_override = environment.get("WMS_TUDATPY_PYTHON", "").strip()
    home_override = environment.get("WMS_TUDAT_HOME", "").strip()
    data_root_override = any(
        environment.get(variable, "").strip()
        for variable in (XAEROSPACE_HOME_ENV, LEGACY_HOME_ENV)
    )

    source_root = source_project_root(module_file)
    legacy_python = (
        source_root / ".tudat-env" / "bin" / "python"
        if source_root is not None
        else None
    )
    legacy_home = source_root / ".local-home" if source_root is not None else None
    data_root = user_data_root(
        environ=environment,
        home=home,
        platform=platform,
    )
    runtime_root = data_root / "runtime" / "tudat"

    python_executable = (
        Path(python_override).expanduser().resolve()
        if python_override
        else legacy_python
        if (
            not data_root_override
            and legacy_python is not None
            and legacy_python.is_file()
        )
        else runtime_root / "env" / "bin" / "python"
    )
    runtime_home = (
        Path(home_override).expanduser().resolve()
        if home_override
        else legacy_home
        if (
            not data_root_override
            and legacy_home is not None
            and (legacy_home / ".tudat" / "resource").is_dir()
        )
        else runtime_root / "home"
    )
    return python_executable, runtime_home


def runtime_working_directory(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
    module_file: str | Path | None = None,
) -> Path:
    source_root = source_project_root(module_file)
    if source_root is not None:
        return source_root
    return user_data_root(
        environ=environ,
        home=home,
        platform=platform,
    )
