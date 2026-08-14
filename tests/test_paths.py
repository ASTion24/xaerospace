from pathlib import Path

from aerospace_simulator.paths import (
    default_runs_root,
    default_tudat_runtime,
    runtime_working_directory,
    source_project_root,
    user_data_root,
)


def _source_module(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    module = root / "src" / "aerospace_simulator" / "paths.py"
    module.parent.mkdir(parents=True)
    module.touch()
    (root / "pyproject.toml").touch()
    return module


def _installed_module(tmp_path: Path) -> Path:
    module = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "aerospace_simulator"
        / "paths.py"
    )
    module.parent.mkdir(parents=True)
    module.touch()
    return module


def test_user_data_root_uses_macos_application_support_and_explicit_override(
    tmp_path,
):
    default = user_data_root(environ={}, home=tmp_path, platform="darwin")
    overridden = user_data_root(
        environ={"XAEROSPACE_HOME": str(tmp_path / "custom")},
        home=tmp_path,
        platform="darwin",
    )

    assert default == tmp_path / "Library" / "Application Support" / "Xaerospace"
    assert overridden == (tmp_path / "custom").resolve()
    assert (
        default_runs_root(
            environ={},
            home=tmp_path,
            platform="darwin",
        )
        == default / "runs"
    )


def test_source_checkout_keeps_compatible_local_tudat_runtime(tmp_path):
    module = _source_module(tmp_path)
    root = module.parents[2]
    python = root / ".tudat-env" / "bin" / "python"
    resource = root / ".local-home" / ".tudat" / "resource"
    python.parent.mkdir(parents=True)
    python.touch()
    resource.mkdir(parents=True)

    resolved_python, resolved_home = default_tudat_runtime(
        environ={},
        home=tmp_path / "home",
        platform="darwin",
        module_file=module,
    )

    assert source_project_root(module) == root
    assert resolved_python == python
    assert resolved_home == root / ".local-home"
    assert runtime_working_directory(module_file=module) == root


def test_installed_wheel_paths_never_target_site_packages(tmp_path):
    module = _installed_module(tmp_path)
    home = tmp_path / "home"
    data_root = home / "Library" / "Application Support" / "Xaerospace"

    python, runtime_home = default_tudat_runtime(
        environ={},
        home=home,
        platform="darwin",
        module_file=module,
    )
    working_directory = runtime_working_directory(
        environ={},
        home=home,
        platform="darwin",
        module_file=module,
    )

    assert source_project_root(module) is None
    assert python == data_root / "runtime" / "tudat" / "env" / "bin" / "python"
    assert runtime_home == data_root / "runtime" / "tudat" / "home"
    assert working_directory == data_root
    assert "site-packages" not in str(python)
    assert "site-packages" not in str(working_directory)


def test_legacy_environment_overrides_remain_supported(tmp_path):
    python, runtime_home = default_tudat_runtime(
        environ={
            "WMS_TUDATPY_PYTHON": str(tmp_path / "python"),
            "WMS_TUDAT_HOME": str(tmp_path / "home"),
        },
        module_file=_installed_module(tmp_path),
    )

    assert python == (tmp_path / "python").resolve()
    assert runtime_home == (tmp_path / "home").resolve()


def test_explicit_xaerospace_home_takes_precedence_over_checkout_runtime(tmp_path):
    module = _source_module(tmp_path)
    root = module.parents[2]
    legacy_python = root / ".tudat-env" / "bin" / "python"
    legacy_python.parent.mkdir(parents=True)
    legacy_python.touch()
    data_root = tmp_path / "portable-data"

    python, runtime_home = default_tudat_runtime(
        environ={"XAEROSPACE_HOME": str(data_root)},
        module_file=module,
    )

    assert python == data_root / "runtime" / "tudat" / "env" / "bin" / "python"
    assert runtime_home == data_root / "runtime" / "tudat" / "home"
