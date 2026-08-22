"""Minimal HTTPS/mTLS HTTP server for the LinuxCNC machine agent."""

import json
import ssl
import base64
import binascii
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent.policy import AgentPolicyError, validate_action, validate_upload, validate_activation, validate_restart_activation, validate_mesa_identification, validate_tool_table_path, validate_gcode_path, validate_override, validate_pid, validate_firmware, validate_machine_command, validate_home_axis, validate_probe_operation, validate_tool_setter_move
from agent.bundles import BundleError, approve_bundle, bundle_status
from agent.backups import BackupError, backup_manifest
from agent.archive_transfer import ArchiveError, create_archive, restore_archive
from agent.authorization import AuthorizationError, authorize_client, authorize_role
from agent.state_policy import MachineStateError
from agent.status import build_status


MAX_REQUEST_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_APPLICATION_BYTES = 32 * 1024 * 1024


class MachineAgentServer:
    """Expose bounded, policy-checked lifecycle and upload endpoints."""

    def __init__(self, address, tls_context, runtime, allowed_config_roots, gcode_root=None, allowed_client_fingerprints=None, client_roles=None, application_staging_root="/var/lib/cncfusion/updates", peer_certificate_provider=None):
        """Bind a TLS server to the supplied address and runtime adapter."""
        self.runtime = runtime
        self.allowed_config_roots = allowed_config_roots
        self.gcode_root = gcode_root or "/home/operator/linuxcnc/nc_files"
        self.allowed_client_fingerprints = allowed_client_fingerprints or []
        self.client_roles = client_roles or {}
        self.application_staging_root = application_staging_root
        self.peer_certificate_provider = peer_certificate_provider
        handler = self._handler_type()
        self.server = ThreadingHTTPServer(address, handler)
        self.server.socket = tls_context.wrap_socket(self.server.socket, server_side=True)

    def _handler_type(self):
        """Create a request handler closed over this server's runtime policy."""
        runtime = self.runtime
        allowed_config_roots = self.allowed_config_roots
        gcode_root = self.gcode_root
        allowed_client_fingerprints = self.allowed_client_fingerprints
        client_roles = self.client_roles
        role_mode = "roles" if client_roles else ("fingerprint" if allowed_client_fingerprints else "compatibility")
        application_staging_root = self.application_staging_root
        peer_certificate_provider = self.peer_certificate_provider

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                """Serve the authenticated lifecycle status endpoint."""
                if not self._authorize(self._required_role()):
                    return
                if self.path == "/v1/hal/pins":
                    self._send_json(200, runtime.show_hal_pins())
                    return
                if self.path == "/v1/machine/state":
                    self._send_json(200, runtime.machine_state())
                    return
                if self.path == "/v1/telemetry":
                    self._send_json(200, runtime.telemetry())
                    return
                if self.path == "/v1/agent/status":
                    self._send_json(200, build_status(runtime, role_mode))
                    return
                if self.path == "/v1/logs/launch":
                    self._send_json(200, runtime.read_launch_log())
                    return
                if self.path.startswith("/v1/tool-table/"):
                    self._read_tool_table()
                    return
                if self.path == "/v1/gcode":
                    self._send_json(200, {"files": runtime.list_gcode(gcode_root)})
                    return
                if self.path.startswith("/v1/gcode/"):
                    self._read_gcode()
                    return
                if self.path.startswith("/v1/configuration/bundles/"):
                    self._bundle_status()
                    return
                if self.path == "/v1/backups/config":
                    self._backup_manifest("config")
                    return
                if self.path == "/v1/backups/gcode":
                    self._backup_manifest("gcode")
                    return
                if self.path == "/v1/backups/config/archive":
                    self._download_archive("config")
                    return
                if self.path == "/v1/backups/gcode/archive":
                    self._download_archive("gcode")
                    return
                if self.path != "/v1/lifecycle":
                    self._send_error(404, "Not found.")
                    return
                self._send_json(200, {"running": runtime.is_running()})

            def do_POST(self):
                """Dispatch only the lifecycle and file-upload endpoints."""
                if not self._authorize(self._required_role()):
                    return
                if self.path == "/v1/files":
                    self._upload_file()
                    return
                if self.path == "/v1/mesa/identify":
                    self._identify_mesa()
                    return
                if self.path == "/v1/program/open":
                    self._open_program()
                    return
                if self.path == "/v1/tuning/override":
                    self._set_override()
                    return
                if self.path == "/v1/tuning/pid":
                    self._set_pid()
                    return
                if self.path == "/v1/mesa/firmware":
                    self._flash_firmware()
                    return
                if self.path == "/v1/application/update":
                    self._stage_application_update()
                    return
                if self.path == "/v1/machine/command":
                    self._machine_command()
                    return
                if self.path == "/v1/machine/home-axis":
                    self._home_axis()
                    return
                if self.path == "/v1/machine/probe-z":
                    self._probe_z()
                    return
                if self.path == "/v1/machine/tool-setter":
                    self._move_tool_setter()
                    return
                if self.path == "/v1/tool-table/reload":
                    self._reload_tool_table()
                    return
                if self.path == "/v1/backups/config/restore":
                    self._restore_archive("config")
                    return
                if self.path == "/v1/backups/gcode/restore":
                    self._restore_archive("gcode")
                    return
                if self.path == "/v1/configuration/actions":
                    self._activate_configuration()
                    return
                if self.path.startswith("/v1/configuration/bundles/") and self.path.endswith("/approve"):
                    self._approve_bundle()
                    return
                if self.path != "/v1/lifecycle/actions":
                    self._send_error(404, "Not found.")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    action, ini_path = validate_action(payload, allowed_config_roots)
                    if action == "graceful_shutdown":
                        runtime.graceful_shutdown()
                    elif action == "recovery_shutdown":
                        runtime.recovery_shutdown()
                    else:
                        runtime.start(ini_path)
                    self._send_json(202, {"accepted": True, "action": action})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(500, "Lifecycle operation failed.")

            def _upload_file(self):
                """Validate and pass one bounded upload to the runtime adapter."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_UPLOAD_BYTES:
                        raise AgentPolicyError("Upload is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    kind, destination = validate_upload(payload, allowed_config_roots[0], gcode_root)
                    content = base64.b64decode(payload["content"], validate=True)
                    if len(content) > MAX_UPLOAD_BYTES:
                        raise AgentPolicyError("Upload is too large.")
                    backup_root = str(destination.parent / ".cncfusion-backups") if kind == "config" else None
                    runtime.upload_file(destination, content, payload["sha256"], backup_root)
                    self._send_json(202, {"accepted": True, "kind": kind})
                except (AgentPolicyError, ValueError, base64.binascii.Error) as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(500, "File upload failed.")

            def _activate_configuration(self):
                """Promote a validated pending INI through the runtime adapter."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if payload.get("action") == "activate_and_restart":
                        pending_path, active_path = validate_restart_activation(payload, allowed_config_roots[0])
                    else:
                        pending_path, active_path = validate_activation(payload, allowed_config_roots[0])
                    backup_root = str(pending_path.parent / ".cncfusion-backups")
                    if payload["action"] == "activate_and_restart":
                        runtime.activate_and_restart(pending_path, active_path, backup_root)
                    else:
                        runtime.activate_pending(pending_path, active_path, backup_root)
                    self._send_json(202, {"accepted": True, "action": payload["action"]})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(409, "Configuration activation was rejected.")

            def _identify_mesa(self):
                """Run and return bounded read-only Mesa identification output."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    board, address = validate_mesa_identification(payload)
                    self._send_json(200, runtime.identify_mesa(board, address))
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(500, "Mesa identification failed.")

            def _read_tool_table(self):
                """Read a validated relative tool-table path."""
                try:
                    relative_path = unquote(self.path[len("/v1/tool-table/"):])
                    path = validate_tool_table_path(relative_path, allowed_config_roots[0])
                    self._send_json(200, runtime.read_tool_table(path))
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(500, "Tool-table read failed.")

            def _read_gcode(self):
                """Read one validated G-code file from the approved root."""
                try:
                    relative_path = unquote(self.path[len("/v1/gcode/"):])
                    path = validate_gcode_path(relative_path, gcode_root)
                    self._send_json(200, runtime.read_gcode(path))
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(500, "G-code read failed.")

            def _open_program(self):
                """Open a validated G-code file through the local LinuxCNC API."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if set(payload) != {"relative_path"}:
                        raise AgentPolicyError("Program-open fields are invalid.")
                    path = validate_gcode_path(payload["relative_path"], gcode_root)
                    runtime.open_program(path)
                    self._send_json(202, {"accepted": True, "relative_path": payload["relative_path"]})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except MachineStateError as error:
                    self._send_error(409, str(error))
                except Exception:
                    self._send_error(409, "Program open was rejected.")

            def _set_override(self):
                """Apply one validated runtime-safe override."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    kind, value = validate_override(payload)
                    runtime.set_override(kind, value)
                    self._send_json(202, {"accepted": True, "kind": kind, "value": value})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except MachineStateError as error:
                    self._send_error(409, str(error))
                except Exception:
                    self._send_error(409, "Override was rejected.")

            def _set_pid(self):
                """Apply one validated spindle PID parameter through the runtime."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    field, value = validate_pid(payload)
                    runtime.set_pid(field, value)
                    self._send_json(202, {"accepted": True, "field": field, "value": value})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(409, "PID tuning was rejected.")

            def _flash_firmware(self):
                """Verify and flash one Mesa firmware image."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > 16 * 1024 * 1024:
                        raise AgentPolicyError("Firmware request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    board, address, filename = validate_firmware(payload)
                    content = base64.b64decode(payload["content"], validate=True)
                    result = runtime.flash_firmware(board, address, filename, content, payload["sha256"])
                    self._send_json(202, {"accepted": True, "filename": filename, **result})
                except (AgentPolicyError, ValueError, binascii.Error) as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(409, "Firmware flashing was rejected.")

            def _stage_application_update(self):
                """Validate and stage an application archive without executing code."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_APPLICATION_BYTES:
                        raise ArchiveError("Application update is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if set(payload) != {"sha256", "content"}:
                        raise ArchiveError("Application update fields are invalid.")
                    content = base64.b64decode(payload["content"], validate=True)
                    result = runtime.stage_application_update(content, payload["sha256"], application_staging_root)
                    self._send_json(202, result)
                except (ArchiveError, ValueError, binascii.Error) as error:
                    self._send_error(409, str(error))

            def _machine_command(self):
                """Execute one validated operator machine command."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    command = validate_machine_command(payload)
                    runtime.machine_command(command)
                    self._send_json(202, {"accepted": True, "command": command})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except Exception:
                    self._send_error(409, "Machine command was rejected.")

            def _home_axis(self):
                """Home one validated axis through the local runtime."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    axis = validate_home_axis(payload)
                    runtime.home_axis(axis)
                    self._send_json(202, {"accepted": True, "axis": axis})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except MachineStateError as error:
                    self._send_error(409, str(error))
                except Exception:
                    self._send_error(409, "Axis homing was rejected.")

            def _probe_z(self):
                """Run one validated Z probe operation through the local runtime."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    target, velocity = validate_probe_operation(payload)
                    runtime.probe_z(target, velocity)
                    self._send_json(202, {"accepted": True, "operation": "probe_z"})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except MachineStateError as error:
                    self._send_error(409, str(error))
                except Exception:
                    self._send_error(409, "Z probing was rejected.")

            def _move_tool_setter(self):
                """Move to validated tool-setter coordinates through the runtime."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    x, y, z = validate_tool_setter_move(payload)
                    runtime.move_tool_setter(x, y, z)
                    self._send_json(202, {"accepted": True, "operation": "tool_setter_move"})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except MachineStateError as error:
                    self._send_error(409, str(error))
                except Exception:
                    self._send_error(409, "Tool-setter move was rejected.")

            def _reload_tool_table(self):
                """Reload the tool table through the local LinuxCNC runtime."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length > MAX_REQUEST_BYTES:
                        raise AgentPolicyError("Request is too large.")
                    payload = json.loads(self.rfile.read(max(length, 0)).decode("utf-8")) if length else {}
                    if payload != {}:
                        raise AgentPolicyError("Tool-table reload does not accept fields.")
                    runtime.reload_tool_table()
                    self._send_json(202, {"accepted": True, "operation": "tool_table_reload"})
                except AgentPolicyError as error:
                    self._send_error(400, str(error))
                except MachineStateError as error:
                    self._send_error(409, str(error))
                except Exception:
                    self._send_error(409, "Tool-table reload was rejected.")

            def _bundle_name(self):
                """Extract and decode one bundle name from an API path."""
                prefix = "/v1/configuration/bundles/"
                suffix = "/approve"
                value = self.path[len(prefix):]
                if value.endswith(suffix):
                    value = value[:-len(suffix)]
                return unquote(value)

            def _bundle_status(self):
                """Return staged bundle metadata without changing machine state."""
                try:
                    status = bundle_status(allowed_config_roots[0], self._bundle_name())
                    self._send_json(200, status)
                except BundleError as error:
                    self._send_error(400, str(error))

            def _approve_bundle(self):
                """Persist explicit approval for a complete staged bundle."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > MAX_REQUEST_BYTES:
                        raise BundleError("Request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if payload != {"action": "approve"}:
                        raise BundleError("An explicit approve action is required.")
                    status = approve_bundle(allowed_config_roots[0], self._bundle_name())
                    self._send_json(202, status)
                except BundleError as error:
                    self._send_error(409, str(error))

            def _backup_manifest(self, kind):
                """Return checksums for an approved backup area."""
                try:
                    self._send_json(200, backup_manifest(kind, allowed_config_roots[0], gcode_root))
                except BackupError as error:
                    self._send_error(400, str(error))

            def _download_archive(self, kind):
                """Create and return one bounded archive with its checksum."""
                try:
                    content, checksum = create_archive(kind, allowed_config_roots[0], gcode_root)
                    self._send_json(200, {"kind": kind, "sha256": checksum, "content": base64.b64encode(content).decode("ascii")})
                except ArchiveError as error:
                    self._send_error(409, str(error))

            def _restore_archive(self, kind):
                """Validate and transactionally restore one archive."""
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > 24 * 1024 * 1024:
                        raise ArchiveError("Restore request is missing or too large.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if set(payload) != {"sha256", "content"}:
                        raise ArchiveError("Restore fields are invalid.")
                    content = base64.b64decode(payload["content"], validate=True)
                    restore_archive(kind, content, payload["sha256"], allowed_config_roots[0], gcode_root, runtime)
                    self._send_json(202, {"accepted": True, "kind": kind})
                except (ArchiveError, ValueError, binascii.Error) as error:
                    self._send_error(409, str(error))

            def log_message(self, format_string, *args):
                """Suppress request logging so payloads cannot leak into logs."""
                return

            def _send_json(self, status, payload):
                """Write one compact JSON response with an explicit length."""
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_error(self, status, message):
                """Return a structured error without exposing internal details."""
                self._send_json(status, {"error": message})

            def _required_role(self):
                """Return the minimum role required by the requested endpoint."""
                if self.path in {"/v1/lifecycle", "/v1/machine/state", "/v1/telemetry", "/v1/hal/pins", "/v1/logs/launch", "/v1/gcode"} or self.path.startswith("/v1/tool-table/") or self.path.startswith("/v1/gcode/"):
                    return "monitor"
                if self.path in {"/v1/program/open", "/v1/tuning/override", "/v1/machine/command", "/v1/machine/home-axis", "/v1/tool-table/reload"}:
                    return "operator"
                if self.path == "/v1/tuning/pid":
                    return "technician"
                if self.path in {"/v1/machine/probe-z", "/v1/machine/tool-setter"}:
                    return "technician"
                return "administrator"

            def _authorize(self, required_role):
                """Authorize the peer certificate when an allowlist is configured."""
                if not allowed_client_fingerprints and not client_roles:
                    return True
                try:
                    certificate = (
                        peer_certificate_provider()
                        if peer_certificate_provider is not None
                        else self.connection.getpeercert(binary_form=True)
                    )
                    self.client_fingerprint = authorize_client(certificate, allowed_client_fingerprints or client_roles)
                    authorize_role(self.client_fingerprint, required_role, client_roles)
                    return True
                except (AuthorizationError, AttributeError):
                    self._send_error(403, "Client certificate is not authorized.")
                    return False

        return Handler

    def serve_forever(self):
        """Run the threaded HTTPS server until shutdown is requested."""
        self.server.serve_forever()

    def shutdown(self):
        """Stop serving and release the listening socket."""
        self.server.shutdown()
        self.server.server_close()


def create_server_tls_context(ca_file, certificate_file, private_key_file):
    """Create a TLS 1.2+ server context requiring client certificates."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=ca_file)
    context.load_cert_chain(certificate_file, private_key_file)
    return context