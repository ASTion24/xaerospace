<p align="right">
  <a href="README.md">中文</a> | <strong>English</strong>
</p>

<div align="center">

# XAEROSPACE

**Orchestrate trustworthy aerospace simulations with natural language**

RocketPy · TudatPy · JSBSim · Basilisk

[Chinese User Manual](docs/v0.2.2_product_demo_zh.md) ·
[Unified Protocol](docs/unified_io_protocol.md) ·
[Provider Configuration](docs/provider_configuration.md)

</div>

![Xaerospace Studio](docs/assets/v0.1.1/studio_overview.jpg)

## What Is Xaerospace?

Xaerospace is an LLM-first, verification-backed aerospace simulation studio.
Users can describe a mission in one sentence or begin with a parameter form,
JSON contract, or reference scenario. The LLM interprets intent and drafts a
contract. Deterministic code enforces schemas, permissions, routing, and
fail-closed behavior. Real open-source physics backends perform the final
computation.

Xaerospace currently integrates four backends behind one interface:

| Backend | Capabilities |
|---|---|
| RocketPy 1.13.0 | Rocket 3DOF/6DOF flight, events, and parachute recovery |
| TudatPy 1.0.0 | Two-body, J2, and drag orbit propagation; two-stage launch to orbit |
| JSBSim 1.3.1 | Fixed-wing trim, nonlinear six-degree-of-freedom flight, and control response |
| Basilisk 2.11.0 | Spacecraft attitude, MRP control, and reaction-wheel dynamics |

There is no fallback physics backend. If the selected backend is unavailable,
the contract is unsupported, or the output violates its constraints, the task
fails explicitly. Xaerospace never substitutes a simplified model or fabricates
a successful result.

## Run in Ten Minutes

### 1. Install

Xaerospace supports Python 3.10 through 3.12. Python 3.12 is recommended.

With `uv`:

```bash
git clone https://github.com/ASTion24/xaerospace.git Xaerospace
cd Xaerospace
uv sync --extra test --python 3.12
```

With `venv` and `pip`:

```bash
python3.12 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test,release]"
```

TudatPy runs in a separate, locked Conda environment managed by Xaerospace.
On a supported platform, run:

```bash
xaerospace setup-tudatpy
```

Current four-backend support matrix:

| Platform | Architecture | Python | CI Gate |
|---|---|---|---|
| macOS | arm64 | 3.12 | Complete four-backend gate |
| Linux | x86_64 | 3.10-3.12 | Install and wheel on all versions; complete four-backend gate on 3.12 |
| Windows | x86_64 | 3.10-3.12 | Install and wheel on all versions; complete four-backend gate on 3.12 |

The TudatPy installer rejects unsupported architectures explicitly. It does not
download a mismatched binary environment.

### 2. Start the Studio

```bash
uv run xaerospace web
```

Your browser opens:

```text
http://127.0.0.1:8000
```

For a headless environment:

```bash
uv run xaerospace web --no-browser
```

Run artifacts and the isolated TudatPy environment are stored in the native
per-user data directory by default:

| Platform | Default Directory |
|---|---|
| macOS | `~/Library/Application Support/Xaerospace/` |
| Linux | `$XDG_DATA_HOME/xaerospace/` or `~/.local/share/xaerospace/` |
| Windows | `%LOCALAPPDATA%\Xaerospace\` |

Set `XAEROSPACE_HOME` to move the entire user-data root, or use
`xaerospace web --runs-dir` to move only run artifacts. The workflow index and
state are stored in `workspace.sqlite3`; large time-series results remain under
`runs/`.

### 3. Run Your First Task

1. Select `Single-stage Rocket 3DOF` under Reference Scenarios.
2. Review the populated parameters.
3. Click `Add to Workflow`.
4. Click `Run Workflow`.
5. Inspect the state timeline, events, metrics, equations, and plots.

You can also run a scenario without starting the web interface:

```bash
xaerospace simulate scenarios/single_stage_demo.json \
  --output outputs/single_stage_demo
```

## Persistent Mission Workspace

Confirmed workflows are stored durably. After restarting the service or
browser, the Studio can reopen task queues, results, and exported contracts
from History. A browser refresh restores the most recently viewed workflow.

An active task is never guessed to have succeeded and is never retried
automatically. If the service stops unexpectedly, unfinished workflows become
`interrupted`. The user reviews the context and decides whether to replay them
explicitly. Result artifacts carry SHA-256 digests that are verified before
download. Users can explicitly delete terminal workflows from History.

The persistence layer retains the existing `WorkflowStore` and file artifact
directory while adding a local SQLite index. `DraftSession` remains a
short-lived editing context and does not persist complete conversations.

## From One Sentence to Orbit

After configuring an LLM Provider, enter this in the AI compiler:

```text
Launch a 15,000 kg payload into a 220 km near-circular orbit with a two-stage launch vehicle.
```

The system processes it through the following boundary:

```text
Natural language
  -> IntentInterpreter
  -> CapabilityMatcher
  -> ContractSynthesizer
  -> User review and confirmation
  -> Strongly typed contract compilation
  -> TudatPy two-stage powered flight
  -> Post-insertion orbit verification
```

The LLM cannot run a simulation directly or modify locked fields. The server
submits the deterministic `DraftSession` revision only after the user clicks
`Confirm Contract and Run`.

The reference mission reaches a near-circular orbit at approximately 220 km.
It includes propellant depletion for both stages, staging, a pitch program, J2,
rotating-atmosphere drag, and 1,200 seconds of post-insertion verification.

## Provider Configuration

Copy the secret-free template:

```bash
cp config/providers.example.json config/providers.local.json
chmod 600 config/providers.local.json
```

Windows PowerShell users do not need to run `chmod`. Keep the local
configuration readable only by the current user and continue to supply secrets
through environment variables.

Set the API endpoint and model in the local file, and keep the API key in an
environment variable:

```json
{
  "schema_version": 1,
  "active_provider": "cloud",
  "providers": {
    "cloud": {
      "type": "openai_compatible",
      "base_url": "https://your-provider.example/v1",
      "model": "your-model",
      "api_key_env": "XAEROSPACE_PROVIDER_API_KEY",
      "compatibility_mode": "strict"
    }
  }
}
```

```bash
export XAEROSPACE_PROVIDER_API_KEY="..."
xaerospace web --provider-profile cloud
```

Git ignores `config/providers.local.json` and
`config/providers.*.local.json`. Never commit API keys, private endpoints, or
organization-specific headers.

Runtime configuration, installed resources, schemas, and browser storage all
use the `Xaerospace` namespace. The only command-line entry point is
`xaerospace`.

## Capability Catalog

Five task families provide sixteen real-backend variants:

| Task Family | Variants |
|---|---|
| `rocket_flight` | 3DOF, 3DOF recovery, 6DOF, 6DOF recovery |
| `launch_to_orbit` | Two-stage 220 km near-circular reference mission |
| `orbit_propagation` | Two-body, J2, J2 with atmospheric drag |
| `aircraft_flight` | C172P, C172R, C182, C310, J3 Cub |
| `spacecraft_gnc` | Inertial pointing, uncontrolled reference, rate damping |

The repository includes seventeen directly runnable reference scenarios. They
are examples of strongly typed contracts, not hard-coded demonstration
trajectories. Change parameters within the corresponding schema to generate a
new task.

## Unified Output

Every simulation returns the same versioned boundary:

- A shared timeline.
- State channels with units, physical quantities, and reference frames.
- Events and normalized metrics.
- Backend identity, version, and model inventory.
- Dynamics equations, parameters, assumptions, and limitations.
- JSON, CSV, Markdown, and PNG artifacts.
- Assistant provenance and workflow audit information.

Backend-native objects never cross the unified protocol. Unknown, conflicting,
or ambiguous backend selections fail explicitly.

## Common Commands

Start the web interface:

```bash
xaerospace web
```

Select a Provider:

```bash
xaerospace web \
  --provider-config config/providers.local.json \
  --provider-profile cloud
```

Run a scenario:

```bash
xaerospace simulate scenarios/earth_orbit_j2_demo.json \
  --output outputs/earth_orbit_j2_demo
```

Run the Assistant evaluation:

```bash
xaerospace assistant-eval \
  --provider-profile cloud \
  --output outputs/assistant_eval
```

Replay an exported request:

```bash
xaerospace simulate outputs/single_stage_demo/request.json \
  --output outputs/replayed_single_stage
```

## Architecture

```text
User input
  -> IntentIR / parameter form / JSON contract
  -> TaskFamilyRegistry
  -> BackendRegistry
  -> RocketPy | TudatPy | JSBSim | Basilisk
  -> WorkflowStore + SQLite durable index
  -> UnifiedSimulationResult
  -> States / events / metrics / equations / plots / audit artifacts
```

Cross-backend tasks use an explicit state-handover protocol. Xaerospace
currently supports strongly typed conversion from RocketPy flight results to a
TudatPy orbit state, recording the source state, target state, units, reference
frames, and transformation basis.

## Development and Verification

Run the test suite:

```bash
python -m pytest
```

Run static checks:

```bash
python -m ruff check .
```

Run the complete physics and packaging gate:

```bash
python scripts/release_gate.py
```

The complete gate requires RocketPy, TudatPy, JSBSim, and Basilisk.

Regenerate the PDF user manual from Markdown:

```bash
uv sync --extra docs --python 3.12
uv run python scripts/build_manual_pdf.py
```

The builder performs two-pass pagination, table-of-contents page-number
backfilling, Chinese font embedding, plot freezing, and PDF layout validation.
On macOS, it requires Google Chrome and the system Hiragino Sans GB,
Avenir Next, and Menlo fonts.

## Security Boundaries

- The Studio can bind only to `127.0.0.1`, `localhost`, or `::1`; it does not
  expose an unauthenticated LAN service.
- The LLM can submit only structured drafts and cannot execute tasks directly.
- The user must explicitly confirm the current revision.
- A Provider failure never falls back to another unselected Provider.
- A backend failure never falls back to another physics model.
- Interrupted tasks are never resumed or retried automatically.
- Run artifacts are verified with SHA-256 before download.
- Local Provider configuration is excluded from Git and release wheels.
- This project is not flight-certification, mission-safety, or operational
  guidance and control software.

## Documentation

- [Complete Chinese User Manual](docs/v0.2.2_product_demo_zh.md)
- [Provider Configuration](docs/provider_configuration.md)
- [Platform Support](docs/platform_support.md)
- [Unified Input/Output Protocol](docs/unified_io_protocol.md)
- [Two-stage Launch to Orbit](docs/two_stage_launch_to_orbit.md)
- [Cross-backend State Handover](docs/cross_backend_handover.md)

## License

Xaerospace is released under the MIT License. Each physics backend remains
subject to its own license.
