from __future__ import annotations

import hashlib
import platform
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .paths import environment_python, source_project_root, user_data_root
from .tudat_runtime import TUDATPY_VERSION, runtime_environment

MICROMAMBA_VERSION = "2.3.3"
MICROMAMBA_BASE_URL = "https://micro.mamba.pm/api/micromamba"
RESOURCE_BASE_URL = (
    "https://raw.githubusercontent.com/tudat-team/tudat-resources/v2.4/resource"
)
RESOURCE_FILES = {
    "quadrature/gaussianNodes.txt": (
        "efc979aeda2e60cc2198b4674807d6baf82350040275c7ed19a925cb8b1c2308"
    ),
    "quadrature/gaussianWeights.txt": (
        "39f67badb1a3ac7d4c38f4b156c3217cb13e81f9478c533dd684e56f88c582e3"
    ),
    "station_locations/glo.sit": (
        "8b2b4581afba85e6bbe963215830eeed7ad845bf7cc2af01beaa09a3057e9329"
    ),
    "station_locations/glo.vel": (
        "f0dba59765f511b16a504538c55411ea80a0c4775b752fe9a9f1cefd6ae7da6d"
    ),
    "station_locations/ns_codes.dat": (
        "68b245efea8ccb82bba8739c6901072816351a1c2cad4682c7c244e868f2ce03"
    ),
}


class TudatSetupError(RuntimeError):
    """Raised when the pinned TudatPy runtime cannot be installed safely."""


@dataclass(frozen=True)
class TudatPlatform:
    conda_subdir: str
    lock_filename: str
    micromamba_member: str
    micromamba_sha256: str
    executable_name: str


_PLATFORMS = {
    ("darwin", "arm64"): TudatPlatform(
        conda_subdir="osx-arm64",
        lock_filename="tudatpy-macos-arm64-lock.txt",
        micromamba_member="bin/micromamba",
        micromamba_sha256=(
            "bd5b8d3f151c3ad2d92f0fb918806543d713c7f67895c020db4041097a7f004d"
        ),
        executable_name="micromamba",
    ),
    ("linux", "x86_64"): TudatPlatform(
        conda_subdir="linux-64",
        lock_filename="tudatpy-linux-64-lock.txt",
        micromamba_member="bin/micromamba",
        micromamba_sha256=(
            "e7274528ceb9c20d048a428d6c22d7e02e268f8ffb762c4c365422347c8b8ba2"
        ),
        executable_name="micromamba",
    ),
    ("win32", "amd64"): TudatPlatform(
        conda_subdir="win-64",
        lock_filename="tudatpy-win-64-lock.txt",
        micromamba_member="Library/bin/micromamba.exe",
        micromamba_sha256=(
            "df531329cb3dd59d93f569bb597c715cc8ac239144718b5c530ca325d5d2e42d"
        ),
        executable_name="micromamba.exe",
    ),
}


def detect_tudat_platform(
    *,
    system_platform: str | None = None,
    machine: str | None = None,
) -> TudatPlatform:
    current_platform = (system_platform or sys.platform).lower()
    current_machine = (machine or platform.machine()).lower()
    if current_machine == "aarch64" and current_platform == "darwin":
        current_machine = "arm64"
    if current_machine == "amd64" and current_platform == "linux":
        current_machine = "x86_64"
    if current_machine in {"x64", "x86_64"} and current_platform.startswith("win"):
        current_machine = "amd64"
    if current_platform.startswith("win"):
        current_platform = "win32"
    try:
        return _PLATFORMS[(current_platform, current_machine)]
    except KeyError as exc:
        raise TudatSetupError(
            "TudatPy setup supports macOS arm64, Linux x86_64, and "
            "Windows x86_64; detected "
            f"{current_platform}/{current_machine}."
        ) from exc


def install_tudat_runtime(*, data_root: Path | None = None) -> tuple[Path, Path]:
    target_platform = detect_tudat_platform()
    root = (
        data_root.expanduser().resolve() if data_root is not None else user_data_root()
    )
    runtime_root = root / "runtime" / "tudat"
    tools_root = runtime_root / "tools"
    environment_root = runtime_root / "env"
    runtime_home = runtime_root / "home"
    mamba_root = runtime_root / "mamba-root"
    micromamba = tools_root / "bin" / target_platform.executable_name
    lock_file = _configuration_directory() / target_platform.lock_filename
    if not lock_file.is_file():
        raise TudatSetupError(f"pinned TudatPy lock file not found: {lock_file}")

    for directory in (micromamba.parent, mamba_root, runtime_home):
        directory.mkdir(parents=True, exist_ok=True)
    _ensure_micromamba(micromamba, target_platform)

    python_executable = environment_python(environment_root)
    command = "install" if python_executable.is_file() else "create"
    environment = runtime_environment(runtime_home)
    environment["MAMBA_ROOT_PREFIX"] = str(mamba_root)
    completed = subprocess.run(
        [
            str(micromamba),
            command,
            "-y",
            "-p",
            str(environment_root),
            "--file",
            str(lock_file),
        ],
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise TudatSetupError(
            f"micromamba failed to {command} the pinned TudatPy runtime"
        )

    resource_root = runtime_home / ".tudat" / "resource"
    for relative_path, expected_sha256 in RESOURCE_FILES.items():
        destination = resource_root / relative_path
        if not destination.is_file() or _sha256(destination) != expected_sha256:
            _download(
                f"{RESOURCE_BASE_URL}/{relative_path}",
                destination,
                expected_sha256=expected_sha256,
            )

    python_executable = environment_python(environment_root)
    validation = subprocess.run(
        [
            str(python_executable),
            "-c",
            (
                "import tudatpy; "
                "from tudatpy.kernel.dynamics import "
                "environment_setup, propagation_setup, simulator; "
                f"assert tudatpy.__version__ == {TUDATPY_VERSION!r}; "
                "assert environment_setup and propagation_setup and simulator"
            ),
        ],
        check=False,
        env=runtime_environment(runtime_home),
    )
    if validation.returncode != 0:
        raise TudatSetupError("the installed TudatPy runtime failed validation")
    return python_executable, runtime_home


def _configuration_directory() -> Path:
    source_root = source_project_root(__file__)
    if source_root is not None:
        return source_root / "config"
    return Path(sysconfig.get_path("data")) / "share" / "xaerospace" / "config"


def _ensure_micromamba(
    destination: Path,
    target_platform: TudatPlatform,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        version = subprocess.run(
            [str(destination), "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        if version.returncode == 0 and version.stdout.strip() == MICROMAMBA_VERSION:
            return

    with tempfile.TemporaryDirectory(prefix="xaerospace-micromamba-") as name:
        archive = Path(name) / "micromamba.tar.bz2"
        _download(
            (
                f"{MICROMAMBA_BASE_URL}/{target_platform.conda_subdir}/"
                f"{MICROMAMBA_VERSION}"
            ),
            archive,
            expected_sha256=target_platform.micromamba_sha256,
        )
        try:
            with tarfile.open(archive, mode="r:bz2") as package:
                member = package.getmember(target_platform.micromamba_member)
                stream = package.extractfile(member)
                if stream is None:
                    raise TudatSetupError(
                        "micromamba package did not contain its executable"
                    )
                temporary = destination.with_suffix(destination.suffix + ".download")
                with temporary.open("wb") as output:
                    while chunk := stream.read(1024 * 1024):
                        output.write(chunk)
        except (KeyError, OSError, tarfile.TarError) as exc:
            raise TudatSetupError("unable to extract micromamba safely") from exc
        temporary.chmod(
            temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        temporary.replace(destination)


def _download(url: str, destination: Path, *, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".download")
    request = Request(url, headers={"User-Agent": "Xaerospace-Tudat-Setup/1"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with (
                urlopen(request, timeout=120) as response,
                temporary.open("wb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            actual_sha256 = _sha256(temporary)
            if actual_sha256 != expected_sha256:
                raise TudatSetupError(
                    f"download checksum mismatch for {url}: {actual_sha256}"
                )
            temporary.replace(destination)
            return
        except (OSError, URLError, TudatSetupError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(1.0 + attempt)
    raise TudatSetupError(f"unable to download pinned resource: {url}") from last_error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
