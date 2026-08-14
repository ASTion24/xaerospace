# RocketPy to TudatPy State Handover

The workflow layer supports one explicit cross-backend transition:

```text
RocketPy local launch trajectory
  -> selected burnout or apogee state
  -> WGS84 Earth-fixed Cartesian state
  -> rotating-Earth J2000 Cartesian state
  -> osculating Keplerian elements
  -> TudatPy propagation contract
```

The transition is never inferred from task order. The TudatPy task must contain:

```json
{
  "task_id": "orbit",
  "document": {},
  "handover": {
    "type": "rocketpy_to_tudatpy",
    "source_task_id": "ascent",
    "source_event": "burnout",
    "launch_epoch_s_since_j2000": 0.0
  }
}
```

`launch_epoch_s_since_j2000` is the epoch of RocketPy time zero. The target
TudatPy epoch is:

```text
target_epoch = launch_epoch + source_event_time
```

## Frame conversion

RocketPy channels are required to declare:

- position east and north in `local_enu`;
- altitude in `above_launch_site`;
- velocity east, north, and up in `local_enu`.

The launch-site geodetic latitude, longitude, and elevation come from the
RocketPy contract. The adapter computes the WGS84 launch position and applies
the local ENU basis:

```text
r_ecef = r_launch,wgs84 + C_enu_to_ecef r_enu
v_ecef = C_enu_to_ecef v_enu
```

Earth rotation is then added before rotating into J2000:

```text
r_j2000 = R3(theta) r_ecef
v_j2000 = R3(theta) (v_ecef + omega_earth x r_ecef)
theta = theta_j2000 + omega_earth target_epoch
```

The Cartesian state is converted to osculating Keplerian elements using the
target contract's gravitational parameter. Those elements and the derived
epoch replace the target starter values. The resulting
`OrbitPropagationConfig` is parsed again before TudatPy is invoked.

## Failure behavior

The compiler rejects the transition when:

- the source is not a completed RocketPy result;
- the target is not a TudatPy orbit contract;
- the selected source event or required channel is missing;
- any required channel uses an unexpected frame;
- the local tangent displacement exceeds the verified 100 km domain;
- the derived state is unbound, circular, equatorial, or intersects Earth;
- the source task does not appear before the target task.

There is no fallback to the target's original orbit state.

## Evidence

`tests/test_handover.py` constructs a reference ENU state whose expected orbit
is known analytically. It verifies the derived semi-major axis, eccentricity,
inclination, RAAN, explicit workflow dependency, and a real TudatPy
propagation. It also verifies fail-closed behavior for unbound and invalid
dependencies.

## Deliberate limits

- This handover transfers translational state only.
- Rocket mass, staging, attitude, covariance, and navigation uncertainty are
  not transferred.
- The Earth orientation model is a deterministic constant-rate approximation,
  not an IERS Earth-orientation solution.
- RocketPy remains a local launch model. This adapter does not claim that a
  sounding-rocket starter scenario achieves orbit.
