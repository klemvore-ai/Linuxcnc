"""Validation rules for machine-agent lifecycle and file requests."""

from pathlib import Path
import ipaddress
import re


class AgentPolicyError(ValueError):
    """Raised when an agent request violates the allowed operation policy."""

    pass


HAL_PIN_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
MAX_HAL_VALUE_PINS = 16
ALLOWED_O_CODE_SUBROUTINES = {
    "O100", "O101", "O102", "O103", "O104", "O105",
    "O106", "O107", "O108", "O109", "O110", "O111",
    "O112", "O113",
}
# LinuxCNC expects O-code files WITHOUT .ngc extension (e.g., "O100", not "O100.ngc")
O_CODE_FILENAME_PATTERN = re.compile(r"(?i)^o(100|101|102|103|104|105|106|107|108|109|110|111|112|113)$")
O_CODE_DEFINITION_PATTERN = re.compile(r"(?im)^\s*o\s*(100|101|102|103|104|105|106|107|108|109|110|111|112|113)\s+sub\b")


def validate_o_code_subroutine(payload):
    """Validate one allowlisted O-code subroutine call."""
    if not isinstance(payload, dict) or set(payload) != {"ocode"}:
        raise AgentPolicyError("Subroutine fields are invalid.")
    ocode = payload["ocode"]
    if not isinstance(ocode, str):
        raise AgentPolicyError("Subroutine name must be a string.")
    normalized = ocode.strip().upper()
    if normalized not in ALLOWED_O_CODE_SUBROUTINES:
        raise AgentPolicyError("Subroutine is not approved for secure execution.")
    return normalized


def validate_macro_upload(payload):
    """Validate one allowlisted O-code macro upload."""
    required = {"filename", "sha256", "content"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise AgentPolicyError("Macro upload fields are invalid.")
    filename = payload["filename"]
    if not isinstance(filename, str) or not O_CODE_FILENAME_PATTERN.fullmatch(Path(filename).name):
        raise AgentPolicyError("Macro filename must be an approved O-code (O100-O113, no extension).")
    if not isinstance(payload["sha256"], str) or len(payload["sha256"]) != 64 or any(character not in "0123456789abcdef" for character in payload["sha256"].lower()):
        raise AgentPolicyError("Macro checksum is invalid.")
    if not isinstance(payload["content"], str):
        raise AgentPolicyError("Macro content must be text.")
    match = O_CODE_DEFINITION_PATTERN.search(payload["content"])
    expected = Path(filename).stem.upper()
    if match is None or f"O{match.group(1)}" != expected:
        raise AgentPolicyError("Macro content must define the matching O-code subroutine.")
    return expected


def validate_hal_value_request(payload):
    """Validate one bounded read-only HAL value snapshot request."""
    if not isinstance(payload, dict) or set(payload) != {"pins"}:
        raise AgentPolicyError("HAL value request fields are invalid.")
    pins = payload["pins"]
    if not isinstance(pins, list) or not 1 <= len(pins) <= MAX_HAL_VALUE_PINS:
        raise AgentPolicyError(f"Request between 1 and {MAX_HAL_VALUE_PINS} HAL pins.")
    if not all(isinstance(pin, str) and HAL_PIN_NAME_PATTERN.fullmatch(pin) for pin in pins):
        raise AgentPolicyError("HAL pin name is invalid.")
    if len(set(pins)) != len(pins):
        raise AgentPolicyError("HAL pin names must be unique.")
    return pins


def validate_action(payload, allowed_config_roots):
    """Validate a lifecycle payload and return its action plus normalized INI."""
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
        raise AgentPolicyError("A lifecycle action is required.")

    action = payload["action"]
    if action == "graceful_shutdown" or action == "recovery_shutdown":
        if set(payload) != {"action"}:
            raise AgentPolicyError("This action does not accept additional fields.")
        return action, None

    if action != "start" or set(payload) != {"action", "ini_path"}:
        raise AgentPolicyError("Unsupported lifecycle action.")
    if not isinstance(payload["ini_path"], str) or not payload["ini_path"]:
        raise AgentPolicyError("A configuration path is required.")

    ini_path = Path(payload["ini_path"]).expanduser().resolve(strict=False)
    if ini_path.suffix.lower() != ".ini":
        raise AgentPolicyError("Only INI configuration files may be started.")
    if not ini_path.is_file():
        raise AgentPolicyError("The requested INI file does not exist.")

    roots = [Path(root).expanduser().resolve(strict=False) for root in allowed_config_roots]
    if not any(ini_path == root or root in ini_path.parents for root in roots):
        raise AgentPolicyError("The requested INI file is outside approved roots.")
    return action, str(ini_path)


def validate_upload(payload, config_root, gcode_root):
    """Validate an upload and return its kind plus safe destination path."""
    if not isinstance(payload, dict):
        raise AgentPolicyError("An upload object is required.")
    required = {"kind", "relative_path", "sha256", "content"}
    if set(payload) != required:
        raise AgentPolicyError("Upload fields are invalid.")

    kind = payload["kind"]
    if kind not in {"config", "gcode"}:
        raise AgentPolicyError("Upload kind is not supported.")
    if not all(isinstance(payload[field], str) for field in required):
        raise AgentPolicyError("Upload fields must be strings.")
    relative_path = Path(payload["relative_path"])
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise AgentPolicyError("Upload path must stay inside its approved root.")

    allowed_suffixes = {".ini", ".hal", ".tbl", ".var", ".ngc", ".gcode"}
    if relative_path.suffix.lower() not in allowed_suffixes:
        raise AgentPolicyError("File type is not supported.")
    if kind == "config" and relative_path.suffix.lower() in {".ngc", ".gcode"}:
        raise AgentPolicyError("G-code must use the G-code upload area.")
    if kind == "gcode" and relative_path.suffix.lower() not in {".ngc", ".gcode"}:
        raise AgentPolicyError("Only G-code files may use the G-code upload area.")

    root = Path(config_root if kind == "config" else gcode_root).expanduser().resolve(strict=False)
    destination = (root / relative_path).resolve(strict=False)
    if destination != root and root not in destination.parents:
        raise AgentPolicyError("Upload path must stay inside its approved root.")
    if len(payload["sha256"]) != 64 or any(character not in "0123456789abcdef" for character in payload["sha256"].lower()):
        raise AgentPolicyError("Upload checksum is invalid.")
    return kind, destination


def validate_activation(payload, config_root):
    """Validate a pending-to-active configuration swap and return both paths."""
    if not isinstance(payload, dict) or set(payload) != {"action", "pending_path", "active_path"}:
        raise AgentPolicyError("Activation fields are invalid.")
    if payload["action"] not in {"activate_pending", "activate_and_restart"}:
        raise AgentPolicyError("Unsupported configuration action.")
    if not isinstance(payload["pending_path"], str) or not isinstance(payload["active_path"], str):
        raise AgentPolicyError("Configuration paths must be strings.")

    root = Path(config_root).expanduser().resolve(strict=False)
    paths = []
    for field in ("pending_path", "active_path"):
        relative_path = Path(payload[field])
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise AgentPolicyError("Configuration paths must stay inside the approved root.")
        if relative_path.suffix.lower() != ".ini":
            raise AgentPolicyError("Only INI files may be activated.")
        path = (root / relative_path).resolve(strict=False)
        if path != root and root not in path.parents:
            raise AgentPolicyError("Configuration paths must stay inside the approved root.")
        paths.append(path)
    if paths[0] == paths[1] or not paths[0].is_file():
        raise AgentPolicyError("A distinct pending INI file is required.")
    return paths[0], paths[1]


def validate_restart_activation(payload, config_root):
    """Validate an activation request that also restarts LinuxCNC."""
    pending_path, active_path = validate_activation(payload, config_root)
    if payload["action"] != "activate_and_restart":
        raise AgentPolicyError("Unsupported restart activation action.")
    return pending_path, active_path


def validate_mesa_identification(payload):
    """Validate the limited board and address data needed by mesaflash."""
    if not isinstance(payload, dict) or set(payload) != {"board", "address"}:
        raise AgentPolicyError("Mesa identification fields are invalid.")
    board = payload["board"]
    address = payload["address"]
    if not isinstance(board, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", board):
        raise AgentPolicyError("Mesa board identifier is invalid.")
    if not isinstance(address, str):
        raise AgentPolicyError("Mesa address must be an IP address.")
    try:
        ipaddress.ip_address(address)
    except ValueError as error:
        raise AgentPolicyError("Mesa address must be an IP address.") from error
    return board, address


def validate_tool_table_path(relative_path, config_root):
    """Resolve a relative tool-table path inside the approved config root."""
    if not isinstance(relative_path, str):
        raise AgentPolicyError("Tool-table path must be a string.")
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".tbl":
        raise AgentPolicyError("Tool-table path must be a relative .tbl path.")
    root = Path(config_root).expanduser().resolve(strict=False)
    destination = (root / path).resolve(strict=False)
    if destination != root and root not in destination.parents:
        raise AgentPolicyError("Tool-table path must stay inside the approved root.")
    return destination


def validate_gcode_path(relative_path, gcode_root):
    """Resolve a relative G-code path inside the approved G-code root."""
    if not isinstance(relative_path, str):
        raise AgentPolicyError("G-code path must be a string.")
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in {".ngc", ".gcode"}:
        raise AgentPolicyError("G-code path must be a relative .ngc or .gcode path.")
    root = Path(gcode_root).expanduser().resolve(strict=False)
    destination = (root / path).resolve(strict=False)
    if destination != root and root not in destination.parents:
        raise AgentPolicyError("G-code path must stay inside the approved root.")
    if not destination.is_file():
        raise AgentPolicyError("The requested G-code file does not exist.")
    return destination


def validate_override(payload):
    """Validate a runtime feed or spindle override request."""
    if not isinstance(payload, dict) or set(payload) != {"kind", "value"}:
        raise AgentPolicyError("Override fields are invalid.")
    if payload["kind"] not in {"feed", "spindle"}:
        raise AgentPolicyError("Override kind is not supported.")
    if not isinstance(payload["value"], (int, float)) or isinstance(payload["value"], bool):
        raise AgentPolicyError("Override value must be numeric.")
    if not 0.0 <= payload["value"] <= 2.0:
        raise AgentPolicyError("Override value must be between 0 and 2.")
    return payload["kind"], float(payload["value"])


def validate_pid(payload):
    """Validate bounded spindle PID field updates."""
    fields = {"p", "i", "d", "ff0", "ff1"}
    if not isinstance(payload, dict) or set(payload) != fields | {"value"}:
        raise AgentPolicyError("PID fields are invalid.")
    if payload.get("p") not in {True, False} or payload.get("i") not in {True, False}:
        raise AgentPolicyError("PID selection is invalid.")
    selected = next((field for field in fields if payload.get(field) is True), None)
    if selected is None or any(payload.get(field) is True for field in fields if field != selected):
        raise AgentPolicyError("Exactly one PID field must be selected.")
    value = payload["value"]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not -10000.0 <= value <= 10000.0:
        raise AgentPolicyError("PID value is outside the allowed range.")
    if selected in {"p", "i", "d"} and value < 0:
        raise AgentPolicyError("PID gains cannot be negative.")
    return selected, float(value)


def validate_firmware(payload):
    """Validate one bounded Mesa firmware image request."""
    required = {"board", "address", "filename", "sha256", "content"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise AgentPolicyError("Firmware request fields are invalid.")
    board, address = validate_mesa_identification({"board": payload["board"], "address": payload["address"]})
    filename = payload["filename"]
    if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix.lower() not in {".bit", ".bin"}:
        raise AgentPolicyError("Firmware filename must be a single .bit or .bin file.")
    if not isinstance(payload["sha256"], str) or len(payload["sha256"]) != 64 or any(character not in "0123456789abcdef" for character in payload["sha256"].lower()):
        raise AgentPolicyError("Firmware checksum is invalid.")
    if not isinstance(payload["content"], str):
        raise AgentPolicyError("Firmware content is invalid.")
    return board, address, filename


def validate_machine_command(payload):
    """Validate one explicitly named operator machine command."""
    if not isinstance(payload, dict) or set(payload) != {"command"}:
        raise AgentPolicyError("Machine command fields are invalid.")
    allowed = {
        "abort",
        "power_on",
        "power_off",
        "estop",
        "estop_reset",
        "override_limits",
        "coolant_mist_on",
        "coolant_flood_on",
        "coolant_off",
        "program_start",
        "program_pause_resume",
        "tool_change_ack",
        "mode_manual",
        "mode_auto",
        "home_all",
        "unhome_all",
    }
    if payload["command"] not in allowed:
        raise AgentPolicyError("Machine command is not supported.")
    return payload["command"]


def validate_spindle_command(payload):
    """Validate one named spindle direction command and bounded RPM."""
    if not isinstance(payload, dict) or set(payload) != {"action", "rpm"}:
        raise AgentPolicyError("Spindle command fields are invalid.")
    action = payload["action"]
    rpm = payload["rpm"]
    if action not in {"forward", "reverse", "stop"}:
        raise AgentPolicyError("Spindle action is not supported.")
    if not isinstance(rpm, (int, float)) or isinstance(rpm, bool) or not 0.0 <= rpm <= 100000.0:
        raise AgentPolicyError("Spindle RPM is outside the allowed range.")
    if action != "stop" and rpm < 1.0:
        raise AgentPolicyError("Spindle RPM must be at least 1.")
    return action, float(rpm)


def validate_tool_change(payload):
    """Validate one bounded tool number for the fixed tool-change sequence."""
    if not isinstance(payload, dict) or set(payload) != {"tool"}:
        raise AgentPolicyError("Tool-change fields are invalid.")
    tool = payload["tool"]
    if not isinstance(tool, int) or isinstance(tool, bool) or not 0 <= tool <= 9999:
        raise AgentPolicyError("Tool number is outside the allowed range.")
    return tool


def validate_home_axis(payload):
    """Validate a named axis homing request."""
    if not isinstance(payload, dict) or set(payload) != {"axis"}:
        raise AgentPolicyError("Axis homing fields are invalid.")
    axis = payload["axis"]
    if not isinstance(axis, str) or axis.upper() not in {"X", "Y", "Z", "A", "B", "C"}:
        raise AgentPolicyError("Axis name is not supported.")
    return axis.upper()


def validate_jog_operation(payload):
    """Validate a bounded jog start or stop request without accepting MDI text."""
    if not isinstance(payload, dict) or set(payload) not in ({"action", "axis"}, {"action", "axis", "velocity"}):
        raise AgentPolicyError("Jog fields are invalid.")
    action = payload["action"]
    axis = payload["axis"]
    if action not in {"start", "stop"}:
        raise AgentPolicyError("Jog action is not supported.")
    if not isinstance(axis, str) or axis.upper() not in {"X", "Y", "Z", "A", "B", "C"}:
        raise AgentPolicyError("Jog axis is not supported.")
    if action == "stop":
        return action, axis.upper(), 0.0
    if set(payload) != {"action", "axis", "velocity"}:
        raise AgentPolicyError("Jog start requires a velocity.")
    velocity = payload["velocity"]
    if not isinstance(velocity, (int, float)) or isinstance(velocity, bool):
        raise AgentPolicyError("Jog velocity must be numeric.")
    velocity = float(velocity)
    if not -10000.0 <= velocity <= 10000.0 or abs(velocity) < 0.001:
        raise AgentPolicyError("Jog velocity is outside the allowed range.")
    return action, axis.upper(), velocity


def validate_touch_off_axis(payload):
    """Validate a named axis work-offset zero operation."""
    if not isinstance(payload, dict) or set(payload) != {"axis", "value"}:
        raise AgentPolicyError("Axis touch-off fields are invalid.")
    axis = payload["axis"]
    value = payload["value"]
    if not isinstance(axis, str) or axis.upper() not in {"X", "Y", "Z"}:
        raise AgentPolicyError("Axis touch-off supports only X, Y, and Z.")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not -10000.0 <= float(value) <= 10000.0:
        raise AgentPolicyError("Axis touch-off value is outside the allowed range.")
    return axis.upper(), float(value)


def validate_probe_operation(payload):
    """Validate one bounded Z probe operation without accepting MDI text."""
    if not isinstance(payload, dict) or set(payload) != {"operation", "target", "velocity"}:
        raise AgentPolicyError("Probe operation fields are invalid.")
    if payload["operation"] != "probe_z":
        raise AgentPolicyError("Probe operation is not supported.")
    if not all(isinstance(payload[field], (int, float)) and not isinstance(payload[field], bool) for field in ("target", "velocity")):
        raise AgentPolicyError("Probe target and velocity must be numeric.")
    if not -10000.0 <= payload["target"] < 0.0:
        raise AgentPolicyError("Probe target must be a bounded downward Z distance.")
    if not 1.0 <= payload["velocity"] <= 10000.0:
        raise AgentPolicyError("Probe velocity is outside the allowed range.")
    return float(payload["target"]), float(payload["velocity"])


def validate_touch_off_z(payload):
    """Validate bounded parameters for one touch-plate Z work-offset operation."""
    required = {"target", "velocity", "plate_thickness", "retract"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise AgentPolicyError("Touch-off fields are invalid.")
    if not all(isinstance(payload[field], (int, float)) and not isinstance(payload[field], bool) for field in required):
        raise AgentPolicyError("Touch-off values must be numeric.")
    target = float(payload["target"])
    velocity = float(payload["velocity"])
    plate_thickness = float(payload["plate_thickness"])
    retract = float(payload["retract"])
    if not -10000.0 <= target < 0.0:
        raise AgentPolicyError("Touch-off target must be a bounded downward Z distance.")
    if not 1.0 <= velocity <= 10000.0:
        raise AgentPolicyError("Touch-off velocity is outside the allowed range.")
    if not -1000.0 <= plate_thickness <= 1000.0:
        raise AgentPolicyError("Touch-plate thickness is outside the allowed range.")
    if not 0.1 <= retract <= 100.0:
        raise AgentPolicyError("Touch-off retract is outside the allowed range.")
    return target, velocity, plate_thickness, retract


def validate_tool_setter_move(payload):
    """Validate bounded absolute coordinates for a tool-setter move."""
    required = {"x", "y", "z"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise AgentPolicyError("Tool-setter move fields are invalid.")
    if not all(isinstance(payload[field], (int, float)) and not isinstance(payload[field], bool) for field in required):
        raise AgentPolicyError("Tool-setter coordinates must be numeric.")
    if any(not -10000.0 <= payload[field] <= 10000.0 for field in required):
        raise AgentPolicyError("Tool-setter coordinate is outside the allowed range.")
    return tuple(float(payload[field]) for field in ("x", "y", "z"))


def validate_tool_setter_calibration(payload):
    """Validate bounded parameters for the complete tool-setter probe cycle."""
    required = {"x", "y", "velocity", "search_distance"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise AgentPolicyError("Tool-setter calibration fields are invalid.")
    if not all(isinstance(payload[field], (int, float)) and not isinstance(payload[field], bool) for field in required):
        raise AgentPolicyError("Tool-setter calibration values must be numeric.")
    x = float(payload["x"])
    y = float(payload["y"])
    velocity = float(payload["velocity"])
    search_distance = float(payload["search_distance"])
    if not -10000.0 <= x <= 10000.0 or not -10000.0 <= y <= 10000.0:
        raise AgentPolicyError("Tool-setter coordinates are outside the allowed range.")
    if not 1.0 <= velocity <= 10000.0:
        raise AgentPolicyError("Tool-setter velocity is outside the allowed range.")
    if 0.1 <= search_distance <= 1000.0:
        return x, y, velocity, search_distance
    raise AgentPolicyError("Tool-setter search distance is outside the allowed range.")