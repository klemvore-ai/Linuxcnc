"""Local, read-only bridge to LinuxCNC HAL owned by the LinuxCNC user."""

import argparse
import importlib
import json
import os
import socket
import stat
import subprocess
import threading
import time

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
    status_program = """import json, linuxcnc, time
import sys

# Inline safe_poll to avoid import issues in subprocess
def safe_poll(stat_obj, retries=5, delay=0.1):
    for attempt in range(retries):
        try:
            stat_obj.poll()
            return True
        except (SystemError, OSError, AttributeError):
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    return False

status = linuxcnc.stat()
safe_poll(status)
errors = linuxcnc.error_channel()
error_messages = []
while True:
    error = errors.poll()
    if not error:
        break
    error_messages.append(str(error[1]))
position = getattr(status, 'actual_position', (0.0, 0.0, 0.0))
dtg = getattr(status, 'dtg', (0.0, 0.0, 0.0))
interpreter = getattr(status, 'interp_state', None)
mode = getattr(status, 'task_mode', None)
joints = int(getattr(status, 'joints', getattr(status, 'axes', 3)))

def joint_input(index, fallback):
    try:
        return float(status.joint[index]['input'])
    except Exception:
        return fallback

def joint_homed(index):
    try:
        return bool(status.joint[index]['homed'])
    except Exception:
        try:
            return bool(status.homed[index])
        except Exception:
            return False

homed_count = sum(1 for index in range(joints) if joint_homed(index))

if joints >= 4:
    x_machine_position = joint_input(0, position[0] if len(position) > 0 else 0.0)
    y1_position = joint_input(1, position[1] if len(position) > 1 else 0.0)
    y2_position = joint_input(2, y1_position)
    y_machine_position = (y1_position + y2_position) / 2.0
    z_machine_position = joint_input(3, position[2] if len(position) > 2 else 0.0)
else:
    x_machine_position = position[0] if len(position) > 0 else 0.0
    y_machine_position = position[1] if len(position) > 1 else 0.0
    z_machine_position = position[2] if len(position) > 2 else 0.0

g5x = getattr(status, 'g5x_offset', (0.0,) * 9)
g92 = getattr(status, 'g92_offset', (0.0,) * 9)
tool = getattr(status, 'tool_offset', (0.0,) * 9)
x_position = x_machine_position - (g5x[0] + g92[0] + tool[0])
y_position = y_machine_position - (g5x[1] + g92[1] + tool[1])
z_position = z_machine_position - (g5x[2] + g92[2] + tool[2])
# This machine has no spindle encoder: report commanded RPM, not feedback.
spindle_rpm = 0.0
try:
    spindle_list = getattr(status, 'spindle', ())
    if spindle_list and len(spindle_list) > 0:
        spindle0 = spindle_list[0]
        if isinstance(spindle0, dict):
            commanded = spindle0.get('commanded_speed')
            if commanded is None:
                commanded = spindle0.get('speed', 0.0)
            spindle_rpm = abs(float(commanded))
except Exception:
    pass

result = {
    'machine_state': {
        'running': True,
        'idle': interpreter == linuxcnc.INTERP_IDLE,
        'machine_on': getattr(status, 'task_state', None) == linuxcnc.STATE_ON,
        'state_available': True,
    },
    'telemetry': {
        'X': x_position,
        'Y': y_position,
        'Z': z_position,
        'FEED': getattr(status, 'current_vel', 0.0) * 60.0,
        'RPM': spindle_rpm,
        'FEED_OVR': getattr(status, 'feedrate', 1.0),
        'SPINDLE_OVR': getattr(status, 'spindle_scale', 1.0),
        'HOMED_COUNT': homed_count,
        'MACHINE_ON': getattr(status, 'task_state', None) == linuxcnc.STATE_ON,
        'ESTOP_ACTIVATED': getattr(status, 'task_state', None) == linuxcnc.STATE_ESTOP,
        'JOINTS': joints,
        'LINE': getattr(status, 'motion_line', 0),
        'CURRENT_LINE': getattr(status, 'current_line', 0),
        'READ_LINE': getattr(status, 'read_line', 0),
        'EXEC_STATE': getattr(status, 'exec_state', None),
        'INTERP_STATE': interpreter,
        'TASK_STATE': getattr(status, 'task_state', None),
        'COMMAND': getattr(status, 'command', ''),
        'STATUS': 'IDLE' if interpreter == linuxcnc.INTERP_IDLE else 'PAUSED' if interpreter == linuxcnc.INTERP_PAUSED else 'RUNNING',
        'MODE': 'MANUAL' if mode == linuxcnc.MODE_MANUAL else 'AUTO' if mode == linuxcnc.MODE_AUTO else 'MDI' if mode == linuxcnc.MODE_MDI else 'UNKNOWN',
        'PROGRAM_LOADED': bool(getattr(status, 'file', None)),
        'PROGRAM_FILE': getattr(status, 'file', ''),
        'TASK_BLOCKED': getattr(status, 'exec_state', 2) not in (1, 2),
        'TOOL_IN_SPINDLE': int(getattr(status, 'tool_in_spindle', 0)),
        'POCKET_PREPPED': int(getattr(status, 'pocket_prepped', -1)),
        'ABSOLUTE_X': x_machine_position,
        'ABSOLUTE_Y': y_machine_position,
        'ABSOLUTE_Z': z_machine_position,
        'DTG_X': dtg[0] if len(dtg) > 0 else 0.0,
        'DTG_Y': dtg[1] if len(dtg) > 1 else 0.0,
        'DTG_Z': dtg[2] if len(dtg) > 2 else 0.0,
    },
    'error': '',
}
if error_messages:
    result['telemetry']['ERROR'] = '\\n'.join(error_messages)[-4096:]
print(json.dumps(result, separators=(',', ':')))"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", status_program],
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
    if command == "home_all":
        program = """import json, linuxcnc, time
controller = linuxcnc.command()
controller.mode(linuxcnc.MODE_MANUAL)
controller.wait_complete()
controller.teleop_enable(0)
controller.wait_complete()
controller.home(3)
controller.wait_complete()
time.sleep(30)
controller.home(0)
controller.wait_complete()
time.sleep(30)
print(json.dumps({"accepted": True, "command": "home_all"}, separators=(",", ":")))
"""
        result = subprocess.run(
            ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "HOME ALL failed.")
        return json.loads(result.stdout.strip().splitlines()[-1])

    if command == "unhome_all":
        program = """import json, linuxcnc, time
controller = linuxcnc.command()
controller.mode(linuxcnc.MODE_MANUAL)
result = controller.wait_complete(10)
if result == -1 or result == linuxcnc.RCS_ERROR:
    raise RuntimeError("Mode switch did not complete before unhoming.")
controller.teleop_enable(0)
result = controller.wait_complete(10)
if result == -1 or result == linuxcnc.RCS_ERROR:
    raise RuntimeError("Teleop disable did not complete before unhoming.")
for joint in range(4):
    controller.unhome(joint)
    result = controller.wait_complete(10)
    if result == -1 or result == linuxcnc.RCS_ERROR:
        raise RuntimeError(f"Joint {joint} unhome did not complete.")
print(json.dumps({"accepted": True, "command": "unhome_all"}, separators=(",", ":")))
"""
        result = subprocess.run(
            ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "UNHOME ALL failed.")
        return json.loads(result.stdout.strip().splitlines()[-1])

    if command == "program_start":
        program = """import json, linuxcnc, time
controller = linuxcnc.command()

# Inline safe_poll for subprocess stability
def safe_poll(stat_obj, retries=5, delay=0.1):
    for attempt in range(retries):
        try:
            stat_obj.poll()
            return True
        except (SystemError, OSError, AttributeError):
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    return False

status = linuxcnc.stat()
for attempt in range(10):
    safe_poll(status)
    if getattr(status, 'file', None):
        break
    time.sleep(0.1)
if not getattr(status, 'file', None):
    raise RuntimeError('Cycle start requires a loaded program.')
controller.mode(linuxcnc.MODE_AUTO)
controller.wait_complete(5)
controller.auto(linuxcnc.AUTO_RUN, 0)
print(json.dumps({"accepted": True, "command": "program_start"}, separators=(",", ":")))
"""
        result = subprocess.run(
            ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Cycle start failed.")
        return json.loads(result.stdout.strip().splitlines()[-1])

    if command == "program_pause_resume":
        program = """import json, linuxcnc, time
controller = linuxcnc.command()

# Inline safe_poll for subprocess stability
def safe_poll(stat_obj, retries=5, delay=0.1):
    for attempt in range(retries):
        try:
            stat_obj.poll()
            return True
        except (SystemError, OSError, AttributeError):
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    return False

status = linuxcnc.stat()
safe_poll(status)
if status.interp_state == linuxcnc.INTERP_PAUSED:
    controller.auto(linuxcnc.AUTO_RESUME)
elif status.interp_state == linuxcnc.INTERP_IDLE:
    raise RuntimeError('Pause/resume requires an active or paused program.')
else:
    controller.auto(linuxcnc.AUTO_PAUSE)
print(json.dumps({"accepted": True, "command": "program_pause_resume"}, separators=(",", ":")))
"""
        result = subprocess.run(
            ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Pause/resume failed.")
        return json.loads(result.stdout.strip().splitlines()[-1])

    if command == "tool_change_ack":
        for value in ("1", "0"):
            result = subprocess.run(
                ["halcmd", "setp", "iocontrol.0.tool-changed", value],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Tool-change acknowledge failed.")
            if value == "1":
                time.sleep(0.3)
        return {"accepted": True, "command": "tool_change_ack"}

    if command in {"probe_source_setter", "probe_source_probe"}:
        value = "0" if command == "probe_source_setter" else "1"
        result = subprocess.run(
            ["halcmd", "setp", "lut5.0.in-2", value],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Probe source selection failed.")
        return {"accepted": True, "command": command, "source": "setter" if value == "0" else "probe"}

    if command in {"estop_reset", "power_on", "power_off", "estop"}:
        state_commands = {
            "estop_reset": "controller.state(linuxcnc.STATE_ESTOP_RESET)",
            "power_on": "controller.state(linuxcnc.STATE_ON)",
            "power_off": "controller.state(linuxcnc.STATE_OFF)",
            "estop": "controller.state(linuxcnc.STATE_OFF)\ncontroller.state(linuxcnc.STATE_ESTOP)",
        }
        program = f"""import json, linuxcnc
controller = linuxcnc.command()
{state_commands[command]}
print(json.dumps({{"accepted": True, "command": {command!r}}}, separators=(",", ":")))
"""
        result = subprocess.run(
            ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Machine state command failed.")
        return json.loads(result.stdout.strip().splitlines()[-1])

    commands = {
        "abort": "controller.abort()",
        "override_limits": "controller.override_limits()",
        "mode_manual": "controller.mode(linuxcnc.MODE_MANUAL)",
        "mode_auto": "controller.mode(linuxcnc.MODE_AUTO)",
        "coolant_mist_on": "controller.mist(1)",
        "coolant_flood_on": "controller.flood(1)",
        "coolant_off": "controller.mist(0)\ncontroller.flood(0)",
    }
    if command not in commands:
        raise AgentPolicyError("Unsupported LinuxCNC helper command.")
    program = f"""import json, linuxcnc, time
controller = linuxcnc.command()
{commands[command]}
controller.wait_complete()
print(json.dumps({{'accepted': True, 'command': {command!r}}}))"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=120 if command in {"home_all", "unhome_all"} else 10,
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
    joint_index = {"X": 0, "Y": 1, "Z": 3, "A": 4, "B": 5, "C": 6}[axis]
    program = f"""import json, linuxcnc, time
controller = linuxcnc.command()
controller.mode(linuxcnc.MODE_MANUAL)
controller.wait_complete()
controller.teleop_enable(0)
controller.wait_complete()
controller.home({joint_index})
controller.wait_complete()
print(json.dumps({{"accepted": True, "axis": {axis!r}}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
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


def linuxcnc_jog(action, axis, velocity):
    """Start or stop one bounded Cartesian jog as the LinuxCNC owner."""
    axis_index = {"X": 0, "Y": 1, "Z": 2, "A": 3, "B": 4, "C": 5}[axis]
    program = f"""import json, linuxcnc, time
controller = linuxcnc.command()

# Inline safe_poll for subprocess stability
def safe_poll(stat_obj, retries=5, delay=0.1):
    for attempt in range(retries):
        try:
            stat_obj.poll()
            return True
        except (SystemError, OSError, AttributeError):
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    return False

status = linuxcnc.stat()
safe_poll(status)
if status.task_state != linuxcnc.STATE_ON:
    raise RuntimeError("Jogging requires powered LinuxCNC.")
controller.mode(linuxcnc.MODE_MANUAL)
controller.wait_complete(2)
if {action!r} == "start":
    joints = int(getattr(status, "joints", getattr(status, "axes", 3)))
    if sum(1 for index in range(joints) if bool(status.joint[index]["homed"])) < joints:
        raise RuntimeError("Secure jogging requires all joints homed.")
    controller.teleop_enable(1)
    controller.wait_complete(2)
    controller.jog(linuxcnc.JOG_CONTINUOUS, False, {axis_index}, {velocity:.6f})
else:
    controller.jog(linuxcnc.JOG_STOP, False, {axis_index})
controller.wait_complete(2)
print(json.dumps({{"accepted": True, "action": {action!r}, "axis": {axis!r}}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Jog operation failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept jog operation.")
    return payload


def linuxcnc_spindle_command(action, rpm):
    """Apply one fixed spindle command as the LinuxCNC owner."""
    program = f"""import json, linuxcnc
controller = linuxcnc.command()
if {action!r} == "forward":
    controller.spindle(linuxcnc.SPINDLE_FORWARD, {rpm:.4f})
elif {action!r} == "reverse":
    controller.spindle(linuxcnc.SPINDLE_REVERSE, {rpm:.4f})
else:
    controller.spindle(linuxcnc.SPINDLE_OFF)
print(json.dumps({{"accepted": True, "action": {action!r}, "rpm": {rpm:.4f}}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Spindle command failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept spindle command.")
    return payload


def linuxcnc_touch_off_axis(axis, value):
    """Set one work-coordinate axis value through a fixed MDI command."""
    program = f"""import json, linuxcnc, time
controller = linuxcnc.command()

# Inline safe_poll for subprocess stability
def safe_poll(stat_obj, retries=5, delay=0.1):
    for attempt in range(retries):
        try:
            stat_obj.poll()
            return True
        except (SystemError, OSError, AttributeError):
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    return False

status = linuxcnc.stat()
safe_poll(status)
if status.task_state != linuxcnc.STATE_ON or status.interp_state != linuxcnc.INTERP_IDLE:
    raise RuntimeError("Axis touch-off requires powered, idle LinuxCNC.")
fixture = int(getattr(status, "g5x_index", 1) or 1)
controller.mode(linuxcnc.MODE_MDI)
controller.wait_complete(5)
controller.mdi(f"G10 L20 P{{fixture}} {axis}{value:.4f}")
result = controller.wait_complete(5)
if result == -1 or result == linuxcnc.RCS_ERROR:
    raise RuntimeError("Axis touch-off command did not complete.")
controller.mode(linuxcnc.MODE_MANUAL)
controller.wait_complete(5)
print(json.dumps({{"accepted": True, "axis": {axis!r}, "value": {value:.4f}}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Axis touch-off failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept axis touch-off.")
    return payload


def linuxcnc_open_program(path):
    """Open one validated G-code path as the LinuxCNC owner."""
    program = f"""import json, linuxcnc
controller = linuxcnc.command()
controller.program_open({str(path)!r})
result = controller.wait_complete(10)
if result == -1 or result == linuxcnc.RCS_ERROR:
    raise RuntimeError("Program open command did not complete.")
print(json.dumps({{"accepted": True, "path": {str(path)!r}}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Program open failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept program open.")
    return payload


def linuxcnc_probe_z(target, velocity):
    """Run one bounded raw Z probe as the LinuxCNC owner."""
    program = f"""import json, linuxcnc, subprocess
controller = linuxcnc.command()

def probe_input_active():
    result = subprocess.run(
        ["halcmd", "getp", "motion.probe-input"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip().upper() == "TRUE"

controller.mode(linuxcnc.MODE_MDI)
if controller.wait_complete(5) in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Probe MDI mode selection did not complete.")
controller.mdi("G91 G38.2 Z{target:.4f} F{velocity:.4f}")
result = controller.wait_complete(45)
if result in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Raw Z probe motion did not complete.")
if not probe_input_active():
    raise RuntimeError("Raw Z probe failed because the probe did not trip.")
controller.mdi("G91 G0 Z2.0000")
if controller.wait_complete(45) in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Raw Z probe retract did not complete.")
print(json.dumps({{"accepted": True, "operation": "probe_z"}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=50,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Raw Z probe failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept raw Z probe.")
    return payload


def linuxcnc_touch_off_z(target, velocity, plate_thickness, retract):
    """Run one bounded touch-off cycle (probe down, set G10 L20 P0 Z, retract) as the LinuxCNC owner."""
    program = f"""import json, linuxcnc, subprocess
controller = linuxcnc.command()

def probe_input_active():
    result = subprocess.run(
        ["halcmd", "getp", "motion.probe-input"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip().upper() == "TRUE"

controller.mode(linuxcnc.MODE_MDI)
if controller.wait_complete(5) in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Touch-off MDI mode selection did not complete.")
controller.mdi("G91 G38.2 Z{target:.4f} F{velocity:.4f}")
result = controller.wait_complete(45)
if result in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Touch-off probe motion did not complete.")
if not probe_input_active():
    raise RuntimeError("Touch-off failed because the probe did not trip.")
controller.mdi("G10 L20 P0 Z{plate_thickness:.4f}")
if controller.wait_complete(5) in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Touch-off Z offset command did not complete.")
controller.mdi("G91 G0 Z{retract:.4f}")
if controller.wait_complete(45) in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Touch-off retract did not complete.")
print(json.dumps({{"accepted": True, "operation": "touch_off_z", "plate_thickness": {plate_thickness:.4f}, "retract": {retract:.4f}}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=50,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Touch-off failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept touch-off.")
    return payload


def linuxcnc_move_tool_setter(x, y, z):
    """Move to tool-setter location (G53 Z, then G53 XY) as the LinuxCNC owner."""
    program = f"""import json, linuxcnc
controller = linuxcnc.command()
controller.mode(linuxcnc.MODE_MDI)
if controller.wait_complete(5) in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Tool-setter move MDI mode selection did not complete.")
controller.mdi("G53 G0 Z{z:.4f}")
if controller.wait_complete(30) in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Tool-setter Z retract did not complete.")
controller.mdi("G53 G0 X{x:.4f} Y{y:.4f}")
if controller.wait_complete(45) in (-1, getattr(linuxcnc, "RCS_ERROR", -1)):
    raise RuntimeError("Tool-setter XY move did not complete.")
print(json.dumps({{"accepted": True, "operation": "move_tool_setter"}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=50,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Tool-setter move failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept tool-setter move.")
    return payload


def linuxcnc_run_subroutine(ocode):

    """Run one allowlisted O-code subroutine as a fixed MDI call."""
    # Run from the LinuxCNC config directory so SUBROUTINE_PATH resolves correctly
    linuxcnc_config_dir = "/home/darrell/linuxcnc/configs/LinuxCNC"
    program = f"""import json, linuxcnc, time, subprocess, sys, os

# Change to LinuxCNC config directory so relative paths work
os.chdir({linuxcnc_config_dir!r})

# Use halcmd for reliable state checking instead of linuxcnc.stat()
def check_hal_pin(pin_name):
    try:
        result = subprocess.run(
            ["halcmd", "getp", pin_name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "TRUE"
    except Exception:
        return False

def get_interp_state():
    if check_hal_pin("halui.program.is-idle"):
        return "IDLE"
    if check_hal_pin("halui.program.is-running"):
        return "RUNNING"
    if check_hal_pin("halui.program.is-paused"):
        return "PAUSED"
    return "UNKNOWN"

def get_task_state():
    if check_hal_pin("halui.machine.is-on"):
        return "ON"
    if check_hal_pin("halui.estop.is-activated"):
        return "ESTOP"
    return "OFF"

interp = get_interp_state()
task = get_task_state()
if task != "ON" or interp != "IDLE":
    raise RuntimeError(f"Subroutine requires powered, idle LinuxCNC. Current: task={{task}}, interp={{interp}}")

controller = linuxcnc.command()
errors = linuxcnc.error_channel()

controller.mode(linuxcnc.MODE_MDI)
controller.wait_complete(5)

time.sleep(0.5)
for _ in range(50):
    if check_hal_pin("halui.mode.is-mdi"):
        break
    time.sleep(0.1)
else:
    raise RuntimeError("MDI mode switch did not take effect.")

controller.mdi({(ocode + ' call')!r})
time.sleep(0.5)
error = errors.poll()
if error:
    raise RuntimeError(error[1])

print(json.dumps({{"accepted": True, "ocode": {ocode!r}}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        cwd=linuxcnc_config_dir,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-MAX_ERROR_BYTES:] or "Subroutine failed.")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload.get("accepted") is not True:
        raise RuntimeError("LinuxCNC did not accept subroutine.")
    return payload


def linuxcnc_tool_setter_calibration(x, y, velocity, search_distance):
    """Run the fixed tool-setter probe cycle as the LinuxCNC owner."""
    program = f"""import json, linuxcnc, time, subprocess, re
controller = linuxcnc.command()
errors = linuxcnc.error_channel()

def hal_true(pin):
    result = subprocess.run(["halcmd", "getp", pin], capture_output=True, text=True, timeout=5, check=False)
    return result.returncode == 0 and result.stdout.strip() == "TRUE"

if not hal_true("halui.machine.is-on"):
    raise RuntimeError("Tool-setter calibration requires powered LinuxCNC.")

def wait_for_step(step):
    result = controller.wait_complete(120)
    if result == -1 or result == linuxcnc.RCS_ERROR:
        raise RuntimeError(f"Tool-setter {{step}} did not complete.")
    messages = []
    while True:
        error = errors.poll()
        if not error:
            break
        messages.append(str(error[1]))
    if messages:
        raise RuntimeError(f"Tool-setter {{step}} failed: {{messages[-1]}}")

controller.mode(linuxcnc.MODE_MDI)
wait_for_step("mode selection")
controller.mdi("G90")
wait_for_step("absolute mode")
controller.mdi("G53 G0 Z0")
wait_for_step("safe Z move")
controller.mdi("G53 G0 X{x:.4f} Y{y:.4f}")
wait_for_step("setter XY move")

controller.mdi("G91")
wait_for_step("relative mode")
controller.mdi(f"G38.2 Z-{search_distance:.4f} F{velocity * 4.0:.4f}")
wait_for_step("fast probe")
controller.mdi("G91 G0 Z2.0000")
wait_for_step("probe retract")
controller.mdi(f"G38.2 Z-4.0000 F{velocity:.4f}")
wait_for_step("slow probe")
controller.mdi("G90")
wait_for_step("absolute mode")
controller.mdi("(DEBUG, SETTER_HEIGHT=#5063)")
result = controller.wait_complete(10)
if result == -1 or result == linuxcnc.RCS_ERROR:
    raise RuntimeError("Unable to read the probed setter height.")
setter_height = None
for _ in range(20):
    error = errors.poll()
    if not error:
        time.sleep(0.05)
        continue
    match = re.search(r"SETTER_HEIGHT\\s*=\\s*(-?\\d+(?:\\.\\d+)?)", str(error[1]))
    if match:
        setter_height = float(match.group(1))
        break
if setter_height is None:
    raise RuntimeError("LinuxCNC did not report the probed setter height.")
controller.mdi("G91 G0 Z5.0000")
wait_for_step("final retract")
print(json.dumps({{"setter_height": setter_height}}, separators=(",", ":")))
"""
    result = subprocess.run(
        ["/opt/LinuxCNC/.venv/bin/python", "-c", program],
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
            elif payload["operation"] == "linuxcnc_jog" and set(payload) == {"operation", "action", "axis", "velocity"} and isinstance(payload["action"], str) and isinstance(payload["axis"], str):
                response = linuxcnc_jog(payload["action"], payload["axis"], float(payload["velocity"]))
            elif payload["operation"] == "linuxcnc_spindle_command" and set(payload) == {"operation", "action", "rpm"} and isinstance(payload["action"], str):
                response = linuxcnc_spindle_command(payload["action"], float(payload["rpm"]))
            elif payload["operation"] == "linuxcnc_touch_off_axis" and set(payload) == {"operation", "axis", "value"} and isinstance(payload["axis"], str):
                response = linuxcnc_touch_off_axis(payload["axis"], float(payload["value"]))
            elif payload["operation"] == "linuxcnc_open_program" and set(payload) == {"operation", "path"} and isinstance(payload["path"], str):
                response = linuxcnc_open_program(payload["path"])
            elif payload["operation"] == "linuxcnc_probe_z" and set(payload) == {"operation", "target", "velocity"}:
                response = linuxcnc_probe_z(float(payload["target"]), float(payload["velocity"]))
            elif payload["operation"] == "linuxcnc_touch_off_z" and set(payload) == {"operation", "target", "velocity", "plate_thickness", "retract"}:
                response = linuxcnc_touch_off_z(
                    float(payload["target"]),
                    float(payload["velocity"]),
                    float(payload["plate_thickness"]),
                    float(payload["retract"]),
                )
            elif payload["operation"] == "linuxcnc_move_tool_setter" and set(payload) == {"operation", "x", "y", "z"}:
                response = linuxcnc_move_tool_setter(
                    float(payload["x"]),
                    float(payload["y"]),
                    float(payload["z"]),
                )
            elif payload["operation"] == "linuxcnc_run_subroutine" and set(payload) == {"operation", "ocode"} and isinstance(payload["ocode"], str):
                response = linuxcnc_run_subroutine(payload["ocode"])
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
            threading.Thread(
                target=_serve_connection,
                args=(connection,),
                daemon=True,
            ).start()
    finally:
        listener.close()
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass


def _serve_connection(connection):
    """Handle one helper request without blocking other socket clients."""
    with connection:
        try:
            _handle_connection(connection)
        except OSError:
            return


def main():
    """Start the local helper on the configured Unix-domain socket."""
    parser = argparse.ArgumentParser(description="LinuxCNC read-only HAL helper")
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    serve(args.socket)


if __name__ == "__main__":
    main()