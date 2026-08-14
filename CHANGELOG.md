# Changelog

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
- Published the Xaerospace Studio brand and `xaerospace` CLI while retaining
  the previous command and environment-variable identifiers for compatibility.
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
- Retained the legacy `WMS_ASSISTANT_LLM_*` environment variables when no
  Provider JSON is selected.
- Preserved fail-closed behavior: an invalid selected profile never falls back
  to another profile or the legacy environment.

## 0.1.0

- Internal functional-freeze and acceptance baseline.
- Not published as a public release.
