import sys
from types import SimpleNamespace

import pytest

from aerospace_simulator import __version__
from aerospace_simulator.cli import _run_tudatpy_setup, _run_web, build_parser


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_web_command_accepts_only_loopback_hosts(host):
    parsed = build_parser().parse_args(["web", "--host", host, "--no-browser"])

    assert parsed.host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com"])
def test_web_command_rejects_non_loopback_hosts(host):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["web", "--host", host, "--no-browser"])

    assert error.value.code == 2


def test_cli_version_uses_package_version(capsys):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["--version"])

    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == f"xaerospace {__version__}"


def test_web_command_uses_application_factory(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(
            run=lambda target, **options: calls.append((target, options)),
        ),
    )
    args = build_parser().parse_args(["web", "--no-browser"])

    assert _run_web(args) == 0
    assert calls == [
        (
            "aerospace_simulator.web_api:create_app",
            {
                "host": "127.0.0.1",
                "port": 8000,
                "factory": True,
                "reload": False,
            },
        )
    ]


def test_tudat_setup_command_uses_cross_platform_installer(tmp_path, monkeypatch):
    calls = []
    runtime_python = tmp_path / "runtime" / "env" / "python"
    runtime_home = tmp_path / "runtime" / "home"
    monkeypatch.setattr(
        "aerospace_simulator.cli.install_tudat_runtime",
        lambda **options: calls.append(options) or (runtime_python, runtime_home),
    )
    args = build_parser().parse_args(
        ["setup-tudatpy", "--data-dir", str(tmp_path / "data")]
    )

    assert _run_tudatpy_setup(args) == 0
    assert calls == [{"data_root": tmp_path / "data"}]
