import unittest
import base64
import hashlib

from core.lifecycle import LifecycleState, LinuxCNCLifecycle
from core.remote_lifecycle import RemoteLinuxCNCBackend
from core.machine_agent import MachineAgentClient
from agent.linuxcnc_runtime import LinuxCNCRuntime


class FakeAgentClient:
    def __init__(self, running=False, graceful_shutdown_works=True):
        self.running = running
        self.graceful_shutdown_works = graceful_shutdown_works
        self.requests = []

    def get(self, path):
        self.requests.append(("GET", path, None))
        return {"running": self.running}

    def post(self, path, payload):
        self.requests.append(("POST", path, payload))
        if payload["action"] == "graceful_shutdown" and self.graceful_shutdown_works:
            self.running = False
        elif payload["action"] == "start":
            self.running = True
        elif payload["action"] == "recovery_shutdown":
            self.running = False
        return {"accepted": True}


class RemoteLifecycleTests(unittest.TestCase):
    def test_graceful_shutdown_uses_structured_agent_action(self):
        client = FakeAgentClient(running=True)
        lifecycle = LinuxCNCLifecycle(RemoteLinuxCNCBackend(client))

        result = lifecycle.graceful_shutdown()

        self.assertTrue(result.success)
        self.assertEqual(result.state, LifecycleState.STOPPED)
        self.assertIn(
            ("POST", "/v1/lifecycle/actions", {"action": "graceful_shutdown"}),
            client.requests,
        )

    def test_start_sends_ini_path_as_data(self):
        client = FakeAgentClient()
        lifecycle = LinuxCNCLifecycle(RemoteLinuxCNCBackend(client))

        result = lifecycle.start("/home/operator/linuxcnc/configs/machine.ini")

        self.assertTrue(result.success)
        self.assertIn(
            (
                "POST",
                "/v1/lifecycle/actions",
                {"action": "start", "ini_path": "/home/operator/linuxcnc/configs/machine.ini"},
            ),
            client.requests,
        )

    def test_recovery_is_a_distinct_explicit_action(self):
        client = FakeAgentClient(running=True, graceful_shutdown_works=False)
        lifecycle = LinuxCNCLifecycle(RemoteLinuxCNCBackend(client))

        lifecycle.recovery_shutdown()

        self.assertIn(
            ("POST", "/v1/lifecycle/actions", {"action": "recovery_shutdown"}),
            client.requests,
        )

    def test_upload_content_sends_checksum_and_base64(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload))
        content = b"G0 X1\n"

        client.upload_content("gcode", "active_program.ngc", content)

        path, payload = requests[0]
        self.assertEqual(path, "/v1/files")
        self.assertEqual(payload["kind"], "gcode")
        self.assertEqual(payload["relative_path"], "active_program.ngc")
        self.assertEqual(payload["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(base64.b64decode(payload["content"]), content)

    def test_hal_pin_inspection_uses_fixed_agent_endpoint(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.get = lambda path: requests.append(path) or {"output": "pins", "error": ""}

        result = client.show_hal_pins()

        self.assertEqual(result["output"], "pins")
        self.assertEqual(requests, ["/v1/hal/pins"])

    def test_mesa_identification_uses_structured_request(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"output": "HMID", "error": ""}

        result = client.identify_mesa("7i76e", "10.10.10.10")

        self.assertEqual(result["output"], "HMID")
        self.assertEqual(
            requests,
            [("/v1/mesa/identify", {"board": "7i76e", "address": "10.10.10.10"})],
        )

    def test_tool_table_read_uses_encoded_relative_endpoint(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.get = lambda path: requests.append(path) or {"content": "T1 P1"}

        result = client.read_tool_table("Test Mill/tool.tbl")

        self.assertEqual(result["content"], "T1 P1")
        self.assertEqual(requests, ["/v1/tool-table/Test%20Mill/tool.tbl"])

    def test_tool_table_reload_uses_empty_structured_request(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}

        client.reload_tool_table()

        self.assertEqual(requests, [("/v1/tool-table/reload", {})])

    def test_gcode_list_and_read_use_typed_endpoints(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.get = lambda path: (requests.append(path) or ({"files": ["macro.ngc"]} if path == "/v1/gcode" else {"content": "G0 X1"}))

        self.assertEqual(client.list_gcode()["files"], ["macro.ngc"])
        self.assertEqual(client.read_gcode("macro.ngc")["content"], "G0 X1")
        self.assertEqual(requests, ["/v1/gcode", "/v1/gcode/macro.ngc"])

    def test_program_open_uses_structured_relative_path(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}

        result = client.open_program("active_program.ngc")

        self.assertTrue(result["accepted"])
        self.assertEqual(requests, [("/v1/program/open", {"relative_path": "active_program.ngc"})])

    def test_machine_state_uses_typed_status_endpoint(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.get = lambda path: requests.append(path) or {"running": True, "idle": True}

        result = client.machine_state()

        self.assertTrue(result["running"])
        self.assertTrue(result["idle"])
        self.assertEqual(requests, ["/v1/machine/state"])

    def test_telemetry_uses_typed_snapshot_endpoint(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.get = lambda path: requests.append(path) or {"X": 1.0, "STATUS": "IDLE"}

        result = client.telemetry()

        self.assertEqual(result["STATUS"], "IDLE")
        self.assertEqual(requests, ["/v1/telemetry"])

    def test_agent_status_uses_typed_discovery_endpoint(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.get = lambda path: requests.append(path) or {"agent_version": "0.1.0", "capabilities": []}

        result = client.agent_status()

        self.assertEqual(result["agent_version"], "0.1.0")
        self.assertEqual(requests, ["/v1/agent/status"])

    def test_agent_status_updates_client_capabilities(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        client.capabilities = set()
        client.get = lambda path: {"capabilities": ["backup_transfer"]}

        client.agent_status()

        self.assertTrue(client.supports("backup_transfer"))
        self.assertFalse(client.supports("mesa_firmware"))

    def test_override_uses_structured_tuning_request(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}

        result = client.set_override("feed", 1.1)

        self.assertTrue(result["accepted"])
        self.assertEqual(requests, [("/v1/tuning/override", {"kind": "feed", "value": 1.1})])

    def test_pid_uses_one_named_structured_field(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}

        client.set_pid("ff1", 0.25)

        self.assertEqual(
            requests,
            [("/v1/tuning/pid", {"p": False, "i": False, "d": False, "ff0": False, "ff1": True, "value": 0.25})],
        )

    def test_firmware_upload_sends_checksum_and_encoded_content(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}
        content = b"firmware"

        client.flash_firmware("7i76e", "10.10.10.10", "firmware.bit", content)

        self.assertEqual(requests[0][0], "/v1/mesa/firmware")
        self.assertEqual(requests[0][1]["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(base64.b64decode(requests[0][1]["content"]), content)

    def test_machine_command_uses_named_agent_operation(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}

        client.machine_command("abort")

        self.assertEqual(requests, [("/v1/machine/command", {"command": "abort"})])

    def test_home_axis_uses_named_agent_operation(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}

        client.home_axis("Z")

        self.assertEqual(requests, [("/v1/machine/home-axis", {"axis": "Z"})])

    def test_probe_z_uses_bounded_named_agent_operation(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}

        client.probe_z(-10.0, 50.0)

        self.assertEqual(
            requests,
            [("/v1/machine/probe-z", {"operation": "probe_z", "target": -10.0, "velocity": 50.0})],
        )

    def test_tool_setter_move_uses_named_coordinate_operation(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}

        client.move_tool_setter(10.0, 20.0, -5.0)

        self.assertEqual(
            requests,
            [("/v1/machine/tool-setter", {"x": 10.0, "y": 20.0, "z": -5.0})],
        )

    def test_backup_archive_download_decodes_content_and_checksum(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        content = b"archive-bytes"
        client.get = lambda path: {
            "content": base64.b64encode(content).decode("ascii"),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

        downloaded, checksum = client.download_backup_archive("config")

        self.assertEqual(downloaded, content)
        self.assertEqual(checksum, hashlib.sha256(content).hexdigest())

    def test_backup_restore_sends_checksum_and_encoded_content(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.post = lambda path, payload: requests.append((path, payload)) or {"accepted": True}
        content = b"archive-bytes"

        client.restore_backup_archive("config", content)

        self.assertEqual(requests[0][0], "/v1/backups/config/restore")
        self.assertEqual(requests[0][1]["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(base64.b64decode(requests[0][1]["content"]), content)

    def test_activation_backs_up_active_file_before_replacing_it(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "settings_pending.ini"
            active = root / "settings.ini"
            backups = root / "backups"
            pending.write_text("pending", encoding="utf-8")
            active.write_text("active", encoding="utf-8")
            runtime = LinuxCNCRuntime()
            runtime.is_running = lambda: False

            runtime.activate_pending(pending, active, backups)

            self.assertEqual(active.read_text(encoding="utf-8"), "pending")
            self.assertFalse(pending.exists())
            self.assertEqual(len(list(backups.glob("active-*.ini"))), 1)

    def test_restart_activation_restores_previous_file_after_failed_start(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "settings_pending.ini"
            active = root / "settings.ini"
            backups = root / "backups"
            pending.write_text("pending", encoding="utf-8")
            active.write_text("active", encoding="utf-8")
            runtime = LinuxCNCRuntime()
            runtime.is_running = lambda: False
            runtime.start = lambda ini_path: None

            with self.assertRaises(RuntimeError):
                runtime.activate_and_restart(pending, active, backups, timeout=0)

            self.assertEqual(active.read_text(encoding="utf-8"), "active")
            self.assertEqual(pending.read_text(encoding="utf-8"), "pending")


if __name__ == "__main__":
    unittest.main()