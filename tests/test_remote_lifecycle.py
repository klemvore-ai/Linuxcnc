import unittest
import base64
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from core.lifecycle import LifecycleState, LinuxCNCLifecycle
from core.remote_lifecycle import RemoteLinuxCNCBackend
from core.machine_agent import MachineAgentClient
from agent.linuxcnc_runtime import LinuxCNCRuntime
from agent import hal_helper


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
    def test_agent_client_reports_structured_http_error(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        client.base_url = "https://machine.example"
        client.context = None
        client.timeout = 1
        error = HTTPError(
            "https://machine.example/v1/application/update",
            500,
            "Internal Server Error",
            None,
            BytesIO(b'{"error":"Application update staging failed."}'),
        )

        with patch("core.machine_agent.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Application update staging failed"):
                client.stage_application_update(b"archive")

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

    def test_hal_component_inspection_uses_fixed_agent_endpoint(self):
        client = MachineAgentClient.__new__(MachineAgentClient)
        requests = []
        client.get = lambda path: requests.append(path) or {"output": "components", "error": ""}

        result = client.show_hal_components()

        self.assertEqual(result["output"], "components")
        self.assertEqual(requests, ["/v1/hal/components"])

    def test_hal_pin_inspection_uses_local_fixed_operation_helper(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b'{"output":"pins","error":""}\n'
        runtime = LinuxCNCRuntime(hal_helper_socket="/run/cncfusion/hal-helper.sock")

        with (
            patch("agent.linuxcnc_runtime.socket.AF_UNIX", 1, create=True),
            patch("agent.linuxcnc_runtime.socket.socket", return_value=connection),
        ):
            result = runtime.show_hal_pins()

        self.assertEqual(result, {"output": "pins", "error": ""})
        connection.connect.assert_called_once_with("/run/cncfusion/hal-helper.sock")
        connection.sendall.assert_called_once_with(b"show_hal_pins\n")

    def test_hal_component_inspection_uses_local_fixed_operation_helper(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b'{"output":"components","error":""}\n'
        runtime = LinuxCNCRuntime(hal_helper_socket="/run/cncfusion/hal-helper.sock")

        with (
            patch("agent.linuxcnc_runtime.socket.AF_UNIX", 1, create=True),
            patch("agent.linuxcnc_runtime.socket.socket", return_value=connection),
        ):
            result = runtime.show_hal_components()

        self.assertEqual(result, {"output": "components", "error": ""})
        connection.sendall.assert_called_once_with(b"show_hal_components\n")

    def test_hal_helper_returns_output_for_each_fixed_operation(self):
        completed = SimpleNamespace(stdout="inspection", stderr="")

        with patch("agent.hal_helper.subprocess.run", return_value=completed) as run:
            pins = hal_helper.show_hal_pins()
            components = hal_helper.show_hal_components()

        self.assertEqual(pins, {"output": "inspection", "error": ""})
        self.assertEqual(components, {"output": "inspection", "error": ""})
        self.assertEqual(
            run.call_args_list[1].args[0],
            ("halcmd", "show", "comp"),
        )

    def test_raw_z_probe_issues_downward_incremental_probe_and_retract(self):
        runtime = LinuxCNCRuntime()
        runtime.machine_state = lambda: {"running": True, "machine_on": True, "idle": True}
        controller = MagicMock()
        status = SimpleNamespace(probe_tripped=True, poll=lambda: None)
        errors = SimpleNamespace(poll=lambda: None)
        linuxcnc = SimpleNamespace(MODE_MDI=2, command=lambda: controller, stat=lambda: status, error_channel=lambda: errors)

        with patch("agent.linuxcnc_runtime.importlib.import_module", return_value=linuxcnc):
            runtime.probe_z(-10.0, 50.0)

        controller.mdi.assert_any_call("G91 G38.2 Z-10.0000 F50.0000")
        controller.mdi.assert_any_call("G91 G0 Z2.0000")

    def test_raw_z_probe_rejects_missing_contact_without_retract(self):
        runtime = LinuxCNCRuntime()
        runtime.machine_state = lambda: {"running": True, "machine_on": True, "idle": True}
        controller = MagicMock()
        status = SimpleNamespace(probe_tripped=False, poll=lambda: None)
        linuxcnc = SimpleNamespace(MODE_MDI=2, command=lambda: controller, stat=lambda: status)

        with patch("agent.linuxcnc_runtime.importlib.import_module", return_value=linuxcnc):
            with self.assertRaisesRegex(Exception, "probe did not trip"):
                runtime.probe_z(-10.0, 50.0)

        controller.mdi.assert_called_once_with("G91 G38.2 Z-10.0000 F50.0000")

    def test_touch_off_requires_probe_contact_then_sets_z_offset_and_retracts(self):
        runtime = LinuxCNCRuntime()
        runtime.machine_state = lambda: {"running": True, "machine_on": True, "idle": True}
        controller = MagicMock()
        status = SimpleNamespace(probe_tripped=True, poll=lambda: None)
        errors = SimpleNamespace(poll=lambda: None)
        linuxcnc = SimpleNamespace(MODE_MDI=2, command=lambda: controller, stat=lambda: status, error_channel=lambda: errors)

        with patch("agent.linuxcnc_runtime.importlib.import_module", return_value=linuxcnc):
            result = runtime.touch_off_z(-10.0, 50.0, 6.35, 2.0)

        self.assertEqual(result, {"plate_thickness": 6.35, "retract": 2.0})
        controller.mdi.assert_any_call("G91 G38.2 Z-10.0000 F50.0000")
        controller.mdi.assert_any_call("G10 L20 P0 Z6.3500")
        controller.mdi.assert_any_call("G91 G0 Z2.0000")

    def test_touch_off_stops_when_linuxcnc_reports_probe_error(self):
        runtime = LinuxCNCRuntime()
        runtime.machine_state = lambda: {"running": True, "machine_on": True, "idle": True}
        controller = MagicMock()
        status = SimpleNamespace(probe_tripped=True, poll=lambda: None)
        errors = SimpleNamespace(poll=lambda: (1, "probe motion error"))
        linuxcnc = SimpleNamespace(MODE_MDI=2, command=lambda: controller, stat=lambda: status, error_channel=lambda: errors)

        with patch("agent.linuxcnc_runtime.importlib.import_module", return_value=linuxcnc):
            with self.assertRaisesRegex(Exception, "probe motion error"):
                runtime.touch_off_z(-10.0, 50.0, 6.35, 2.0)

        controller.mdi.assert_called_once_with("G91 G38.2 Z-10.0000 F50.0000")

    def test_touch_off_reports_offset_applied_when_retract_fails(self):
        runtime = LinuxCNCRuntime()
        runtime.machine_state = lambda: {"running": True, "machine_on": True, "idle": True}
        controller = MagicMock()
        status = SimpleNamespace(probe_tripped=True, poll=lambda: None)
        errors = SimpleNamespace(poll=MagicMock(side_effect=[None, None, (1, "retract motion error")]))
        linuxcnc = SimpleNamespace(MODE_MDI=2, command=lambda: controller, stat=lambda: status, error_channel=lambda: errors)

        with patch("agent.linuxcnc_runtime.importlib.import_module", return_value=linuxcnc):
            with self.assertRaisesRegex(Exception, "Z work offset was already applied"):
                runtime.touch_off_z(-10.0, 50.0, 6.35, 2.0)

        controller.mdi.assert_any_call("G10 L20 P0 Z6.3500")
        controller.mdi.assert_any_call("G91 G0 Z2.0000")

    def test_secure_telemetry_includes_program_mode_and_override_state(self):
        runtime = LinuxCNCRuntime()
        status = SimpleNamespace(
            actual_position=(1.0, 2.0, 3.0),
            current_vel=2.0,
            spindle_speed=-1200.0,
            feedrate=0.75,
            spindle_scale=1.1,
            homed=(True, True, True),
            task_state=1,
            joints=3,
            motion_line=42,
            interp_state=10,
            task_mode=20,
            file="/home/darrell/linuxcnc/nc_files/test.ngc",
            dtg=(0.1, 0.2, 0.3),
            poll=lambda: None,
        )
        linuxcnc = SimpleNamespace(
            STATE_ON=1,
            STATE_ESTOP=2,
            INTERP_IDLE=10,
            INTERP_PAUSED=11,
            MODE_MANUAL=20,
            MODE_AUTO=21,
            MODE_MDI=22,
            stat=lambda: status,
        )

        with patch("agent.linuxcnc_runtime.importlib.import_module", return_value=linuxcnc):
            telemetry = runtime.telemetry()

        self.assertEqual(telemetry["STATUS"], "IDLE")
        self.assertEqual(telemetry["MODE"], "MANUAL")
        self.assertTrue(telemetry["PROGRAM_LOADED"])
        self.assertEqual(telemetry["FEED_OVR"], 0.75)
        self.assertEqual(telemetry["DTG_Z"], 0.3)

    def test_secure_telemetry_uses_linuxcnc_owner_helper_status(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b'{"machine_state":{"running":true,"idle":true},"telemetry":{"X":1.0},"error":""}\n'
        runtime = LinuxCNCRuntime(hal_helper_socket="/run/cncfusion/hal-helper.sock")

        with (
            patch("agent.linuxcnc_runtime.socket.AF_UNIX", 1, create=True),
            patch("agent.linuxcnc_runtime.socket.socket", return_value=connection),
        ):
            telemetry = runtime.telemetry()
            machine_state = runtime.machine_state()

        self.assertEqual(telemetry, {"X": 1.0})
        self.assertEqual(machine_state, {"running": True, "idle": True})
        connection.sendall.assert_called_with(b'{"operation":"linuxcnc_status"}')

    def test_machine_command_uses_linuxcnc_owner_helper(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b'{"accepted":true,"command":"estop_reset"}\n'
        runtime = LinuxCNCRuntime(hal_helper_socket="/run/cncfusion/hal-helper.sock")

        with (
            patch("agent.linuxcnc_runtime.socket.AF_UNIX", 1, create=True),
            patch("agent.linuxcnc_runtime.socket.socket", return_value=connection),
        ):
            runtime.machine_command("estop_reset")

        connection.sendall.assert_called_once_with(b'{"operation":"linuxcnc_machine_command","command":"estop_reset"}')

    def test_helper_machine_command_rejects_unknown_command(self):
        with self.assertRaises(Exception):
            hal_helper.linuxcnc_machine_command("mdi G0 X1")

    def test_linuxcnc_status_runs_a_short_lived_status_probe(self):
        completed = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout='{"machine_state":{"running":true},"telemetry":{"X":1.0},"error":""}',
        )
        with patch("agent.hal_helper.subprocess.run", return_value=completed) as run:
            status = hal_helper.linuxcnc_status()

        self.assertEqual(status["machine_state"]["running"], True)
        self.assertEqual(status["telemetry"]["X"], 1.0)
        self.assertEqual(run.call_args.args[0][:2], ["/opt/CNCFusion/.venv/bin/python", "-c"])

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