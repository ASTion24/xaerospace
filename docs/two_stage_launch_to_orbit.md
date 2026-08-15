# Two-Stage Launch to Orbit

## Scope

`xaerospace.launch_to_orbit.v1` models a two-stage point-mass launch
vehicle from an Earth-fixed launch site through orbital insertion and an
unpowered verification coast.

The implementation uses TudatPy 1.0.0 for:

- degree-2, order-0 Earth gravity;
- rotating exponential-atmosphere drag;
- custom pitch-programmed thrust acceleration;
- coupled Cartesian and mass propagation;
- fixed-step RK4 integration;
- Cartesian-to-Keplerian conversion at second-stage cutoff.

Stage separation is an explicit arc boundary. The first-stage dry mass is
removed while Cartesian position and velocity remain continuous.

## Reference Mission

`scenarios/two_stage_220km_launch_demo.json` launches eastward from Cape
Canaveral with:

- 15,000 kg payload;
- 400,000 kg first-stage propellant;
- 57,000 kg second-stage propellant;
- contract-defined piecewise-linear pitch programs;
- 1 s integration and 5 s normalized output;
- 1,200 s unpowered post-insertion verification.

The release acceptance requires:

- insertion periapsis and apoapsis within 220 km ± 30 km;
- insertion eccentricity below 0.005;
- negative specific orbital energy;
- positive periapsis after the verification coast;
- final mass equal to payload plus upper-stage dry mass;
- monotonically non-increasing propagated mass;
- strict event order from lift-off through orbital verification.

The current reference result is approximately:

```text
insertion periapsis: 219.8 km
insertion apoapsis:  226.1 km
insertion e:         0.000477
final mass:          23,000 kg
```

Exact values are produced by the pinned TudatPy runtime and checked by the
release gate.

## Deliberate Limits

- prescribed pitch program rather than closed-loop guidance;
- no attitude, structural-load, wind, or engine-throttle dynamics;
- constant thrust and specific impulse per stage;
- instantaneous separation;
- only two stages;
- exponential atmosphere and constant Earth rotation.

Requests outside these limits fail contract validation or require a new
explicit task-family component.
