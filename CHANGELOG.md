# Changelog

## Unreleased

- Added native user-data, Provider-config, virtual-environment, and executable
  path handling for Linux x86_64 and Windows x86_64.
- Replaced the macOS-only TudatPy Bash setup with a checksum-pinned Python
  installer for macOS arm64, Linux x86_64, and Windows x86_64.
- Added explicit TudatPy 1.0.0 Conda locks for Linux and Windows.
- Expanded CI to test Linux and Windows on Python 3.10-3.12 and to execute the
  complete four-backend physics gate on Python 3.12 for all supported systems.
- Made release-wheel construction and installed-wheel smoke tests portable
  across POSIX and Windows virtual-environment layouts.

## 0.2.2

- Removed all pre-release compatibility aliases and standardized runtime
  configuration on `XAEROSPACE_*`.
- Standardized contract, task-family, workflow, provenance, JSON Schema
  extension, browser-storage, and evaluation identifiers on `xaerospace`.
- Removed the alternate CLI entry point and moved wheel resources to
  `share/xaerospace`.
- Added fail-closed tests for removed environment variables and contract
  schemas, plus wheel inspection that rejects removed namespace tokens.
- Removed the stale v0.2.0 PDF from the current branch; the v0.2.2 Markdown
  manual remains the authoritative documentation source.

## 0.2.1

- Removed import-time FastAPI and SQLite initialization; Uvicorn now invokes
  `create_app` through its application-factory mode.
- Normalized workspace directory, connection, migration, read, update, and
  deletion failures as `WorkspaceDatabaseError`.
- Added regression coverage proving that importing `web_api` does not create
  the configured user-data directory.
- Expanded the installed-wheel smoke test to execute, restore, verify, corrupt,
  reject, delete, and re-check a persisted RocketPy workflow.
- Corrected the manual's stale statement about `WorkflowStore` persistence.

## 0.2.0

- Added a SQLite-backed durable workspace without replacing the existing
  `WorkflowStore` or file-based result layout.
- Restored completed workflows and verified artifacts after service restarts.
- Marked unfinished work as `interrupted` after a process stop; no task is
  resumed or retried automatically.
- Added SHA-256 artifact integrity metadata and download-time verification.
- Added paginated workflow history, status filtering, and terminal-workflow
  deletion APIs.
- Added a compact bilingual history panel with refresh recovery, explicit open,
  and confirmed deletion actions.
- Added process-level crash recovery, schema-version, path-boundary, integrity,
  deletion, API, and browser acceptance coverage.

## 0.1.2

- Moved installed runtime state, workflow artifacts, and the isolated TudatPy
  environment out of package directories and into the per-user Xaerospace data
  directory.
- Added `xaerospace setup-tudatpy` with a pinned macOS arm64 package lock,
  micromamba checksum, and Tudat resource checksums.
- Restricted Xaerospace Studio to loopback bind addresses until an authenticated
  remote service mode exists.
- Established `_version.py` as the single package, CLI, API, wheel, and manual
  version source.
- Added a temporary wheel-install smoke test covering CLI, Web health, bundled
  resources, run storage, and TudatPy path derivation.
- Added GitHub Actions quality and four-backend release gates.
- Standardized the default Studio port on `8000`.
- Pinned Mermaid `11.12.0` for reproducible manual rendering.

## 0.1.1

- First public release of Xaerospace.
- Published the Xaerospace Studio brand and `xaerospace` CLI.
- Integrated RocketPy, TudatPy, JSBSim, and Basilisk behind one typed protocol.
- Published 5 task families, 16 real backend variants, 17 reference scenarios,
  the bilingual Web Studio, model/equation reports, and physical plots.
- Published Assistant-led IntentIR, capability matching, contract synthesis,
  multi-turn DraftSession, explicit execution confirmation, workflow
  export/replay, and persistent Assistant provenance.
- Published the verified two-stage launch-to-orbit mission and
  RocketPy-to-TudatPy handover.
- Added versioned, named LLM Provider profiles.
- Added CLI selection through `--provider-config` and `--provider-profile`.
- Added arbitrary OpenAI-compatible endpoints, models, request paths, model
  discovery paths, Bearer authentication, and environment-backed custom
  headers.
- Added automatic discovery of ignored local Provider configurations.
- Added a secret-free packaged example configuration.
- Moved the previously used GLM endpoint and model into an ignored,
  permission-restricted local initial profile.
- Preserved fail-closed behavior: an invalid selected profile never falls back
  to another profile or direct environment configuration.

## 0.1.0

- Internal functional-freeze and acceptance baseline.
- Not published as a public release.
