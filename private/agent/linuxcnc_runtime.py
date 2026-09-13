"""Operating-system adapter used by the LinuxCNC machine agent."""

import hashlib
import importlib
import json
import re
import socket
import subprocess
import time
from agent.state_policy import MachineStateError
from agent.policy import ALLOWED_O_CODE_SUBROUTINES
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class LinuxCNCRuntime:
    """Execute approved local LinuxCNC and atomic file operations."""

    def __init__(self, linuxcnc_executable="linuxcnc", python_executable="python3", hal_helper_socket=None):
        """Configure executable names without shell interpretation."""
        self.linuxcnc_executable = linuxcnc_executable
        self.python_executable = python_executable
        self.hal_helper_socket = hal_helper_socket

    def is_running(self):
        """Return whether the LinuxCNC process is present."""
        result = subprocess.run(
            ["pgrep", "-x", self.linuxcnc_executable],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def machine_state(self):
        """Return a minimal LinuxCNC state snapshot for agent policy checks."""
        if self.hal_helper_socket:
            helper_status = self._linuxcnc_status_with_helper()
            if helper_status.get("error"):
                return {"running": self.is_running(), "idle": False, "state_available": False}
            return helper_status["machine_state"]
        if not self.is_running():
            return {"running": False, "idle": False}
        try:
            linuxcnc = importlib.import_module("linuxcnc")
            status = linuxcnc.stat()
            status.poll()
        except Exception:
            return {
                "running": True,
                "idle": False,
                "machine_on": False,
                "state_available": False,
            }
        spindle_rpm = 0.0
        spindle_list = getattr(status, "spindle", ())
        if spindle_list and isinstance(spindle_list[0], dict):
            spindle0 = spindle_list[0]
            commanded = spindle0.get("commanded_speed")
            if commanded is None:
                commanded = spindle0.get("speed", 0.0)
            spindle_rpm = abs(float(commanded))
        else:
            spindle_rpm = abs(getattr(status, "spindle_speed", 0.0))
        return {
            "running": True,
            "idle": getattr(status, "interp_state", None) == linuxcnc.INTERP_IDLE,
            "machine_on": getattr(status, "task_state", None) == linuxcnc.STATE_ON,
            "state_available": True,
        }

    def telemetry(self):
        """Return a bounded operator telemetry snapshot from LinuxCNC status."""
        if self.hal_helper_socket:
            helper_status = self._linuxcnc_status_with_helper()
            if helper_status.get("error"):
                raise RuntimeError(helper_status["error"])
            return helper_status["telemetry"]
        linuxcnc = importlib.import_module("linuxcnc")
        status = linuxcnc.stat()
        status.poll()
        positions = getattr(status, "actual_position", (0.0, 0.0, 0.0))
        interpreter_state = getattr(status, "interp_state", None)
        task_mode = getattr(status, "task_mode", None)
        program_status = (
            "IDLE" if interpreter_state == linuxcnc.INTERP_IDLE
            else "PAUSED" if interpreter_state == linuxcnc.INTERP_PAUSED
            else "RUNNING"
        )
        mode = (
            "MANUAL" if task_mode == linuxcnc.MODE_MANUAL
            else "AUTO" if task_mode == linuxcnc.MODE_AUTO
            else "MDI" if task_mode == linuxcnc.MODE_MDI
            else "UNKNOWN"
        )
        distance_to_go = getattr(status, "dtg", (0.0, 0.0, 0.0))
        spindle_rpm = 0.0
        spindle_list = getattr(status, "spindle", ())
        if spindle_list and isinstance(spindle_list[0], dict):
            spindle0 = spindle_list[0]
            commanded = spindle0.get("commanded_speed")
            if commanded is None:
                commanded = spindle0.get("speed", 0.0)
            spindle_rpm = abs(float(commanded))
        else:
            spindle_rpm = abs(getattr(status, "spindle_speed", 0.0))
        return {
            "X": positions[0] if len(positions) > 0 else 0.0,
            "Y": positions[1] if len(positions) > 1 else 0.0,
            "Z": positions[2] if len(positions) > 2 else 0.0,
            "FEED": getattr(status, "current_vel", 0.0) * 60.0,
            "RPM": spindle_rpm,
            "FEED_OVR": getattr(status, "feedrate", 1.0),
            "SPINDLE_OVR": getattr(status, "spindle_scale", 1.0),
            "HOMED_COUNT": sum(1 for value in getattr(status, "homed", []) if value),
            "MACHINE_ON": getattr(status, "task_state", None) == linuxcnc.STATE_ON,
            "ESTOP_ACTIVATED": getattr(status, "task_state", None) == linuxcnc.STATE_ESTOP,
            "JOINTS": int(getattr(status, "joints", getattr(status, "axes", 3))),
            "LINE": getattr(status, "motion_line", 0),
            "STATUS": program_status,
            "MODE": mode,
            "PROGRAM_LOADED": bool(getattr(status, "file", None)),
            "ABSOLUTE_X": positions[0] if len(positions) > 0 else 0.0,
            "ABSOLUTE_Y": positions[1] if len(positions) > 1 else 0.0,
            "ABSOLUTE_Z": positions[2] if len(positions) > 2 else 0.0,
            "DTG_X": distance_to_go[0] if len(distance_to_go) > 0 else 0.0,
            "DTG_Y": distance_to_go[1] if len(distance_to_go) > 1 else 0.0,
            "DTG_Z": distance_to_go[2] if len(distance_to_go) > 2 else 0.0,
        }

    def show_hal_pins(self):
        """Return the fixed HAL pin listing without accepting shell input."""
        if self.hal_helper_socket:
            return self._show_hal_with_helper(b"show_hal_pins\n")
        result = subprocess.run(
            ["halcmd", "show", "pin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return {"output": result.stdout[-1024 * 1024:], "error": result.stderr[-65536:]}

    def show_hal_components(self):
        """Return the fixed HAL component listing without accepting shell input."""
        if self.hal_helper_socket:
            return self._show_hal_with_helper(b"show_hal_components\n")
        result = subprocess.run(
            ["halcmd", "show", "comp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return {"output": result.stdout[-1024 * 1024:], "error": result.stderr[-65536:]}

    def read_hal_values(self, pins):
        """Read one bounded snapshot of validated HAL pin values."""
        if self.hal_helper_socket:
            return self._read_hal_values_with_helper(pins)
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
        return {"values": values, "error": "\n".join(errors)[-65536:]}

    def _show_hal_with_helper(self, request):
        """Request one fixed HAL operation over the local helper socket."""
        socket_family = getattr(socket, "AF_UNIX", None)
        if socket_family is None:
            return {"output": "", "error": "HAL helper unavailable: Unix sockets are not supported."}
        try:
            with socket.socket(socket_family, socket.SOCK_STREAM) as connection:
                connection.settimeout(10)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024 + 65536:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("HAL helper returned an invalid response.")
            output = payload.get("output", "")
            error = payload.get("error", "")
            if not isinstance(output, str) or not isinstance(error, str):
                raise ValueError("HAL helper returned invalid output.")
            return {"output": output[-1024 * 1024:], "error": error[-65536:]}
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            return {"output": "", "error": f"HAL helper unavailable: {error}"}

    def _read_hal_values_with_helper(self, pins):
        """Request one validated HAL value snapshot from the local helper."""
        request = json.dumps({"operation": "read_hal_values", "pins": pins}, separators=(",", ":")).encode("utf-8")
        try:
            with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                connection.settimeout(10)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            values = payload.get("values", {})
            error = payload.get("error", "")
            if not isinstance(values, dict) or not isinstance(error, str) or not all(isinstance(pin, str) and isinstance(value, str) for pin, value in values.items()):
                raise ValueError("HAL helper returned invalid values.")
            return {"values": values, "error": error[-65536:]}
        except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            return {"values": {}, "error": f"HAL helper unavailable: {error}"}

    def _linuxcnc_status_with_helper(self):
        """Request the LinuxCNC owner's read-only status snapshot through the helper."""
        request = json.dumps({"operation": "linuxcnc_status"}, separators=(",", ":")).encode("utf-8")
        try:
            with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                connection.settimeout(10)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            machine_state = payload.get("machine_state")
            telemetry = payload.get("telemetry")
            error = payload.get("error", "")
            if not isinstance(machine_state, dict) or not isinstance(telemetry, dict) or not isinstance(error, str):
                raise ValueError("HAL helper returned invalid LinuxCNC status.")
            return {"machine_state": machine_state, "telemetry": telemetry, "error": error}
        except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            return {"machine_state": {}, "telemetry": {}, "error": f"HAL helper unavailable: {error}"}

    def _linuxcnc_machine_command_with_helper(self, command):
        """Request one fixed LinuxCNC machine-state command through the local helper."""
        request = json.dumps(
            {"operation": "linuxcnc_machine_command", "command": command},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                connection.settimeout(10)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("HAL helper returned invalid LinuxCNC command response.")
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            if payload.get("accepted") is not True:
                raise ValueError("HAL helper rejected LinuxCNC command.")
            return payload
        except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"HAL helper unavailable: {error}") from error

    def identify_mesa(self, board, address):
        """Run the fixed read-only Mesa identification operation."""
        result = subprocess.run(
            ["mesaflash", "--device", board, "--addr", address, "--readhmid"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {"output": result.stdout[-1024 * 1024:], "error": result.stderr[-65536:]}

    def read_launch_log(self, log_path="/tmp/linuxcnc_launch.log"):
        """Read only the configured LinuxCNC launch log with bounded output."""
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
                return {"content": log_file.read()[-1024 * 1024:]}
        except OSError as error:
            return {"content": "", "error": str(error)}

    def read_tool_table(self, path):
        """Read one validated tool-table file with bounded text output."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as tool_file:
                return {"content": tool_file.read(1024 * 1024 + 1)}
        except OSError as error:
            return {"content": "", "error": str(error)}

    def read_gcode(self, path):
        """Read one validated G-code file with bounded text output."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as gcode_file:
                return {"content": gcode_file.read(4 * 1024 * 1024 + 1)}
        except OSError as error:
            return {"content": "", "error": str(error)}

    def list_gcode(self, root):
        """List bounded relative G-code files below the approved root."""
        files = []
        root = os.path.abspath(root)
        for current_root, _, names in os.walk(root):
            for name in sorted(names):
                if Path(name).suffix.lower() in {".ngc", ".gcode"}:
                    files.append(os.path.relpath(os.path.join(current_root, name), root).replace(os.sep, "/"))
                    if len(files) >= 1000:
                        return files
        return files

    def list_macros(self, root):
        """List the approved O-code macro files available to LinuxCNC."""
        root_path = Path(root).expanduser().resolve(strict=False)
        macros = []
        if not root_path.is_dir():
            return macros
        for path in sorted(root_path.glob("*.ngc"), key=lambda item: item.stem):
            # LinuxCNC resolves "O100 call" to the file "100.ngc"
            if not path.stem.isdigit():
                continue
            ocode = f"O{path.stem}"
            if ocode not in ALLOWED_O_CODE_SUBROUTINES:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not re.search(rf"(?im)^\s*o\s*{re.escape(path.stem)}\s+sub\b", content):
                continue
            description = ""
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("(") and stripped.endswith(")"):
                    description = stripped[1:-1].strip()
                    break
            macros.append({
                "ocode": ocode,
                "filename": path.name,
                "size": path.stat().st_size,
                "description": description,
            })
        return macros

    def upload_macro(self, root, filename, content, expected_sha256):
        """Upload one validated O-code macro into the approved G-code root."""
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256.lower():
            raise ValueError("Macro checksum does not match content.")
        root_path = Path(root).expanduser().resolve(strict=False)
        destination = (root_path / filename).resolve(strict=False)
        if destination.parent != root_path:
            raise ValueError("Macro path must stay inside the approved G-code root.")
        root_path.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_name(destination.name + ".tmp")
        try:
            with open(temporary_path, "wb") as macro_file:
                macro_file.write(content)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return {"filename": filename, "sha256": actual_sha256}

    def reload_tool_table(self):
        """Reload tool offsets through LinuxCNC while the interpreter is idle."""
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state.get("running") or not state.get("idle"):
            raise MachineStateError("Tool-table reload requires running and idle LinuxCNC.")
        linuxcnc.command().load_tool_table()

    def open_program(self, path):
        """Open one validated G-code file through LinuxCNC's command API."""
        if self.hal_helper_socket:
            state = self.machine_state()
            if not state.get("running") or not state.get("idle"):
                raise MachineStateError("Program loading requires running and idle LinuxCNC.")
            request = json.dumps(
                {"operation": "linuxcnc_open_program", "path": str(path)},
                separators=(",", ":"),
            ).encode("utf-8")
            try:
                with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                    connection.settimeout(15)
                    connection.connect(self.hal_helper_socket)
                    connection.sendall(request)
                    response = b""
                    while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        response += chunk
                payload = json.loads(response.decode("utf-8"))
                if payload.get("error"):
                    raise MachineStateError(payload["error"])
                if payload.get("accepted") is not True:
                    raise MachineStateError("LinuxCNC did not accept program open.")
                return
            except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state["running"] or not state["idle"]:
            raise MachineStateError("Program loading requires running and idle LinuxCNC.")
        linuxcnc.command().program_open(path)

    def set_override(self, kind, value):
        """Apply a bounded runtime feed or spindle override through LinuxCNC."""
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state["running"]:
            raise MachineStateError("Runtime override requires running LinuxCNC.")
        command = linuxcnc.command()
        if kind == "feed":
            command.feedrate(value)
        elif kind == "spindle":
            command.spindleoverride(value)
        else:
            raise ValueError("Unsupported override kind.")

    def set_pid(self, field, value):
        """Set one approved spindle PID parameter while LinuxCNC is idle."""
        if not self.is_running():
            raise RuntimeError("LinuxCNC must be running for PID tuning.")
        linuxcnc = importlib.import_module("linuxcnc")
        status = linuxcnc.stat()
        status.poll()
        if getattr(status, "interp_state", None) != linuxcnc.INTERP_IDLE:
            raise RuntimeError("PID tuning requires LinuxCNC to be idle.")
        hal = importlib.import_module("hal")
        hal.set_p(f"pid.s.{field}", value)

    def flash_firmware(self, board, address, filename, content, expected_sha256):
        """Flash one verified Mesa image while LinuxCNC is stopped."""
        if self.is_running():
            raise RuntimeError("LinuxCNC must be stopped before firmware flashing.")
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256.lower():
            raise ValueError("Firmware checksum does not match content.")
        temporary_path = os.path.join(tempfile.gettempdir(), filename)
        try:
            with open(temporary_path, "wb") as firmware_file:
                firmware_file.write(content)
            write_result = subprocess.run(
                ["mesaflash", "--device", board, "--addr", address, "--write", temporary_path],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if write_result.returncode != 0:
                raise RuntimeError(write_result.stderr[-65536:] or "Mesa firmware write failed.")
            reload_result = subprocess.run(
                ["mesaflash", "--device", board, "--addr", address, "--reload"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if reload_result.returncode != 0:
                raise RuntimeError(reload_result.stderr[-65536:] or "Mesa FPGA reload failed.")
            return {"output": reload_result.stdout[-65536:], "error": reload_result.stderr[-65536:]}
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def stage_application_update(self, content, expected_sha256, staging_root):
        """Validate and stage an application archive without executing it."""
        from agent.archive_transfer import validate_application_archive
        archive_path = validate_application_archive(content, expected_sha256)
        os.makedirs(staging_root, exist_ok=True)
        staged_path = os.path.join(staging_root, "linuxcnc-update.tar.gz")
        temporary_path = os.path.join(staging_root, ".linuxcnc-update.tar.gz.tmp")
        try:
            shutil.copyfile(archive_path, temporary_path)
            os.replace(temporary_path, staged_path)
        finally:
            if os.path.exists(archive_path):
                os.unlink(archive_path)
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
        return {"staged": True, "path": staged_path, "sha256": expected_sha256.lower()}

    def application_update_status(self, staging_root):
        """Return staged update metadata and the last local apply result without executing code."""
        staging = Path(staging_root)
        archive = staging / "linuxcnc-update.tar.gz"
        status_file = staging / "last-apply.json"
        result = {"staged": archive.is_file()}
        if archive.is_file():
            result["staged_size"] = archive.stat().st_size
            result["staged_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        if status_file.is_file():
            try:
                status = json.loads(status_file.read_text(encoding="utf-8"))
                if isinstance(status, dict):
                    result["last_apply"] = status
            except (OSError, json.JSONDecodeError):
                result["last_apply"] = {"status": "unknown"}
        return result

    def deployment_preflight(self, staging_root, application_root="/opt/LinuxCNC"):
        """Report read-only deployment prerequisites from the agent service identity."""
        application = Path(application_root)
        staging = Path(staging_root)
        return {
            "application_root": application.is_dir(),
            "python": (application / ".venv" / "bin" / "python").is_file(),
            "update_directory": staging.is_dir(),
            "update_writable": os.access(staging, os.W_OK),
        }

    def machine_command(self, command):
        """Execute one allowlisted LinuxCNC command through its command API."""
        if self.hal_helper_socket and command in {"abort", "power_on", "power_off", "estop", "estop_reset", "override_limits", "mode_manual", "mode_auto", "coolant_mist_on", "coolant_flood_on", "coolant_off", "program_start", "program_pause_resume", "tool_change_ack", "probe_source_setter", "probe_source_probe", "home_all", "unhome_all"}:
            return self._linuxcnc_machine_command_with_helper(command)
        linuxcnc = importlib.import_module("linuxcnc")
        controller = linuxcnc.command()
        state = self.machine_state()
        if command == "abort":
            controller.abort()
        elif command == "power_on":
            if state.get("machine_on"):
                return
            controller.state(linuxcnc.STATE_ON)
        elif command == "power_off":
            controller.state(linuxcnc.STATE_OFF)
        elif command == "estop":
            controller.state(linuxcnc.STATE_OFF)
            controller.state(linuxcnc.STATE_ESTOP)
        elif command == "estop_reset":
            controller.state(linuxcnc.STATE_ESTOP_RESET)
        elif command == "override_limits":
            controller.override_limits()
        elif command in {"coolant_mist_on", "coolant_flood_on", "coolant_off"}:
            if not state.get("running") or not state.get("machine_on"):
                raise MachineStateError("Coolant control requires powered LinuxCNC.")
            if command == "coolant_mist_on":
                controller.mist(1)
            elif command == "coolant_flood_on":
                controller.flood(1)
            else:
                controller.mist(0)
                controller.flood(0)
        elif command == "program_start":
            if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
                raise MachineStateError("Cycle start requires powered, idle LinuxCNC.")
            status = linuxcnc.stat()
            status.poll()
            if not getattr(status, "file", None):
                raise MachineStateError("Cycle start requires a loaded program.")
            controller.mode(linuxcnc.MODE_AUTO)
            controller.auto(linuxcnc.AUTO_RUN, 0)
        elif command == "program_pause_resume":
            if not state.get("running") or not state.get("machine_on"):
                raise MachineStateError("Pause/resume requires powered LinuxCNC.")
            status = linuxcnc.stat()
            status.poll()
            if getattr(status, "interp_state", None) == linuxcnc.INTERP_PAUSED:
                controller.auto(linuxcnc.AUTO_RESUME)
            elif getattr(status, "interp_state", None) == linuxcnc.INTERP_IDLE:
                raise MachineStateError("Pause/resume requires an active or paused program.")
            else:
                controller.auto(linuxcnc.AUTO_PAUSE)
        elif command in {"mode_manual", "mode_auto"}:
            if not state.get("running") or not state.get("idle"):
                raise MachineStateError("Mode selection requires idle LinuxCNC.")
            controller.mode(linuxcnc.MODE_MANUAL if command == "mode_manual" else linuxcnc.MODE_AUTO)
        elif command in {"home_all", "unhome_all"}:
            if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
                raise MachineStateError("Home control requires powered, idle LinuxCNC.")
            controller.mode(linuxcnc.MODE_MANUAL)
            controller.teleop_enable(0)
            if command == "home_all":
                controller.home(-1)
            else:
                controller.unhome(-1)
        else:
            raise ValueError("Unsupported machine command.")

    def spindle_command(self, action, rpm):
        """Apply one bounded spindle direction command through LinuxCNC's API."""
        if self.hal_helper_socket:
            request = json.dumps(
                {"operation": "linuxcnc_spindle_command", "action": action, "rpm": rpm},
                separators=(",", ":"),
            ).encode("utf-8")
            try:
                with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                    connection.settimeout(5)
                    connection.connect(self.hal_helper_socket)
                    connection.sendall(request)
                    response = b""
                    while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        response += chunk
                payload = json.loads(response.decode("utf-8"))
                if payload.get("error"):
                    raise MachineStateError(payload["error"])
                if payload.get("accepted") is not True:
                    raise MachineStateError("LinuxCNC did not accept spindle command.")
                return
            except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on"):
            raise MachineStateError("Spindle control requires powered LinuxCNC.")
        controller = linuxcnc.command()
        if action == "forward":
            controller.spindle(linuxcnc.SPINDLE_FORWARD, rpm)
        elif action == "reverse":
            controller.spindle(linuxcnc.SPINDLE_REVERSE, rpm)
        else:
            controller.spindle(linuxcnc.SPINDLE_OFF)

    def change_tool(self, tool):
        """Run the fixed, validated tool-change and length-offset sequence."""
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
            raise MachineStateError("Tool change requires powered, idle LinuxCNC.")
        controller = linuxcnc.command()
        controller.mode(linuxcnc.MODE_MDI)
        controller.mdi(f"T{tool} M6 G43")

    def home_axis(self, axis):
        """Home one validated axis through LinuxCNC's command API."""
        if self.hal_helper_socket:
            request = json.dumps(
                {"operation": "linuxcnc_home_axis", "axis": axis},
                separators=(",", ":"),
            ).encode("utf-8")
            try:
                with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                    connection.settimeout(120)
                    connection.connect(self.hal_helper_socket)
                    connection.sendall(request)
                    response = b""
                    while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        response += chunk
                payload = json.loads(response.decode("utf-8"))
                if payload.get("error"):
                    raise MachineStateError(payload["error"])
                if payload.get("accepted") is not True:
                    raise MachineStateError("LinuxCNC did not accept axis homing.")
                return
            except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on"):
            raise MachineStateError("Axis homing requires powered LinuxCNC.")
        joint_index = {"X": 0, "Y": 1, "Z": 3, "A": 4, "B": 5, "C": 6}[axis]
        linuxcnc.command().home(joint_index)

    def jog(self, action, axis, velocity):
        """Start or stop one bounded jog operation through the LinuxCNC helper."""
        if not self.hal_helper_socket:
            raise RuntimeError("Secure jogging requires the LinuxCNC helper.")
        request = json.dumps(
            {"operation": "linuxcnc_jog", "action": action, "axis": axis, "velocity": velocity},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            if payload.get("error"):
                raise MachineStateError(payload["error"])
            if payload.get("accepted") is not True:
                raise MachineStateError("LinuxCNC did not accept jog operation.")
        except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error

    def touch_off_axis(self, axis, value):
        """Set one work-coordinate axis value through the LinuxCNC helper."""
        if not self.hal_helper_socket:
            raise RuntimeError("Axis touch-off requires the LinuxCNC helper.")
        request = json.dumps(
            {"operation": "linuxcnc_touch_off_axis", "axis": axis, "value": value},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                connection.settimeout(10)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            if payload.get("error"):
                raise MachineStateError(payload["error"])
            if payload.get("accepted") is not True:
                raise MachineStateError("LinuxCNC did not accept axis touch-off.")
        except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error

    def run_subroutine(self, ocode):
        """Run one allowlisted O-code subroutine through the LinuxCNC helper."""
        if not self.hal_helper_socket:
            raise RuntimeError("Subroutine execution requires the LinuxCNC helper.")
        request = json.dumps(
            {"operation": "linuxcnc_run_subroutine", "ocode": ocode},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                connection.settimeout(20)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            if payload.get("error"):
                raise MachineStateError(payload["error"])
            if payload.get("accepted") is not True:
                raise MachineStateError("LinuxCNC did not accept subroutine.")
        except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error

    def probe_z(self, target, velocity):
        """Run one downward probe and retract a fixed distance while powered and idle."""
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
            raise MachineStateError("Z probing requires powered and idle LinuxCNC.")
        if not self.hal_helper_socket:
            # Legacy/local compatibility path. Secure Debian deployments use
            # the helper branch below so LinuxCNC runs as its owning user.
            linuxcnc = importlib.import_module("linuxcnc")
            controller = linuxcnc.command()
            controller.mode(linuxcnc.MODE_MDI)
            controller.mdi(f"G91 G38.2 Z{target:.4f} F{velocity:.4f}")
            result = controller.wait_complete(45)
            if result == -1 or result == getattr(linuxcnc, "RCS_ERROR", -1):
                raise MachineStateError("Raw Z probe motion did not complete.")
            status = linuxcnc.stat()
            status.poll()
            if not getattr(status, "probe_tripped", False):
                raise MachineStateError("Raw Z probe failed because the probe did not trip.")
            controller.mdi("G91 G0 Z2.0000")
            controller.wait_complete(45)
            return
        request = json.dumps(
            {"operation": "linuxcnc_probe_z", "target": target, "velocity": velocity},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                connection.settimeout(60)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            if payload.get("error"):
                raise MachineStateError(payload["error"])
            if payload.get("accepted") is not True:
                raise MachineStateError("LinuxCNC did not accept raw Z probe.")
        except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error

    def touch_off_z(self, target, velocity, plate_thickness, retract):
        """Probe a locally selected touch plate, set Z work offset, then retract."""
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
            raise MachineStateError("Touch-off requires powered, idle LinuxCNC.")
        if self.hal_helper_socket:
            request = json.dumps(
                {
                    "operation": "linuxcnc_touch_off_z",
                    "target": target,
                    "velocity": velocity,
                    "plate_thickness": plate_thickness,
                    "retract": retract,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            try:
                with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                    connection.settimeout(60)
                    connection.connect(self.hal_helper_socket)
                    connection.sendall(request)
                    response = b""
                    while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        response += chunk
                payload = json.loads(response.decode("utf-8"))
                if payload.get("error"):
                    raise MachineStateError(payload["error"])
                if payload.get("accepted") is not True:
                    raise MachineStateError("LinuxCNC did not accept touch-off Z.")
                return {"plate_thickness": plate_thickness, "retract": retract}
            except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error

        linuxcnc = importlib.import_module("linuxcnc")
        controller = linuxcnc.command()
        errors = linuxcnc.error_channel() if hasattr(linuxcnc, "error_channel") else None
        rcs_error = getattr(linuxcnc, "RCS_ERROR", -1)

        def probe_input_active():
            try:
                result = subprocess.run(
                    ["halcmd", "getp", "motion.probe-input"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except FileNotFoundError:
                result = None
            if result is not None and result.returncode == 0 and result.stdout.strip().upper() == "TRUE":
                return True
            # Test doubles and older bindings may expose probe_tripped on stat;
            # use it only as a fallback when HAL probing is unavailable.
            try:
                status = linuxcnc.stat()
                status.poll()
                return bool(getattr(status, "probe_tripped", False))
            except (SystemError, OSError, AttributeError):
                return False

        def wait_for_step(step):
            result = controller.wait_complete(45)
            if result == -1 or result == rcs_error:
                raise MachineStateError(f"Touch-off {step} did not complete.")
            if errors is not None:
                try:
                    error = errors.poll()
                except (SystemError, OSError, AttributeError):
                    error = None
                if error:
                    raise MachineStateError(f"Touch-off {step} failed: {error[1]}")

        controller.mode(linuxcnc.MODE_MDI)
        controller.mdi(f"G91 G38.2 Z{target:.4f} F{velocity:.4f}")
        wait_for_step("probe")
        if not probe_input_active():
            raise MachineStateError("Touch-off failed because the probe did not trip.")
        controller.mdi(f"G10 L20 P0 Z{plate_thickness:.4f}")
        wait_for_step("offset")
        controller.mdi(f"G91 G0 Z{retract:.4f}")
        try:
            wait_for_step("retract")
        except MachineStateError as error:
            raise MachineStateError(f"{error} The Z work offset was already applied.") from error
        return {"plate_thickness": plate_thickness, "retract": retract}

    def move_tool_setter(self, x, y, z):
        """Move to bounded tool-setter coordinates through fixed MDI commands."""
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
            raise MachineStateError("Tool-setter move requires powered and idle LinuxCNC.")
        if self.hal_helper_socket:
            request = json.dumps(
                {"operation": "linuxcnc_move_tool_setter", "x": x, "y": y, "z": z},
                separators=(",", ":"),
            ).encode("utf-8")
            try:
                with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                    connection.settimeout(60)
                    connection.connect(self.hal_helper_socket)
                    connection.sendall(request)
                    response = b""
                    while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        response += chunk
                payload = json.loads(response.decode("utf-8"))
                if payload.get("error"):
                    raise MachineStateError(payload["error"])
                if payload.get("accepted") is not True:
                    raise MachineStateError("LinuxCNC did not accept tool-setter move.")
                return
            except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(f"LinuxCNC helper unavailable: {error}") from error

        linuxcnc = importlib.import_module("linuxcnc")
        controller = linuxcnc.command()
        controller.mode(linuxcnc.MODE_MDI)
        controller.mdi(f"G53 G0 Z{z:.4f}")
        controller.mdi(f"G53 G0 X{x:.4f} Y{y:.4f}")


    def calibrate_tool_setter(self, x, y, velocity, search_distance):
        """Run the complete bounded tool-setter calibration through the helper."""
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
            raise MachineStateError("Tool-setter calibration requires powered and idle LinuxCNC.")
        if not self.hal_helper_socket:
            raise RuntimeError("Tool-setter calibration requires the LinuxCNC helper.")
        request = json.dumps(
            {
                "operation": "linuxcnc_tool_setter_calibration",
                "x": x,
                "y": y,
                "velocity": velocity,
                "search_distance": search_distance,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:
                connection.settimeout(300)
                connection.connect(self.hal_helper_socket)
                connection.sendall(request)
                response = b""
                while not response.endswith(b"\n") and len(response) <= 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    response += chunk
            payload = json.loads(response.decode("utf-8"))
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            if not isinstance(payload.get("setter_height"), (int, float)):
                raise RuntimeError("Tool-setter helper returned invalid calibration data.")
            return {"setter_height": float(payload["setter_height"])}
        except (AttributeError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Tool-setter helper unavailable: {error}") from error

    def graceful_shutdown(self):
        """Request LinuxCNC's own graceful cleanup through its Python API."""
        subprocess.run(
            [
                self.python_executable,
                "-c",
                "import linuxcnc; linuxcnc.command().exit()",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )

    def start(self, ini_path):
        """Start LinuxCNC with a validated INI path."""
        subprocess.Popen(
            [self.linuxcnc_executable, ini_path],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def recovery_shutdown(self):
        """Keep disruptive recovery disabled until explicitly implemented."""
        raise RuntimeError("Recovery shutdown is disabled until an explicit policy is configured.")

    def upload_file(self, destination, content, expected_sha256, backup_root=None):
        """Verify, back up, and atomically replace one uploaded file."""
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256.lower():
            raise ValueError("Upload checksum does not match content.")

        destination = os.path.abspath(destination)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if backup_root and os.path.exists(destination):
            os.makedirs(backup_root, exist_ok=True)
            shutil.copyfile(destination, os.path.join(backup_root, os.path.basename(destination)))
        file_descriptor, temporary_path = tempfile.mkstemp(dir=os.path.dirname(destination))
        try:
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.chmod(temporary_path, 0o664)
            os.replace(temporary_path, destination)
        except Exception:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise

    def activate_pending(self, pending_path, active_path, backup_root):
        """Back up an active INI and atomically promote a pending INI."""
        if self.is_running():
            raise RuntimeError("LinuxCNC must be stopped before configuration activation.")
        if not os.path.isfile(pending_path):
            raise FileNotFoundError("Pending configuration does not exist.")
        os.makedirs(backup_root, exist_ok=True)
        if os.path.isfile(active_path):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            shutil.copy2(active_path, os.path.join(backup_root, f"active-{timestamp}.ini"))
        os.replace(pending_path, active_path)

    def activate_and_restart(self, pending_path, active_path, backup_root, timeout=15.0):
        """Promote an INI, verify LinuxCNC startup, and restore on failure."""
        if self.is_running():
            raise RuntimeError("LinuxCNC must be stopped before configuration activation.")
        if not os.path.isfile(pending_path):
            raise FileNotFoundError("Pending configuration does not exist.")
        os.makedirs(backup_root, exist_ok=True)
        backup_path = os.path.join(backup_root, "last-known-good.ini")
        pending_backup_path = os.path.join(backup_root, "pending-during-restart.ini")
        had_active = os.path.isfile(active_path)
        if had_active:
            shutil.copy2(active_path, backup_path)
        shutil.copy2(pending_path, pending_backup_path)
        os.replace(pending_path, active_path)
        try:
            self.start(active_path)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.is_running():
                    os.unlink(pending_backup_path)
                    return
                time.sleep(0.1)
            if self.is_running():
                os.unlink(pending_backup_path)
                return
        except Exception:
            pass
        if os.path.isfile(backup_path):
            os.replace(backup_path, active_path)
        elif not had_active and os.path.exists(active_path):
            os.unlink(active_path)
        os.replace(pending_backup_path, pending_path)
        raise RuntimeError("LinuxCNC failed to start; previous configuration restored.")