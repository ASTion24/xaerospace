from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import sysconfig
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from ._version import __version__
from .aircraft_backend import (
    JSBSimBackendUnavailableError,
    JSBSimExecutionError,
)
from .attitude_backend import (
    BasiliskBackendUnavailableError,
    BasiliskExecutionError,
)
from .config import ScenarioValidationError
from .orbit_backend import (
    TudatPyBackendUnavailableError,
    TudatPyExecutionError,
)
from .outputs import write_outputs
from .paths import XAEROSPACE_HOME_ENV, source_project_root
from .protocol import ProtocolValidationError
from .provider_config import (
    PROVIDER_CONFIG_ENV,
    PROVIDER_PROFILE_ENV,
    discover_local_provider_config,
)
from .registry import BackendRegistryError
from .request_io import load_request
from .simulation import (
    BackendUnavailableError,
    SimulationExecutionError,
    run_request,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xaerospace",
        description="Backend-routed aerospace simulation with normalized I/O.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    simulate_parser = subparsers.add_parser(
        "simulate",
        help="Run a scenario or normalized request and generate result artifacts.",
    )
    simulate_parser.add_argument("scenario", type=Path)
    simulate_parser.add_argument(
        "--output",
        type=Path,
        help="Output directory. Defaults to outputs/<scenario-file-name>.",
    )
    web_parser = subparsers.add_parser(
        "web",
        help="Start the aerospace workflow API and browser application.",
    )
    web_parser.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8000)
    web_parser.add_argument(
        "--runs-dir",
        type=Path,
        help="Run artifact directory. Defaults to the Xaerospace user data directory.",
    )
    web_parser.add_argument("--no-browser", action="store_true")
    _add_provider_arguments(web_parser)
    setup_parser = subparsers.add_parser(
        "setup-tudatpy",
        help="Install the pinned TudatPy runtime on macOS arm64.",
    )
    setup_parser.add_argument(
        "--data-dir",
        type=Path,
        help="Xaerospace data root. Defaults to the per-user application directory.",
    )
    evaluation_parser = subparsers.add_parser(
        "assistant-eval",
        help="Run the natural-language contract-drafting evaluation set.",
    )
    evaluation_parser.add_argument(
        "--cases",
        type=Path,
        help="Evaluation JSON file. Defaults to the bundled v1 set.",
    )
    evaluation_parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/assistant_evaluation.json"),
        help="Evaluation report path.",
    )
    evaluation_parser.add_argument("--limit", type=int)
    evaluation_parser.add_argument(
        "--tag",
        action="append",
        dest="tags",
        help="Run cases matching this tag. May be repeated.",
    )
    evaluation_parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Maximum concurrent evaluation cases (1-8).",
    )
    _add_provider_arguments(evaluation_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"web", "assistant-eval"}:
        try:
            _configure_provider_environment(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.command == "web":
        result = _run_web(args)
        return result
    if args.command == "assistant-eval":
        return _run_assistant_evaluation(args)
    if args.command == "setup-tudatpy":
        return _run_tudatpy_setup(args)

    output_dir = args.output or Path("outputs") / args.scenario.stem
    try:
        request = load_request(args.scenario)
        result = run_request(request)
        artifacts = write_outputs(result, output_dir)
    except (
        ScenarioValidationError,
        BackendUnavailableError,
        BasiliskBackendUnavailableError,
        BasiliskExecutionError,
        JSBSimBackendUnavailableError,
        JSBSimExecutionError,
        SimulationExecutionError,
        TudatPyBackendUnavailableError,
        TudatPyExecutionError,
        ProtocolValidationError,
        BackendRegistryError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = {
        "protocol_version": result.protocol_version,
        "request_id": result.request.request_id,
        "scenario": result.request.label,
        "dynamics": result.request.task_kind,
        "backend": (f"{result.backend.backend_name} {result.backend.backend_version}"),
        "metrics": {
            metric.name: {
                "value": round(metric.value, 9),
                "unit": metric.unit,
            }
            for metric in result.metrics
        },
        "artifacts": {name: str(path) for name, path in artifacts.items()},
    }
    print(json.dumps(payload, indent=2))
    return 0


def _add_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider-config",
        type=Path,
        help=(
            "Versioned Provider JSON. Defaults to config/providers.local.json "
            "or ~/.config/wms-aerospace/providers.local.json when present."
        ),
    )
    parser.add_argument(
        "--provider-profile",
        help="Named Provider profile. Defaults to active_provider in the JSON file.",
    )


def _loopback_host(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"127.0.0.1", "localhost", "::1"}:
        raise argparse.ArgumentTypeError(
            "Xaerospace Studio only binds to a loopback address: "
            "127.0.0.1, localhost, or ::1"
        )
    return normalized


def _configure_provider_environment(args: argparse.Namespace) -> None:
    explicit_path = getattr(args, "provider_config", None)
    if explicit_path is not None:
        resolved = explicit_path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"provider configuration file not found: {resolved}")
        os.environ[PROVIDER_CONFIG_ENV] = str(resolved)
    elif not os.environ.get(PROVIDER_CONFIG_ENV, "").strip():
        discovered = discover_local_provider_config(
            project_root=source_project_root(),
        )
        if discovered is not None:
            os.environ[PROVIDER_CONFIG_ENV] = str(discovered)

    profile = getattr(args, "provider_profile", None)
    if profile:
        os.environ[PROVIDER_PROFILE_ENV] = profile


def _run_web(args: argparse.Namespace) -> int:
    import uvicorn

    if args.runs_dir is not None:
        os.environ["WMS_AEROSPACE_RUN_DIR"] = str(args.runs_dir.resolve())
    browser_host = f"[{args.host}]" if args.host == "::1" else args.host
    url = f"http://{browser_host}:{args.port}"
    if not args.no_browser:
        timer = threading.Timer(1.2, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    print(f"Xaerospace Studio: {url}")
    uvicorn.run(
        "aerospace_simulator.web_api:app",
        host=args.host,
        port=args.port,
        reload=False,
    )
    return 0


def _run_tudatpy_setup(args: argparse.Namespace) -> int:
    setup_script = _bundled_resource(
        "scripts",
        "setup_tudatpy_macos_arm64.sh",
    )
    environment = os.environ.copy()
    if args.data_dir is not None:
        environment[XAEROSPACE_HOME_ENV] = str(args.data_dir.expanduser().resolve())
    completed = subprocess.run(
        ["/bin/bash", str(setup_script)],
        check=False,
        env=environment,
    )
    return completed.returncode


def _run_assistant_evaluation(args: argparse.Namespace) -> int:
    from .assistant import (
        AssistantService,
        AssistantUnavailableError,
        provider_from_configuration,
    )
    from .assistant_evaluation import (
        load_evaluation_set,
        run_evaluation,
    )
    from .simulation import create_default_registry
    from .task_families import create_default_task_family_registry
    from .web_api import (
        _build_task_family_catalog,
        _load_scenario_catalog,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.output.with_suffix(f"{args.output.suffix}.partial.jsonl")
    checkpoint_path.unlink(missing_ok=True)

    try:
        provider = provider_from_configuration()
        if provider is None:
            raise AssistantUnavailableError("assistant provider is not configured")
        cases_path = args.cases or _bundled_resource(
            "evaluation",
            "assistant_cases.json",
        )
        scenarios_directory = _bundled_resource("scenarios")
        evaluation_set = load_evaluation_set(cases_path)
        backend_registry = create_default_registry()
        family_registry = create_default_task_family_registry()
        family_registry.validate_backend_capabilities(backend_registry.capabilities())
        scenario_catalog = _load_scenario_catalog(scenarios_directory)
        task_family_catalog = _build_task_family_catalog(
            scenario_catalog,
            family_registry,
            backend_registry,
        )
        service = AssistantService(
            task_family_catalog=task_family_catalog,
            family_registry=family_registry,
            backend_registry=backend_registry,
            provider=provider,
        )

        async def execute():
            async def checkpoint(result):
                line = result.model_dump_json() + "\n"

                def append() -> None:
                    with checkpoint_path.open("a", encoding="utf-8") as stream:
                        stream.write(line)

                await asyncio.to_thread(append)

            try:
                return await run_evaluation(
                    service=service,
                    evaluation_set=evaluation_set,
                    concurrency=args.concurrency,
                    limit=args.limit,
                    tags=set(args.tags or []),
                    on_result=checkpoint,
                )
            finally:
                await service.aclose()

        report = asyncio.run(execute())
    except (AssistantUnavailableError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    args.output.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    checkpoint_path.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "evaluation_set": report.evaluation_set,
                "provider_id": report.provider_id,
                "model": report.model,
                "report": str(args.output),
                **report.summary.model_dump(mode="json"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if report.summary.passed_cases == report.summary.total_cases else 3


def _bundled_resource(directory: str, filename: str | None = None) -> Path:
    project_root = source_project_root()
    if project_root is not None:
        source = project_root / directory
        source = source / filename if filename is not None else source
        if source.exists():
            return source
    installed = Path(sysconfig.get_path("data")) / "share" / "wms-aerospace" / directory
    installed = installed / filename if filename is not None else installed
    if installed.exists():
        return installed
    raise ValueError(f"bundled resource not found: {directory}/{filename or ''}")
