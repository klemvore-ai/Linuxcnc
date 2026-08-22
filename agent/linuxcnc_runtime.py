"""Operating-system adapter used by the LinuxCNC machine agent."""

import subprocess
import time
import hashlib
import importlib
from agent.state_policy import MachineStateError
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class LinuxCNCRuntime:
    """Execute approved local LinuxCNC and atomic file operations."""

    def __init__(self, linuxcnc_executable="linuxcnc", python_executable="python3"):
        """Configure executable names without shell interpretation."""
        self.linuxcnc_executable = linuxcnc_executable
        self.python_executable = python_executable

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
        if not self.is_running():
            return {"running": False, "idle": False}
        linuxcnc = importlib.import_module("linuxcnc")
        status = linuxcnc.stat()
        status.poll()
        return {
            "running": True,
            "idle": getattr(status, "interp_state", None) == linuxcnc.INTERP_IDLE,
            "machine_on": getattr(status, "task_state", None) == linuxcnc.STATE_ON,
        }

    def telemetry(self):
        """Return a bounded operator telemetry snapshot from LinuxCNC status."""
        linuxcnc = importlib.import_module("linuxcnc")
        status = linuxcnc.stat()
        status.poll()
        positions = getattr(status, "actual_position", (0.0, 0.0, 0.0))
        return {
            "X": positions[0] if len(positions) > 0 else 0.0,
            "Y": positions[1] if len(positions) > 1 else 0.0,
            "Z": positions[2] if len(positions) > 2 else 0.0,
            "FEED": getattr(status, "current_vel", 0.0) * 60.0,
            "RPM": abs(getattr(status, "spindle_speed", 0.0)),
            "HOMED_COUNT": sum(1 for value in getattr(status, "homed", []) if value),
            "MACHINE_ON": getattr(status, "task_state", None) == linuxcnc.STATE_ON,
            "ESTOP_ACTIVATED": getattr(status, "task_state", None) == linuxcnc.STATE_ESTOP,
            "JOINTS": int(getattr(status, "joints", getattr(status, "axes", 3))),
            "LINE": getattr(status, "motion_line", 0),
        }

    def show_hal_pins(self):
        """Return the fixed HAL pin listing without accepting shell input."""
        result = subprocess.run(
            ["halcmd", "show", "pin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return {"output": result.stdout[-1024 * 1024:], "error": result.stderr[-65536:]}

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

    def reload_tool_table(self):
        """Reload tool offsets through LinuxCNC while the interpreter is idle."""
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state.get("running") or not state.get("idle"):
            raise MachineStateError("Tool-table reload requires running and idle LinuxCNC.")
        linuxcnc.command().load_tool_table()

    def open_program(self, path):
        """Open one validated G-code file through LinuxCNC's command API."""
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
        staged_path = os.path.join(staging_root, "cncfusion-update.tar.gz")
        os.replace(archive_path, staged_path)
        return {"staged": True, "path": staged_path, "sha256": expected_sha256.lower()}

    def machine_command(self, command):
        """Execute one allowlisted LinuxCNC command through its command API."""
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
        else:
            raise ValueError("Unsupported machine command.")

    def home_axis(self, axis):
        """Home one validated axis through LinuxCNC's command API."""
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on"):
            raise MachineStateError("Axis homing requires powered LinuxCNC.")
        joint_index = {"X": 0, "Y": 1, "Z": 2, "A": 3, "B": 4, "C": 5}[axis]
        linuxcnc.command().home(joint_index)

    def probe_z(self, target, velocity):
        """Run one bounded Z probe cycle while LinuxCNC is powered and idle."""
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
            raise MachineStateError("Z probing requires powered and idle LinuxCNC.")
        linuxcnc.command().mode(linuxcnc.MODE_MDI)
        linuxcnc.command().mdi(f"G38.2 Z{target:.4f} F{velocity:.4f}")

    def move_tool_setter(self, x, y, z):
        """Move to bounded tool-setter coordinates through fixed MDI commands."""
        linuxcnc = importlib.import_module("linuxcnc")
        state = self.machine_state()
        if not state.get("running") or not state.get("machine_on") or not state.get("idle"):
            raise MachineStateError("Tool-setter move requires powered and idle LinuxCNC.")
        controller = linuxcnc.command()
        controller.mode(linuxcnc.MODE_MDI)
        controller.mdi(f"G53 G0 Z{z:.4f}")
        controller.mdi(f"G53 G0 X{x:.4f} Y{y:.4f}")

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
            shutil.copy2(destination, os.path.join(backup_root, os.path.basename(destination)))
        file_descriptor, temporary_path = tempfile.mkstemp(dir=os.path.dirname(destination))
        try:
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
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