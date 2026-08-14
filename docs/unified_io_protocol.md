# Unified Simulation I/O Protocol

Protocol version: 1

This protocol is the stable boundary between product code and simulation
libraries. RocketPy, TudatPy, JSBSim, Basilisk, and future backends must be
integrated through adapters that implement this boundary.

Backend-native classes, state containers, callbacks, and units must never cross
the boundary.

## Request envelope

Every task enters the backend registry as a `SimulationRequest`:

```json
{
  "protocol_version": 1,
  "request_id": "mission-001",
  "label": "Single-stage sounding rocket",
  "description": "Demonstration flight",
  "task_kind": "single_stage_rigid_body_6dof",
  "contract_schema": "wms.aerospace.scenario.v1",
  "backend_preference": "auto",
  "contract": {}
}
```

The envelope is stable. The `contract` is a strictly typed, versioned payload
selected by `task_kind` and `contract_schema`.

The backend preference may be:

- a concrete backend id such as `rocketpy`;
- `auto`, allowing capability-based selection;
- null, equivalent to `auto`.

Automatic selection succeeds only when exactly one backend supports the task
and contract. Zero candidates and multiple candidates are errors.

## Backend capabilities

Each adapter declares:

```json
{
  "backend_id": "rocketpy",
  "backend_name": "RocketPy",
  "backend_version": "1.13.0",
  "supported_task_kinds": [
    "single_stage_point_mass_3dof",
    "single_stage_point_mass_3dof_recovery",
    "single_stage_rigid_body_6dof",
    "single_stage_rigid_body_6dof_recovery"
  ],
  "supported_contract_schemas": [
    "wms.aerospace.scenario.v1"
  ]
}
```

TudatPy declares a separate capability surface:

```json
{
  "backend_id": "tudatpy",
  "backend_name": "TudatPy",
  "backend_version": "1.0.0",
  "supported_task_kinds": [
    "earth_orbit_two_body",
    "earth_orbit_j2"
  ],
  "supported_contract_schemas": [
    "wms.aerospace.orbit_propagation.v2"
  ],
  "supported_family_ids": [
    "orbit_propagation"
  ],
  "supported_component_ids": [
    "orbit.gravity.point_mass",
    "orbit.gravity.spherical_harmonic_j2",
    "orbit.environment.exponential_atmosphere",
    "orbit.force.aerodynamic_drag",
    "orbit.propagator.rk4_fixed"
  ]
}
```

JSBSim declares a third, non-overlapping capability surface:

```json
{
  "backend_id": "jsbsim",
  "backend_name": "JSBSim",
  "backend_version": "1.3.1",
  "supported_task_kinds": [
    "fixed_wing_trimmed_6dof"
  ],
  "supported_contract_schemas": [
    "wms.aerospace.aircraft_flight.v1"
  ]
}
```

Basilisk declares a fourth, non-overlapping capability surface:

```json
{
  "backend_id": "basilisk",
  "backend_name": "Basilisk",
  "backend_version": "2.11.0",
  "supported_task_kinds": [
    "spacecraft_inertial_pointing_gnc",
    "spacecraft_rate_damping_gnc"
  ],
  "supported_contract_schemas": [
    "wms.aerospace.spacecraft_attitude.v1"
  ]
}
```

The backend registry performs exact schema and task matching.
`TaskFamilyRegistry` then resolves the contract fields to exactly one variant
and verifies that every selected component is declared by the backend. An
adapter must not reinterpret or downgrade an unsupported request.

When a backend requires a different Python distribution, the same boundary
applies across a subprocess. The TudatPy adapter exchanges plain JSON with its
isolated Conda worker and validates version, task kind, frame, dimensions,
sample count, and time axis before constructing the normalized result.

## Normalized result

Every backend returns a `UnifiedSimulationResult`:

```json
{
  "protocol_version": 1,
  "request": {},
  "backend": {},
  "time": {
    "name": "time",
    "unit": "s",
    "values": [],
    "sample_count": 0
  },
  "channels": [],
  "events": [],
  "metrics": [],
  "model_manifest": {},
  "diagnostics": []
}
```

### Channels

Each time-series channel declares:

- `name`: stable machine identifier;
- `quantity`: physical quantity;
- `unit`: explicit SI or documented display unit;
- `frame`: coordinate frame or semantic reference;
- `values`: one value for every point on the shared time axis.

Example:

```json
{
  "name": "omega3",
  "quantity": "angular_velocity",
  "unit": "rad/s",
  "frame": "body",
  "values": []
}
```

The protocol rejects:

- duplicate channel names;
- non-finite values;
- channel lengths different from the time axis;
- non-monotonic time;
- missing units or frames.

### Events

Events contain a stable name, time in seconds, and typed attributes:

```json
{
  "name": "apogee",
  "time_s": 12.53,
  "attributes": {
    "altitude_agl_m": 630.88,
    "horizontal_range_m": 129.02
  }
}
```

### Metrics

Scalar metrics always include name, value, and unit. Consumers must look up
metrics by name rather than array position.

### Model manifest

The model manifest records:

- states and initial conditions;
- governing equations;
- instantiated parameters;
- event conditions;
- assumptions and limitations;
- backend implementation references.

It is part of the normalized result, not a backend-specific side channel.

## Artifacts

Every run emits:

- `request.json`: normalized request envelope and typed contract;
- `result.json`: complete normalized result, including samples;
- `summary.json`: the same protocol without channel samples;
- `model_manifest.json`: normalized model provenance;
- task-specific views such as CSV and plots.

Views consume normalized channels and metrics. They must not access backend
native objects.

## Adapter acceptance checklist

A new backend is not considered integrated until tests prove:

1. Its capability declaration is exact.
2. Unsupported contracts fail without fallback.
3. All channels have units and frames.
4. Time and channel validation passes.
5. Events and metrics use stable names.
6. The model manifest exposes equations and assumptions.
7. `result.json` can be consumed without importing the backend library.
8. Existing backend protocol tests still pass.

## Versioning

Breaking envelope or normalized-result changes require a new
`protocol_version`.

Task-specific schema changes require a new `contract_schema`. Adding a new task
kind does not by itself require a protocol-version change.
