#!/usr/bin/env bash

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "TudatPy setup currently supports macOS arm64 only." >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="${SCRIPT_DIR}/../config/tudatpy-macos-arm64-lock.txt"
DATA_ROOT="${XAEROSPACE_HOME:-${WMS_AEROSPACE_HOME:-${HOME}/Library/Application Support/Xaerospace}}"
RUNTIME_ROOT="${DATA_ROOT}/runtime/tudat"
TOOLS_DIR="${RUNTIME_ROOT}/tools"
MAMBA_ROOT="${RUNTIME_ROOT}/mamba-root"
ENV_PREFIX="${RUNTIME_ROOT}/env"
RUNTIME_HOME="${RUNTIME_ROOT}/home"
MICROMAMBA="${TOOLS_DIR}/bin/micromamba"
RESOURCE_ROOT="${RUNTIME_HOME}/.tudat/resource"
MICROMAMBA_VERSION="2.3.3"
MICROMAMBA_URL="https://github.com/mamba-org/micromamba-releases/releases/download/2.3.3-0/micromamba-osx-arm64"
MICROMAMBA_SHA256="aa23d0e01d6f492f43aa86720c0f4c8db91978b81f8af46a852f6c4fcf6737d5"
RESOURCE_BASE_URL="https://raw.githubusercontent.com/tudat-team/tudat-resources/v2.4/resource"

if [[ ! -f "${LOCK_FILE}" ]]; then
    echo "Pinned TudatPy lock file not found: ${LOCK_FILE}" >&2
    exit 2
fi

mkdir -p "${TOOLS_DIR}/bin" "${MAMBA_ROOT}" "${RUNTIME_HOME}"

if [[ ! -x "${MICROMAMBA}" ]] || \
    [[ "$("${MICROMAMBA}" --version 2>/dev/null || true)" != "${MICROMAMBA_VERSION}" ]]
then
    TEMP_DIR="$(mktemp -d)"
    trap 'rm -rf "${TEMP_DIR}"' EXIT
    curl -fsSL "${MICROMAMBA_URL}" -o "${TEMP_DIR}/micromamba"
    printf '%s  %s\n' \
        "${MICROMAMBA_SHA256}" \
        "${TEMP_DIR}/micromamba" \
        | shasum -a 256 -c -
    install -m 0755 "${TEMP_DIR}/micromamba" "${MICROMAMBA}"
fi

if [[ ! -x "${ENV_PREFIX}/bin/python" ]]; then
    HOME="${RUNTIME_HOME}" \
    MAMBA_ROOT_PREFIX="${MAMBA_ROOT}" \
    "${MICROMAMBA}" create -y \
        -p "${ENV_PREFIX}" \
        --file "${LOCK_FILE}"
else
    HOME="${RUNTIME_HOME}" \
    MAMBA_ROOT_PREFIX="${MAMBA_ROOT}" \
    "${MICROMAMBA}" install -y \
        -p "${ENV_PREFIX}" \
        --file "${LOCK_FILE}"
fi

while read -r expected_sha256 relative_path
do
    [[ -n "${expected_sha256}" ]] || continue
    destination="${RESOURCE_ROOT}/${relative_path}"
    mkdir -p "$(dirname "${destination}")"
    if [[ ! -s "${destination}" ]] || \
        ! printf '%s  %s\n' "${expected_sha256}" "${destination}" \
            | shasum -a 256 -c - >/dev/null 2>&1
    then
        temporary="${destination}.download"
        curl -fsSL \
            "${RESOURCE_BASE_URL}/${relative_path}" \
            -o "${temporary}"
        printf '%s  %s\n' "${expected_sha256}" "${temporary}" \
            | shasum -a 256 -c -
        mv "${temporary}" "${destination}"
    fi
done <<'RESOURCES'
efc979aeda2e60cc2198b4674807d6baf82350040275c7ed19a925cb8b1c2308 quadrature/gaussianNodes.txt
39f67badb1a3ac7d4c38f4b156c3217cb13e81f9478c533dd684e56f88c582e3 quadrature/gaussianWeights.txt
8b2b4581afba85e6bbe963215830eeed7ad845bf7cc2af01beaa09a3057e9329 station_locations/glo.sit
f0dba59765f511b16a504538c55411ea80a0c4775b752fe9a9f1cefd6ae7da6d station_locations/glo.vel
68b245efea8ccb82bba8739c6901072816351a1c2cad4682c7c244e868f2ce03 station_locations/ns_codes.dat
RESOURCES

HOME="${RUNTIME_HOME}" \
PYTHONNOUSERSITE=1 \
"${ENV_PREFIX}/bin/python" - <<'PY'
import tudatpy
from tudatpy.kernel.dynamics import environment_setup, propagation_setup, simulator

assert tudatpy.__version__ == "1.0.0"
assert environment_setup is not None
assert propagation_setup is not None
assert simulator is not None
print("TudatPy 1.0.0 isolated runtime is ready.")
PY

printf 'TudatPy runtime: %s\n' "${ENV_PREFIX}"
printf 'TudatPy resources: %s\n' "${RESOURCE_ROOT}"
