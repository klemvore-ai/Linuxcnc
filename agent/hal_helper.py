"""Local, read-only bridge to LinuxCNC HAL owned by the LinuxCNC user."""

import argparse
import importlib
import json
import os
import socket
import stat
import subprocess

from agent.policy import AgentPolicyError, validate_hal_value_request


MAX_OUTPUT_BYTES = 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
REQUESTS = {
    b"show_hal_pins\n": ("halcmd", "show", "pin"),
    b"show_hal_components\n": ("halcmd", "show", "comp"),
}


def _show_hal(argv):
    """Run one fixed, read-only HAL inspection operation."""
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return {
        "output": result.stdout[-MAX_OUTPUT_BYTES:],
        "error": result.stderr[-MAX_ERROR_BYTES:],
    }


def show_hal_pins():
    """Return the fixed HAL pin listing."""
    return _show_hal(REQUESTS[b"show_hal_pins\n"])


def show_hal_components():
    """Return the fixed HAL component listing."""
    return _show_hal(REQUESTS[b"show_hal_components\n"])


def read_hal_values(pins):
    """Read one bounded, validated value snapshot without a shell."""
    values = {}
    errors = []
    for pin in pins:
        result = subprocess.run(
            ["halcmd", "getp", pin],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value:
            values[pin] = value
        else:
            errors.append(result.stderr.strip() or f"Unable to read {pin}.")
    return {"values": values, "error": "\n".join(errors)[-MAX_ERROR_BYTES:]}


def linuxcnc_status():
    """Read one bounded LinuxCNC status snapshot as the LinuxCNC owner."""
    status_program = """import json, linuxcnc
status = linuxcnc.stat()
status.poll()
position = getattr(status, 'actual_position', (0.0, 0.0, 0.0))
dtg = getattr(status, 'dtg', (0.0, 0.0, 0.0))
interpreter = getattr(status, 'interp_state', None)
mode = getattr(status, 'task_mode', None)
result = {
    'machine_state': {
        'running': True,
        'idle': interpreter == linuxcnc.INTERP_IDLE,
        'machine_on': getattr(status, 'task_state', None) == linuxcnc.STATE_ON,
        'state_available': True,
    },
    'telemetry': {
        'X': position[0] if len(position) > 0 else 0.0,
        'Y': position[1] if len(position) > 1 else 0.0,
        'Z': position[2] if len(position) > 2 else 0.0,
        'FEED': getattr(status, 'current_vel', 0.0) * 60.0,
        'RPM': abs(getattr(status, 'spindle_speed', 0.0)),
        'FEED_OVR': getattr(status, 'feedrate', 1.0),
        'SPINDLE_OVR': getattr(status, 'spindle_scale', 1.0),
        'HOMED_COUNT': sum(1 for value in getattr(status, 'homed', []) if value),
        'MACHINE_ON': getattr(status, 'task_state', None) == linuxcnc.STATE_ON,
        'ESTOP_ACTIVATED': getattr(status, 'task_state', None) == linuxcnc.STATE_ESTOP,
        'JOINTS': int(getattr(status, 'joints', getattr(status, 'axes', 3))),
        'LINE': getattr(status, 'motion_line', 0),
        'STATUS': 'IDLE' if interpreter == linuxcnc.INTERP_IDLE else 'PAUSED' if interpreter == linuxcnc.INTERP_PAUSED else 'RUNNING',
        'MODE': 'MANUAL' if mode == linuxcnc.MODE_MANUAL else 'AUTO' if mode == linuxcnc.MODE_AUTO else 'MDI' if mode == linuxcnc.MODE_MDI else 'UNKNOWN',
        'PROGRAM_LOADED': bool(getattr(status, 'file', None)),
        'ABSOLUTE_X': position[0] if len(position) > 0 else 0.0,
        'ABSOLUTE_Y': position[1] if len(position) > 1 else 0.0,
        'ABSOLUTE_Z': position[2] if len(position) > 2 else 0.0,
        'DTG_X': dtg[0] if len(dtg) > 0 else 0.0,
        'DTG_Y': dtg[1] if len(dtg) > 1 else 0.0,
        'DTG_Z': dtg[2] if len(dtg) > 2 else 0.0,
    },
    'error': '',
}
print(json.dumps(result, separators=(',', ':')))"""
    result = subprocess.run(
        ["/opt/CNCFusion/.venv/bin/python", "-c", status_program],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "LinuxCNC status poll failed.")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict) or not isinstance(payload.get("machine_state"), dict) or not isinstance(payload.get("telemetry"), dict):
        raise RuntimeError("LinuxCNC status poll returned invalid JSON.")
    return payload


def linuxcnc_machine_command(command):
    """Run one fixed allowlisted LinuxCNC machine command as the LinuxCNC owner."""
    commands = {
        "abort": "controller.abort()",
        "power_on": "controller.state(linuxcnc.STATE_ON)",
        "power_off": "controller.state(linuxcnc.STATE_OFF)",
        "estop": "controller.state(linuxcnc.STATE_OFF)\ncontroller.state(linuxcnc.STATE_ESTOP)",
        "estop_reset": "controller.state(linuxcnc.STATE_ESTOP_RESET)",
        "override_limits": "controller.override_limits()",
    }
    if command not in commands:
        raise AgentPolicyError("Unsupported LinuxCNC helper command.")
    program = f"""import json, linuxcnc
controller = linuxcnc.command()
{commands[command]}
controller.wait_complete()
print(json.dumps({{'accepted': True, 'command': {command!r}}}))"""
    result = subprocess.run(
        ["/opt/CNCFusion/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "LinuxCNC command failed.")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict) or payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC command returned invalid JSON.")
    return payload


def linuxcnc_home_axis(axis):
    """Home one validated joint as the LinuxCNC owner."""
    joint_index = {"X": 0, "Y": 1, "Z": 2, "A": 3, "B": 4, "C": 5}[axis]
    program = f"""import json, linuxcnc
status = linuxcnc.stat()
status.poll()
if status.task_state != linuxcnc.STATE_ON:
    raise RuntimeError("Axis homing requires powered LinuxCNC.")
controller = linuxcnc.command()
controller.mode(linuxcnc.MODE_MANUAL)
controller.wait_complete()
if getattr(status, "motion_teleop", False):
    controller.teleop_enable(0)
    controller.wait_complete()
controller.home({joint_index})
controller.wait_complete()
print(json.dumps({{"accepted": True, "axis": {axis!r}}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/CNCFusion/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Axis homing failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept axis homing.")
    return payload


def linuxcnc_tool_setter_calibration(x, y, velocity, search_distance):
    """Run the fixed tool-setter probe cycle as the LinuxCNC owner."""
    program = f"""import json, linuxcnc, time
controller = linuxcnc.command()
status = linuxcnc.stat()
errors = linuxcnc.error_channel()
status.poll()
if status.task_state != linuxcnc.STATE_ON or status.interp_state != linuxcnc.INTERP_IDLE:
    raise RuntimeError("Tool-setter calibration requires powered, idle LinuxCNC.")

def wait_for_step(step):
    controller.wait_complete()
    error = errors.poll()
    if error:
        raise RuntimeError(f"Tool-setter {{step}} failed: {{error[1]}}")

controller.mode(linuxcnc.MODE_MDI)
wait_for_step("mode selection")
controller.mdi("G90")
wait_for_step("absolute mode")
controller.mdi("G53 G0 Z0")
wait_for_step("safe Z move")
controller.mdi("G53 G0 X{x:.4f} Y{y:.4f}")
wait_for_step("setter XY move")

status.poll()
if getattr(status, "probe_tripped", False):
    controller.mdi("G91 G38.4 Z{search_distance:.4f} F{velocity:.4f}")
    wait_for_step("probe retract")
    controller.mdi("G90")
    wait_for_step("absolute mode")

try:
    z_min = float(status.axis[2]["min_position_limit"])
except (AttributeError, KeyError, TypeError, IndexError):
    z_min = -1000.0
status.poll()
z_offset = status.g5x_offset[2] + status.g92_offset[2] + status.tool_offset[2]
z_target = z_min - z_offset + 0.1
current_z = status.position[2] - z_offset
if z_target >= current_z:
    z_target = current_z - 200.0

controller.mdi(f"G38.2 Z{{z_target:.4f}} F{velocity * 4.0:.4f}")
wait_for_step("fast probe")
status.poll()
if not status.probe_tripped:
    raise RuntimeError("Fast probe did not trip the tool setter.")
controller.mdi("G91 G0 Z2.0000")
wait_for_step("probe retract")
controller.mdi(f"G38.2 Z{{z_target:.4f}} F{velocity:.4f}")
wait_for_step("slow probe")
status.poll()
if not status.probe_tripped:
    raise RuntimeError("Slow probe did not trip the tool setter.")
setter_height = float(status.probed_position[2])
controller.mdi("G91 G0 Z5.0000")
wait_for_step("final retract")
print(json.dumps({{"setter_height": setter_height}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/CNCFusion/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Tool-setter calibration failed.")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        raise RuntimeError("Tool-setter calibration returned invalid JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("setter_height"), (int, float)):
        raise RuntimeError("Tool-setter calibration returned invalid data.")
    return payload


def _remove_stale_socket(socket_path):
    """Remove a previous helper socket without deleting another file type."""
    try:
        mode = os.stat(socket_path).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"Refusing to replace non-socket path: {socket_path}")
    os.unlink(socket_path)


def _handle_connection(connection):
    """Accept only the fixed request and return one JSON line."""
    connection.settimeout(10)
    request = connection.recv(8192)
    argv = REQUESTS.get(request)
    if argv is not None:
        try:
            response = _show_hal(argv)
        except (OSError, subprocess.SubprocessError) as error:
            response = {"output": "", "error": str(error)}
    else:
        try:
            payload = json.loads(request.decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("operation"), str):
                raise AgentPolicyError("Unsupported HAL helper request.")
            if payload["operation"] == "read_hal_values" and set(payload) == {"operation", "pins"}:
                response = read_hal_values(validate_hal_value_request({"pins": payload["pins"]}))
            elif payload["operation"] == "linuxcnc_status" and set(payload) == {"operation"}:
                response = linuxcnc_status()
            elif payload["operation"] == "linuxcnc_machine_command" and set(payload) == {"operation", "command"} and isinstance(payload["command"], str):
                response = linuxcnc_machine_command(payload["command"])
            elif payload["operation"] == "linuxcnc_home_axis" and set(payload) == {"operation", "axis"} and isinstance(payload["axis"], str) and payload["axis"] in {"X", "Y", "Z", "A", "B", "C"}:
                response = linuxcnc_home_axis(payload["axis"])
            elif payload["operation"] == "linuxcnc_tool_setter_calibration" and set(payload) == {"operation", "x", "y", "velocity", "search_distance"}:
                response = linuxcnc_tool_setter_calibration(
                    float(payload["x"]),
                    float(payload["y"]),
                    float(payload["velocity"]),
                    float(payload["search_distance"]),
                )
            else:
                raise AgentPolicyError("Unsupported HAL helper request.")
        except (AgentPolicyError, UnicodeDecodeError, json.JSONDecodeError) as error:
            response = {"values": {}, "error": str(error)}
        except Exception as error:
            if isinstance(locals().get("payload"), dict) and payload.get("operation") == "linuxcnc_status":
                response = {"machine_state": {}, "telemetry": {}, "error": str(error)}
            else:
                response = {"values": {}, "error": str(error)}
    connection.sendall(json.dumps(response).encode("utf-8") + b"\n")


def serve(socket_path):
    """Serve the fixed HAL inspection operation over a local Unix socket."""
    _remove_stale_socket(socket_path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(socket_path)
        os.chmod(socket_path, 0o660)
        listener.listen(8)
        while True:
            connection, _ = listener.accept()
            with connection:
                _handle_connection(connection)
    finally:
        listener.close()
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass


def main():
    """Start the local helper on the configured Unix-domain socket."""
    parser = argparse.ArgumentParser(description="CNCFusion read-only HAL helper")
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    serve(args.socket)


if __name__ == "__main__":
    main()