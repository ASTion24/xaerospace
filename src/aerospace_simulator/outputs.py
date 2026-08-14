from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from .model_manifest import render_model_report
from .protocol import UnifiedSimulationResult

PHASE_COLORS = {
    "rail": "#64748b",
    "powered_ascent": "#ef4444",
    "coast_ascent": "#f59e0b",
    "descent": "#2563eb",
    "recovery": "#16a34a",
}


def write_outputs(
    result: UnifiedSimulationResult, output_directory: str | Path
) -> dict[str, Path]:
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "request": output_dir / "request.json",
        "result": output_dir / "result.json",
        "summary": output_dir / "summary.json",
        "model_manifest": output_dir / "model_manifest.json",
        "model_report": output_dir / "model_report.md",
        "trajectory": output_dir / "trajectory.csv",
    }
    if _supports_flight_profile(result):
        artifacts["flight_profile"] = output_dir / "flight_profile.png"
    if _supports_attitude_profile(result):
        artifacts["attitude_profile"] = output_dir / "attitude_profile.png"
    if _supports_recovery_profile(result):
        artifacts["recovery_profile"] = output_dir / "recovery_profile.png"
    if _supports_orbit_profile(result):
        artifacts["orbit_profile"] = output_dir / "orbit_profile.png"
    if _supports_orbital_elements_profile(result):
        artifacts["orbital_elements"] = output_dir / "orbital_elements.png"
    if _supports_launch_profile(result):
        artifacts["launch_profile"] = output_dir / "launch_profile.png"
    if _supports_aircraft_path(result):
        artifacts["aircraft_path"] = output_dir / "aircraft_path.png"
    if _supports_aircraft_response(result):
        artifacts["aircraft_response"] = output_dir / "aircraft_response.png"
    if _supports_spacecraft_attitude(result):
        artifacts["spacecraft_attitude"] = output_dir / "spacecraft_attitude.png"
    if _supports_reaction_wheel_response(result):
        artifacts["reaction_wheel_response"] = (
            output_dir / "reaction_wheel_response.png"
        )
    _write_request(result, artifacts["request"])
    _write_result(result, artifacts["result"])
    _write_summary(result, artifacts["summary"])
    _write_model_manifest(result, artifacts["model_manifest"])
    artifacts["model_report"].write_text(
        render_model_report(result.model_manifest),
        encoding="utf-8",
    )
    _write_trajectory(result, artifacts["trajectory"])
    if "flight_profile" in artifacts:
        _write_flight_profile(result, artifacts["flight_profile"])
    if "attitude_profile" in artifacts:
        _write_attitude_profile(result, artifacts["attitude_profile"])
    if "recovery_profile" in artifacts:
        _write_recovery_profile(result, artifacts["recovery_profile"])
    if "orbit_profile" in artifacts:
        _write_orbit_profile(result, artifacts["orbit_profile"])
    if "orbital_elements" in artifacts:
        _write_orbital_elements_profile(result, artifacts["orbital_elements"])
    if "launch_profile" in artifacts:
        _write_launch_profile(result, artifacts["launch_profile"])
    if "aircraft_path" in artifacts:
        _write_aircraft_path(result, artifacts["aircraft_path"])
    if "aircraft_response" in artifacts:
        _write_aircraft_response(result, artifacts["aircraft_response"])
    if "spacecraft_attitude" in artifacts:
        _write_spacecraft_attitude(result, artifacts["spacecraft_attitude"])
    if "reaction_wheel_response" in artifacts:
        _write_reaction_wheel_response(
            result,
            artifacts["reaction_wheel_response"],
        )
    return artifacts


def _write_request(result: UnifiedSimulationResult, path: Path) -> None:
    path.write_text(
        json.dumps(
            result.request.document(),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_result(result: UnifiedSimulationResult, path: Path) -> None:
    path.write_text(
        json.dumps(
            result.document(include_samples=True),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_summary(result: UnifiedSimulationResult, path: Path) -> None:
    path.write_text(
        json.dumps(
            result.document(include_samples=False),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_model_manifest(result: UnifiedSimulationResult, path: Path) -> None:
    path.write_text(
        json.dumps(
            result.document(include_samples=False)["model_manifest"],
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_trajectory(result: UnifiedSimulationResult, path: Path) -> None:
    headers = ["time_s"]
    columns: list[object] = [result.time_s]
    if _has_events(result, "rail_departure", "burnout", "apogee"):
        headers.append("phase")
        columns.append(_phase_labels(result))
    for channel in result.channels:
        headers.append(channel.name)
        columns.append(channel.values)

    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(zip(*columns))


def _write_flight_profile(result: UnifiedSimulationResult, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    phases = np.asarray(_phase_labels(result))
    horizontal_range = _channel_values(result, "horizontal_range")
    altitude = _channel_values(result, "altitude_agl")
    figure, axis = plt.subplots(figsize=(11, 6.5), constrained_layout=True)

    for phase, color in PHASE_COLORS.items():
        mask = phases == phase
        if not np.any(mask):
            continue
        axis.plot(
            horizontal_range[mask],
            altitude[mask],
            color=color,
            linewidth=2.4,
            label=phase.replace("_", " ").title(),
        )

    for event in result.events:
        axis.scatter(
            event.attributes["horizontal_range_m"],
            event.attributes["altitude_agl_m"],
            s=42,
            color="#111827",
            zorder=5,
        )
        axis.annotate(
            event.name.replace("_", " ").title(),
            (
                event.attributes["horizontal_range_m"],
                event.attributes["altitude_agl_m"],
            ),
            xytext=(7, 7),
            textcoords="offset points",
            fontsize=8,
        )

    axis.axhline(0, color="#334155", linewidth=1, alpha=0.7)
    axis.set_title(f"{result.request.label} - Flight Profile")
    axis.set_xlabel("Horizontal range from launch point (m)")
    axis.set_ylabel("Altitude above launch point (m)")
    axis.grid(True, alpha=0.25)
    axis.legend(loc="best")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_attitude_profile(result: UnifiedSimulationResult, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    figure, (angle_axis, rate_axis) = plt.subplots(
        2,
        1,
        figsize=(11, 8),
        sharex=True,
        constrained_layout=True,
    )
    angle_axis.plot(
        result.time_s,
        _channel_values(result, "attitude_angle"),
        linewidth=2,
        label="Attitude angle",
    )
    angle_axis.plot(
        result.time_s,
        _channel_values(result, "angle_of_attack"),
        linewidth=1.5,
        label="Angle of attack",
    )
    angle_axis.set_ylabel("Angle (deg)")
    angle_axis.grid(True, alpha=0.25)
    angle_axis.legend(loc="best")

    for values, label in (
        (_channel_values(result, "omega1"), "omega 1"),
        (_channel_values(result, "omega2"), "omega 2"),
        (_channel_values(result, "omega3"), "omega 3"),
    ):
        rate_axis.plot(result.time_s, values, linewidth=1.5, label=label)
    for event in result.events[:-1]:
        rate_axis.axvline(
            event.time_s,
            color="#64748b",
            linewidth=0.8,
            alpha=0.5,
        )
    rate_axis.set_xlabel("Time (s)")
    rate_axis.set_ylabel("Body angular rate (rad/s)")
    rate_axis.grid(True, alpha=0.25)
    rate_axis.legend(loc="best")
    figure.suptitle(f"{result.request.label} - Attitude and Angular Rates")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_recovery_profile(result: UnifiedSimulationResult, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    apogee_time = result.event("apogee").time_s
    mask = result.time_s >= apogee_time
    time = result.time_s[mask]
    altitude = _channel_values(result, "altitude_agl")[mask]
    vertical_speed = _channel_values(result, "vz")[mask]
    figure, (altitude_axis, speed_axis) = plt.subplots(
        2,
        1,
        figsize=(11, 8),
        sharex=True,
        constrained_layout=True,
    )
    altitude_axis.plot(time, altitude, color="#2563eb", linewidth=2)
    altitude_axis.set_ylabel("Altitude AGL (m)")
    altitude_axis.grid(True, alpha=0.25)

    speed_axis.plot(time, vertical_speed, color="#7c3aed", linewidth=2)
    speed_axis.axhline(0, color="#334155", linewidth=0.8)
    speed_axis.set_xlabel("Time (s)")
    speed_axis.set_ylabel("Vertical speed (m/s)")
    speed_axis.grid(True, alpha=0.25)

    for event in result.events:
        if not event.name.startswith("parachute_"):
            continue
        color = "#f59e0b" if event.name.endswith("_trigger") else "#16a34a"
        label = event.name.removeprefix("parachute_").replace("_", " ").title()
        for axis in (altitude_axis, speed_axis):
            axis.axvline(
                event.time_s,
                color=color,
                linewidth=1.2,
                alpha=0.8,
                label=label if axis is altitude_axis else None,
            )
    altitude_axis.legend(loc="best")
    figure.suptitle(f"{result.request.label} - Recovery Profile")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_orbit_profile(result: UnifiedSimulationResult, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    x_km = _channel_values(result, "position_x") / 1000.0
    y_km = _channel_values(result, "position_y") / 1000.0
    z_km = _channel_values(result, "position_z") / 1000.0
    earth_radius_km = (
        float(
            _channel_values(result, "orbital_radius")[0]
            - _channel_values(result, "altitude")[0]
        )
        / 1000.0
    )
    longitude = np.linspace(0.0, 2.0 * np.pi, 48)
    latitude = np.linspace(-0.5 * np.pi, 0.5 * np.pi, 24)
    sphere_x = earth_radius_km * np.outer(
        np.cos(latitude),
        np.cos(longitude),
    )
    sphere_y = earth_radius_km * np.outer(
        np.cos(latitude),
        np.sin(longitude),
    )
    sphere_z = earth_radius_km * np.outer(
        np.sin(latitude),
        np.ones_like(longitude),
    )
    figure = plt.figure(figsize=(9, 8), constrained_layout=True)
    axis = figure.add_subplot(111, projection="3d")
    axis.plot_surface(
        sphere_x,
        sphere_y,
        sphere_z,
        color="#60a5fa",
        alpha=0.28,
        linewidth=0,
    )
    axis.plot(x_km, y_km, z_km, color="#dc2626", linewidth=1.8)
    axis.scatter(
        [x_km[0]],
        [y_km[0]],
        [z_km[0]],
        color="#111827",
        s=35,
        label="Propagation start",
    )
    extent = float(
        np.max(
            np.abs(
                np.concatenate(
                    (
                        x_km,
                        y_km,
                        z_km,
                        np.asarray([earth_radius_km]),
                    )
                )
            )
        )
    )
    axis.set_xlim(-extent, extent)
    axis.set_ylim(-extent, extent)
    axis.set_zlim(-extent, extent)
    axis.set_box_aspect((1, 1, 1))
    axis.set_xlabel("J2000 x (km)")
    axis.set_ylabel("J2000 y (km)")
    axis.set_zlabel("J2000 z (km)")
    axis.set_title(f"{result.request.label} - Earth-Centered Orbit")
    axis.legend(loc="best")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_orbital_elements_profile(
    result: UnifiedSimulationResult,
    path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    time_hours = result.time_s / 3600.0
    semi_major_axis_delta = (
        _channel_values(result, "semi_major_axis")
        - _channel_values(result, "semi_major_axis")[0]
    )
    figure, axes = plt.subplots(
        2,
        2,
        figsize=(11, 8),
        sharex=True,
        constrained_layout=True,
    )
    plots = (
        (axes[0, 0], semi_major_axis_delta, "Semi-major axis change (m)"),
        (
            axes[0, 1],
            _channel_values(result, "eccentricity"),
            "Eccentricity (1)",
        ),
        (
            axes[1, 0],
            _channel_values(result, "inclination"),
            "Inclination (deg)",
        ),
        (axes[1, 1], _channel_values(result, "raan"), "RAAN (deg)"),
    )
    for axis, values, label in plots:
        axis.plot(time_hours, values, color="#2563eb", linewidth=1.7)
        axis.set_ylabel(label)
        axis.grid(True, alpha=0.25)
    axes[1, 0].set_xlabel("Time since propagation start (h)")
    axes[1, 1].set_xlabel("Time since propagation start (h)")
    figure.suptitle(f"{result.request.label} - Osculating Elements")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_launch_profile(
    result: UnifiedSimulationResult,
    path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    time = result.time_s
    altitude = _channel_values(result, "altitude") / 1000.0
    downrange = _channel_values(result, "downrange") / 1000.0
    speed = _channel_values(result, "speed") / 1000.0
    dynamic_pressure = _channel_values(result, "dynamic_pressure") / 1000.0
    mass = _channel_values(result, "vehicle_mass") / 1000.0
    pitch = _channel_values(result, "pitch_command")
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(11, 10),
        sharex=True,
        constrained_layout=True,
    )
    range_axis = axes[0].twinx()
    axes[0].plot(time, altitude, color="#2563eb", label="Altitude")
    range_axis.plot(
        time,
        downrange,
        color="#7c3aed",
        label="Downrange",
    )
    axes[0].set_ylabel("Altitude (km)", color="#2563eb")
    range_axis.set_ylabel("Downrange (km)", color="#7c3aed")

    pressure_axis = axes[1].twinx()
    axes[1].plot(time, speed, color="#dc2626", label="Inertial speed")
    pressure_axis.plot(
        time,
        dynamic_pressure,
        color="#ea580c",
        label="Dynamic pressure",
    )
    axes[1].set_ylabel("Speed (km/s)", color="#dc2626")
    pressure_axis.set_ylabel("Dynamic pressure (kPa)", color="#ea580c")

    pitch_axis = axes[2].twinx()
    axes[2].plot(time, mass, color="#0f766e", label="Vehicle mass")
    pitch_axis.plot(time, pitch, color="#9333ea", label="Pitch command")
    axes[2].set_ylabel("Mass (Mg)", color="#0f766e")
    pitch_axis.set_ylabel("Pitch (deg)", color="#9333ea")
    axes[2].set_xlabel("Time since lift-off (s)")

    for axis in axes:
        axis.grid(True, alpha=0.25)
        for event in result.events:
            if event.name in {
                "stage_1_burnout",
                "stage_2_burnout",
                "orbit_verification_end",
            }:
                axis.axvline(
                    event.time_s,
                    color="#64748b",
                    linewidth=0.9,
                    alpha=0.65,
                )
    figure.suptitle(f"{result.request.label} - Launch-to-Orbit Profile")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_aircraft_path(
    result: UnifiedSimulationResult,
    path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    north = _channel_values(result, "north_displacement")
    east = _channel_values(result, "east_displacement")
    altitude = _channel_values(result, "altitude_msl")
    altitude_change = altitude - altitude[0]
    figure = plt.figure(figsize=(10, 7.5), constrained_layout=True)
    axis = figure.add_subplot(111, projection="3d")
    axis.plot(east, north, altitude_change, color="#2563eb", linewidth=2)
    axis.scatter(
        [east[0]],
        [north[0]],
        [altitude_change[0]],
        color="#16a34a",
        s=40,
        label="Start",
    )
    axis.scatter(
        [east[-1]],
        [north[-1]],
        [altitude_change[-1]],
        color="#dc2626",
        s=40,
        label="End",
    )
    axis.set_xlabel("East displacement (m)")
    axis.set_ylabel("North displacement (m)")
    axis.set_zlabel("Altitude change (m)")
    axis.set_title(f"{result.request.label} - Local Aircraft Path")
    axis.legend(loc="best")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_aircraft_response(
    result: UnifiedSimulationResult,
    path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    time = result.time_s
    altitude = _channel_values(result, "altitude_msl")
    airspeed = _channel_values(result, "calibrated_airspeed")
    heading = _channel_values(result, "heading")
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(11, 10),
        sharex=True,
        constrained_layout=True,
    )
    axes[0].plot(time, _channel_values(result, "roll"), label="Roll")
    axes[0].plot(time, _channel_values(result, "pitch"), label="Pitch")
    axes[0].plot(time, heading - heading[0], label="Heading change")
    axes[0].set_ylabel("Angle (deg)")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(loc="best")

    altitude_axis = axes[1]
    speed_axis = altitude_axis.twinx()
    altitude_axis.plot(
        time,
        altitude - altitude[0],
        color="#2563eb",
        label="Altitude change",
    )
    speed_axis.plot(
        time,
        airspeed,
        color="#ea580c",
        label="Calibrated airspeed",
    )
    altitude_axis.set_ylabel("Altitude change (m)", color="#2563eb")
    speed_axis.set_ylabel("Calibrated airspeed (m/s)", color="#ea580c")
    altitude_axis.grid(True, alpha=0.25)

    for name, label in (
        ("aileron_command", "Aileron"),
        ("elevator_command", "Elevator"),
        ("rudder_command", "Rudder"),
        ("throttle_command", "Throttle"),
    ):
        axes[2].plot(time, _channel_values(result, name), label=label)
    axes[2].set_xlabel("Time (s)")
    axes[2].set_ylabel("Normalized command")
    axes[2].grid(True, alpha=0.25)
    axes[2].legend(loc="best")
    figure.suptitle(f"{result.request.label} - 6DOF Control Response")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_spacecraft_attitude(
    result: UnifiedSimulationResult,
    path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    time = result.time_s
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(11, 10),
        sharex=True,
        constrained_layout=True,
    )
    for axis_name in ("x", "y", "z"):
        axes[0].plot(
            time,
            _channel_values(result, f"attitude_error_mrp_{axis_name}"),
            label=f"MRP {axis_name}",
        )
    axes[0].plot(
        time,
        _channel_values(result, "attitude_error_norm"),
        color="#111827",
        linewidth=2,
        label="MRP error norm",
    )
    axes[0].set_ylabel("MRP error (1)")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(loc="best")

    for axis_name in ("x", "y", "z"):
        axes[1].plot(
            time,
            _channel_values(result, f"angular_rate_error_{axis_name}"),
            label=f"Rate {axis_name}",
        )
    axes[1].plot(
        time,
        _channel_values(result, "angular_rate_error_norm"),
        color="#111827",
        linewidth=2,
        label="Rate error norm",
    )
    axes[1].set_ylabel("Rate error (rad/s)")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend(loc="best")

    for axis_name in ("x", "y", "z"):
        axes[2].plot(
            time,
            _channel_values(
                result,
                f"requested_body_control_torque_{axis_name}",
            ),
            label=f"Body torque {axis_name}",
        )
    axes[2].set_xlabel("Time (s)")
    axes[2].set_ylabel("Requested torque (N m)")
    axes[2].grid(True, alpha=0.25)
    axes[2].legend(loc="best")
    figure.suptitle(f"{result.request.label} - Spacecraft Attitude and GNC")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _write_reaction_wheel_response(
    result: UnifiedSimulationResult,
    path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    time = result.time_s
    colors = ("#2563eb", "#ea580c", "#16a34a")
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(11, 10),
        sharex=True,
        constrained_layout=True,
    )
    for wheel_number, color in enumerate(colors, start=1):
        axes[0].plot(
            time,
            _channel_values(
                result,
                f"requested_wheel_motor_torque_{wheel_number}",
            ),
            color=color,
            linestyle="--",
            alpha=0.65,
            label=f"RW{wheel_number} requested",
        )
        axes[0].plot(
            time,
            _channel_values(
                result,
                f"applied_wheel_motor_torque_{wheel_number}",
            ),
            color=color,
            linewidth=1.7,
            label=f"RW{wheel_number} applied",
        )
        axes[1].plot(
            time,
            _channel_values(result, f"reaction_wheel_speed_{wheel_number}"),
            color=color,
            label=f"RW{wheel_number}",
        )
        axes[2].plot(
            time,
            _channel_values(
                result,
                f"reaction_wheel_angular_momentum_{wheel_number}",
            ),
            color=color,
            label=f"RW{wheel_number}",
        )
    axes[0].set_ylabel("Motor torque (N m)")
    axes[1].set_ylabel("Wheel speed (rad/s)")
    axes[2].set_ylabel("Wheel momentum (N m s)")
    axes[2].set_xlabel("Time (s)")
    for axis in axes:
        axis.grid(True, alpha=0.25)
        axis.legend(loc="best")
    figure.suptitle(f"{result.request.label} - Reaction-Wheel Response")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _channel_values(result: UnifiedSimulationResult, name: str) -> np.ndarray:
    return result.channel(name).values


def _has_channels(result: UnifiedSimulationResult, *names: str) -> bool:
    available = {channel.name for channel in result.channels}
    return set(names) <= available


def _has_events(result: UnifiedSimulationResult, *names: str) -> bool:
    available = {event.name for event in result.events}
    return set(names) <= available


def _supports_flight_profile(result: UnifiedSimulationResult) -> bool:
    return _has_channels(result, "horizontal_range", "altitude_agl") and _has_events(
        result,
        "rail_departure",
        "burnout",
        "apogee",
        "impact",
    )


def _supports_attitude_profile(result: UnifiedSimulationResult) -> bool:
    return _has_channels(
        result,
        "attitude_angle",
        "angle_of_attack",
        "omega1",
        "omega2",
        "omega3",
    )


def _supports_recovery_profile(result: UnifiedSimulationResult) -> bool:
    return _has_channels(result, "altitude_agl", "vz") and any(
        event.name.endswith("_deployment")
        for event in result.events
        if event.name.startswith("parachute_")
    )


def _supports_orbit_profile(result: UnifiedSimulationResult) -> bool:
    return _has_channels(
        result,
        "position_x",
        "position_y",
        "position_z",
        "orbital_radius",
        "altitude",
    )


def _supports_orbital_elements_profile(
    result: UnifiedSimulationResult,
) -> bool:
    return _has_channels(
        result,
        "semi_major_axis",
        "eccentricity",
        "inclination",
        "raan",
    )


def _supports_launch_profile(result: UnifiedSimulationResult) -> bool:
    return _has_channels(
        result,
        "altitude",
        "downrange",
        "speed",
        "dynamic_pressure",
        "vehicle_mass",
        "pitch_command",
    ) and _has_events(
        result,
        "stage_1_burnout",
        "stage_2_burnout",
        "orbital_insertion",
    )


def _supports_aircraft_path(result: UnifiedSimulationResult) -> bool:
    return _has_channels(
        result,
        "north_displacement",
        "east_displacement",
        "altitude_msl",
    )


def _supports_aircraft_response(result: UnifiedSimulationResult) -> bool:
    return _has_channels(
        result,
        "roll",
        "pitch",
        "heading",
        "altitude_msl",
        "calibrated_airspeed",
        "aileron_command",
        "elevator_command",
        "rudder_command",
        "throttle_command",
    )


def _supports_spacecraft_attitude(result: UnifiedSimulationResult) -> bool:
    return _has_channels(
        result,
        "attitude_error_mrp_x",
        "attitude_error_mrp_y",
        "attitude_error_mrp_z",
        "attitude_error_norm",
        "angular_rate_error_x",
        "angular_rate_error_y",
        "angular_rate_error_z",
        "angular_rate_error_norm",
        "requested_body_control_torque_x",
        "requested_body_control_torque_y",
        "requested_body_control_torque_z",
    )


def _supports_reaction_wheel_response(
    result: UnifiedSimulationResult,
) -> bool:
    return _has_channels(
        result,
        "requested_wheel_motor_torque_1",
        "requested_wheel_motor_torque_2",
        "requested_wheel_motor_torque_3",
        "applied_wheel_motor_torque_1",
        "applied_wheel_motor_torque_2",
        "applied_wheel_motor_torque_3",
        "reaction_wheel_speed_1",
        "reaction_wheel_speed_2",
        "reaction_wheel_speed_3",
        "reaction_wheel_angular_momentum_1",
        "reaction_wheel_angular_momentum_2",
        "reaction_wheel_angular_momentum_3",
    )


def _phase_labels(result: UnifiedSimulationResult) -> tuple[str, ...]:
    rail_time = result.event("rail_departure").time_s
    burnout_time = result.event("burnout").time_s
    apogee_time = result.event("apogee").time_s
    deployment_times = [
        event.time_s
        for event in result.events
        if event.name.startswith("parachute_") and event.name.endswith("_deployment")
    ]
    first_deployment_time = min(deployment_times) if deployment_times else float("inf")
    return tuple(
        "rail"
        if time_s < rail_time
        else "powered_ascent"
        if time_s < burnout_time
        else "coast_ascent"
        if time_s < apogee_time
        else "descent"
        if time_s < first_deployment_time
        else "recovery"
        for time_s in result.time_s
    )
