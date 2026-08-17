import pytest

from aerospace_simulator.tudat_runtime import runtime_environment
from aerospace_simulator.tudat_setup import (
    RESOURCE_FILES,
    TudatSetupError,
    detect_tudat_platform,
)


@pytest.mark.parametrize(
    ("system_platform", "machine", "expected_subdir", "expected_executable"),
    [
        ("darwin", "arm64", "osx-arm64", "micromamba"),
        ("darwin", "aarch64", "osx-arm64", "micromamba"),
        ("linux", "x86_64", "linux-64", "micromamba"),
        ("win32", "AMD64", "win-64", "micromamba.exe"),
        ("win32", "x86_64", "win-64", "micromamba.exe"),
    ],
)
def test_tudat_setup_detects_supported_platforms(
    system_platform,
    machine,
    expected_subdir,
    expected_executable,
):
    result = detect_tudat_platform(
        system_platform=system_platform,
        machine=machine,
    )

    assert result.conda_subdir == expected_subdir
    assert result.executable_name == expected_executable
    assert len(result.micromamba_sha256) == 64


@pytest.mark.parametrize(
    ("system_platform", "machine"),
    [
        ("linux", "aarch64"),
        ("win32", "arm64"),
        ("darwin", "x86_64"),
    ],
)
def test_tudat_setup_rejects_unverified_platforms(system_platform, machine):
    with pytest.raises(TudatSetupError, match="supports"):
        detect_tudat_platform(
            system_platform=system_platform,
            machine=machine,
        )


def test_tudat_resource_manifest_is_checksum_pinned():
    assert set(RESOURCE_FILES) == {
        "quadrature/gaussianNodes.txt",
        "quadrature/gaussianWeights.txt",
        "station_locations/glo.sit",
        "station_locations/glo.vel",
        "station_locations/ns_codes.dat",
    }
    assert all(
        len(checksum) == 64
        and all(character in "0123456789abcdef" for character in checksum)
        for checksum in RESOURCE_FILES.values()
    )


def test_tudat_worker_environment_isolated_home(tmp_path):
    runtime_home = tmp_path / "runtime home"

    environment = runtime_environment(
        runtime_home,
        environ={"PATH": "test-path"},
    )

    assert environment["HOME"] == str(runtime_home)
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PATH"] == "test-path"
