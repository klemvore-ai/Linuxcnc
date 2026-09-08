"""HTTPS/mTLS client for structured CNCFusion machine-agent operations."""

import json
import ssl
import base64
import hashlib
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class MachineAgentError(RuntimeError):
    """Raised when an agent request cannot be completed safely."""

    pass


class MachineAgentClient:
    """Send authenticated, bounded JSON requests to a machine agent."""

    def __init__(self, base_url, ca_file, client_cert=None, client_key=None, timeout=5.0):
        """Create a client with CA validation and optional client identity."""
        if not base_url.startswith("https://"):
            raise ValueError("The machine agent requires an HTTPS URL.")
        if not ca_file:
            raise ValueError("A CA certificate is required.")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=ca_file)
        self.capabilities = set()
        if client_cert:
            if not client_key:
                raise ValueError("A client private key is required with a client certificate.")
            self.context.load_cert_chain(client_cert, client_key)

    def get(self, path):
        """Issue a JSON GET request to an absolute agent path."""
        return self._request("GET", path)

    def show_hal_pins(self):
        """Return the machine's bounded HAL pin listing."""
        return self.get("/v1/hal/pins")

    def show_hal_components(self):
        """Return the machine's bounded HAL component listing."""
        return self.get("/v1/hal/components")

    def read_hal_values(self, pins):
        """Return one bounded snapshot for validated HAL pin names."""
        return self.post("/v1/hal/values", {"pins": pins})

    def identify_mesa(self, board, address):
        """Request fixed read-only Mesa hardware identification."""
        return self.post("/v1/mesa/identify", {"board": board, "address": address})

    def read_launch_log(self):
        """Return the bounded, fixed LinuxCNC launch log."""
        return self.get("/v1/logs/launch")

    def machine_state(self):
        """Return the agent's current LinuxCNC state snapshot."""
        return self.get("/v1/machine/state")

    def telemetry(self):
        """Return one typed operator telemetry snapshot."""
        return self.get("/v1/telemetry")

    def agent_status(self):
        """Return agent version, capabilities, role mode, and machine state."""
        status = self.get("/v1/agent/status")
        self.capabilities = set(status.get("capabilities", []))
        return status

    def supports(self, capability):
        """Return whether the most recently discovered agent supports a capability."""
        return capability in self.capabilities

    def read_tool_table(self, relative_path):
        """Read a validated relative tool-table path from the machine."""
        return self.get(f"/v1/tool-table/{quote(relative_path, safe='/')}")

    def read_gcode(self, relative_path):
        """Read a validated relative G-code file from the machine."""
        return self.get(f"/v1/gcode/{quote(relative_path, safe='/')}")

    def list_gcode(self):
        """Return the machine's approved G-code file list."""
        return self.get("/v1/gcode")

    def list_macros(self):
        """Return approved O-code macro files available on the machine."""
        return self.get("/v1/macros")

    def upload_macro(self, filename, content):
        """Upload one allowlisted O-code macro with a generated checksum."""
        if isinstance(content, str):
            content = content.encode("utf-8")
        if len(content) > 64 * 1024:
            raise MachineAgentError("Macro is too large.")
        payload = {
            "filename": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
            "content": content.decode("utf-8"),
        }
        return self.post("/v1/macros", payload)

    def reload_tool_table(self):
        """Request LinuxCNC to reload the active tool table while idle."""
        return self.post("/v1/tool-table/reload", {})

    def open_program(self, relative_path):
        """Request LinuxCNC to open a validated relative G-code path."""
        return self.post("/v1/program/open", {"relative_path": relative_path})

    def set_override(self, kind, value):
        """Apply a bounded feed or spindle override through the machine agent."""
        return self.post("/v1/tuning/override", {"kind": kind, "value": value})

    def machine_command(self, command, timeout=None):
        """Execute one allowlisted operator machine command."""
        if timeout is None:
            return self.post("/v1/machine/command", {"command": command})
        return self._request("POST", "/v1/machine/command", {"command": command}, timeout=timeout)

    def spindle_command(self, action, rpm=0.0):
        """Apply one allowlisted spindle direction command at a bounded RPM."""
        return self.post("/v1/spindle/command", {"action": action, "rpm": rpm})

    def change_tool(self, tool):
        """Run the fixed tool-change sequence for one validated tool number."""
        return self.post("/v1/tool/change", {"tool": tool})

    def home_axis(self, axis, timeout=None):
        """Request homing for one named axis."""
        if timeout is not None:
            return self._request("POST", "/v1/machine/home-axis", {"axis": axis}, timeout=timeout)
        return self.post("/v1/machine/home-axis", {"axis": axis})

    def jog_start(self, axis, velocity):
        """Start one bounded continuous jog through the machine agent."""
        return self._request("POST", "/v1/machine/jog", {"action": "start", "axis": axis, "velocity": velocity}, timeout=2.0)

    def jog_stop(self, axis):
        """Stop one continuous jog through the machine agent."""
        return self._request("POST", "/v1/machine/jog", {"action": "stop", "axis": axis}, timeout=2.0)

    def probe_z(self, target, velocity):
        """Request a bounded Z probe cycle through the machine agent."""
        return self.post(
            "/v1/machine/probe-z",
            {"operation": "probe_z", "target": target, "velocity": velocity},
        )

    def touch_off_z(self, target, velocity, plate_thickness, retract=2.0):
        """Probe a locally selected plate, set the Z work offset, and retract."""
        return self.post(
            "/v1/machine/touch-off-z",
            {
                "target": target,
                "velocity": velocity,
                "plate_thickness": plate_thickness,
                "retract": retract,
            },
        )

    def touch_off_axis(self, axis, value=0.0):
        """Set one work-coordinate axis to a bounded value."""
        return self.post("/v1/machine/touch-off-axis", {"axis": axis, "value": value})

    def run_subroutine(self, ocode):
        """Run one allowlisted O-code subroutine."""
        return self.post("/v1/machine/subroutine", {"ocode": ocode})

    def move_tool_setter(self, x, y, z):
        """Request a bounded move to the tool-setter location."""
        return self.post("/v1/machine/tool-setter", {"x": x, "y": y, "z": z})

    def calibrate_tool_setter(self, x, y, velocity, search_distance):
        """Run the bounded tool-setter calibration through the machine agent."""
        return self._request(
            "POST",
            "/v1/machine/tool-setter/calibrate",
            {"x": x, "y": y, "velocity": velocity, "search_distance": search_distance},
            timeout=300.0,
        )

    def set_pid(self, field, value):
        """Set one approved spindle PID parameter through the machine agent."""
        fields = {name: name == field for name in ("p", "i", "d", "ff0", "ff1")}
        fields["value"] = value
        return self.post("/v1/tuning/pid", fields)

    def backup_manifest(self, kind):
        """Return checksums and sizes for a machine backup area."""
        if kind not in {"config", "gcode"}:
            raise ValueError("Backup kind is not supported.")
        return self.get(f"/v1/backups/{kind}")

    def download_backup_archive(self, kind):
        """Download a bounded base64 archive and its checksum."""
        if kind not in {"config", "gcode"}:
            raise ValueError("Backup kind is not supported.")
        response = self.get(f"/v1/backups/{kind}/archive")
        try:
            content = base64.b64decode(response["content"], validate=True)
        except (KeyError, ValueError, base64.binascii.Error) as error:
            raise MachineAgentError("Backup archive response is invalid.") from error
        return content, response["sha256"]

    def restore_backup_archive(self, kind, content):
        """Restore a checksum-verified archive through the machine agent."""
        if kind not in {"config", "gcode"}:
            raise ValueError("Backup kind is not supported.")
        payload = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "content": base64.b64encode(content).decode("ascii"),
        }
        return self.post(f"/v1/backups/{kind}/restore", payload)

    def stage_application_update(self, content):
        """Stage an application archive without installing or executing it."""
        if len(content) > 32 * 1024 * 1024:
            raise MachineAgentError("Application update is too large.")
        payload = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "content": base64.b64encode(content).decode("ascii"),
        }
        return self.post("/v1/application/update", payload)

    def application_update_status(self):
        """Return the staged update and last local-apply status from the machine."""
        return self.get("/v1/application/update")

    def deployment_preflight(self):
        """Return read-only prerequisites for secure application updates."""
        return self.get("/v1/agent/preflight")

    def flash_firmware(self, board, address, filename, content):
        """Send one bounded Mesa firmware image through the secure agent."""
        if len(content) > 16 * 1024 * 1024:
            raise MachineAgentError("Firmware image is too large.")
        payload = {
            "board": board,
            "address": address,
            "filename": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
            "content": base64.b64encode(content).decode("ascii"),
        }
        return self.post("/v1/mesa/firmware", payload)

    def post(self, path, payload):
        """Issue a JSON POST request to an absolute agent path."""
        return self._request("POST", path, payload)

    def upload_file(self, kind, relative_path, file_path):
        """Read and upload one bounded file with a generated SHA-256 digest."""
        with open(file_path, "rb") as source_file:
            content = source_file.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise MachineAgentError("Upload is too large.")
        return self.upload_content(kind, relative_path, content)

    def upload_content(self, kind, relative_path, content):
        """Upload bounded bytes using the structured file endpoint."""
        if len(content) > 16 * 1024 * 1024:
            raise MachineAgentError("Upload is too large.")
        payload = {
            "kind": kind,
            "relative_path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "content": base64.b64encode(content).decode("ascii"),
        }
        return self.post("/v1/files", payload)

    def activate_pending(self, pending_path, active_path):
        """Request activation of a staged INI after machine-side checks."""
        return self.post(
            "/v1/configuration/actions",
            {
                "action": "activate_pending",
                "pending_path": pending_path,
                "active_path": active_path,
            },
        )

    def activate_and_restart(self, pending_path, active_path):
        """Request activation, startup verification, and rollback on failure."""
        return self.post(
            "/v1/configuration/actions",
            {
                "action": "activate_and_restart",
                "pending_path": pending_path,
                "active_path": active_path,
            },
        )

    def bundle_status(self, bundle_name):
        """Return staged-file checksums and approval state for one bundle."""
        return self.get(f"/v1/configuration/bundles/{bundle_name}")

    def approve_bundle(self, bundle_name):
        """Explicitly approve a complete staged configuration bundle."""
        return self.post(
            f"/v1/configuration/bundles/{bundle_name}/approve",
            {"action": "approve"},
        )

    def _request(self, method, path, payload=None, timeout=None):
        """Perform one HTTPS request and decode its bounded JSON response."""
        if not path.startswith("/"):
            raise ValueError("Agent paths must be absolute.")

        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, context=self.context, timeout=self.timeout if timeout is None else timeout) as response:
                response_body = response.read(1024 * 1024 + 1)
                if len(response_body) > 1024 * 1024:
                    raise MachineAgentError("Machine agent response is too large.")
                if not response_body:
                    return {}
                return json.loads(response_body.decode("utf-8"))
        except HTTPError as error:
            try:
                response_body = error.read(1024 * 1024 + 1)
                if len(response_body) <= 1024 * 1024:
                    payload = json.loads(response_body.decode("utf-8"))
                    message = payload.get("error") if isinstance(payload, dict) else None
                    if isinstance(message, str) and message:
                        raise MachineAgentError(message) from error
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise MachineAgentError("Machine agent request failed.") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise MachineAgentError("Machine agent request failed.") from error


def client_from_settings(settings):
    """Build an agent client only when all mTLS references are configured."""
    base_url = settings.get_setting("AGENT", "base_url", "")
    ca_file = settings.get_setting("AGENT", "ca_file", "")
    client_cert = settings.get_setting("AGENT", "client_cert", "")
    client_key = settings.get_setting("AGENT", "client_key", "")
    if not base_url or not ca_file or not client_cert or not client_key:
        return None
    return MachineAgentClient(
        base_url,
        ca_file,
        client_cert,
        client_key,
        timeout=float(settings.get_setting("AGENT", "timeout", "5")),
    )