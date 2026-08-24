import json
import base64
import hashlib
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from agent.authorization import certificate_fingerprint
from agent.server import MachineAgentServer
from agent.state_policy import MachineStateError


class FakeTLSContext:
    """TLS wrapper double that preserves a fake peer certificate."""

    def wrap_socket(self, socket, server_side=False):
        return socket


class FakeRuntime:
    """Runtime double recording typed operations without LinuxCNC installed."""

    def __init__(self, root):
        self.root = Path(root)
        self.running = False
        self.calls = []

    def is_running(self):
        return self.running

    def machine_state(self):
        return {"running": self.running, "idle": not self.running, "machine_on": False}

    def telemetry(self):
        return {"X": 0.0, "Y": 0.0, "Z": 0.0}

    def show_hal_pins(self):
        return {"output": "Component Pins:", "error": ""}

    def show_hal_components(self):
        return {"output": "Component Pins:", "error": ""}

    def read_hal_values(self, pins):
        self.calls.append(tuple(pins))
        return {"values": {pin: "1" for pin in pins}, "error": ""}

    def stage_application_update(self, content, expected_sha256, staging_root):
        raise OSError("staging volume unavailable")

    def application_update_status(self, staging_root):
        return {"staged": False, "last_apply": {"status": "applied"}}

    def deployment_preflight(self, staging_root):
        return {"application_root": True, "python": True, "update_directory": True, "update_writable": True}

    def spindle_command(self, action, rpm):
        self.calls.append(("spindle", action, rpm))

    def change_tool(self, tool):
        self.calls.append(("tool", tool))

    def touch_off_z(self, target, velocity, plate_thickness, retract):
        self.calls.append(("touch_off", target, velocity, plate_thickness, retract))
        return {"plate_thickness": plate_thickness, "retract": retract}

    def read_launch_log(self):
        return {"content": ""}

    def list_gcode(self, root):
        return []

    def read_gcode(self, path):
        return {"content": "G0 X1"}

    def set_override(self, kind, value):
        if not self.running:
            raise MachineStateError("Runtime override requires running LinuxCNC.")
        self.calls.append((kind, value))


class AgentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.config_root = Path(self.root.name) / "configs"
        self.gcode_root = Path(self.root.name) / "nc_files"
        self.config_root.mkdir()
        self.gcode_root.mkdir()
        self.runtime = FakeRuntime(self.root.name)
        self.server = MachineAgentServer(
            ("127.0.0.1", 0),
            FakeTLSContext(),
            self.runtime,
            [self.config_root],
            self.gcode_root,
            [certificate_fingerprint(b"client")],
            {certificate_fingerprint(b"client"): ["monitor", "operator", "administrator"]},
            peer_certificate_provider=lambda: b"client",
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection("127.0.0.1", self.server.server.server_port)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.root.cleanup()

    def test_operator_can_read_telemetry_but_state_rejects_tuning_when_stopped(self):
        self.connection.request("GET", "/v1/telemetry")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["X"], 0.0)

        body = json.dumps({"kind": "feed", "value": 1.0})
        self.connection.request("POST", "/v1/tuning/override", body, {"Content-Type": "application/json"})
        response = self.connection.getresponse()
        self.assertEqual(response.status, 409)

    def test_telemetry_returns_structured_error_when_runtime_fails(self):
        self.runtime.telemetry = lambda: (_ for _ in ()).throw(RuntimeError("helper unavailable"))
        self.connection.request("GET", "/v1/telemetry")

        response = self.connection.getresponse()

        self.assertEqual(response.status, 503)
        self.assertIn("Machine telemetry is unavailable", json.loads(response.read())["error"])

    def test_monitor_can_read_hal_components(self):
        self.connection.request("GET", "/v1/hal/components")

        response = self.connection.getresponse()

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["output"], "Component Pins:")

    def test_monitor_can_read_validated_hal_value_snapshot(self):
        body = json.dumps({"pins": ["joint.0.pos-fb"]})
        self.connection.request("POST", "/v1/hal/values", body, {"Content-Type": "application/json"})

        response = self.connection.getresponse()

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["values"], {"joint.0.pos-fb": "1"})
        self.assertEqual(self.runtime.calls, [("joint.0.pos-fb",)])

    def test_application_staging_returns_error_when_runtime_fails(self):
        content = b"not-an-archive"
        body = json.dumps(
            {
                "sha256": hashlib.sha256(content).hexdigest(),
                "content": base64.b64encode(content).decode("ascii"),
            }
        )
        self.connection.request("POST", "/v1/application/update", body, {"Content-Type": "application/json"})

        response = self.connection.getresponse()

        self.assertEqual(response.status, 500)
        self.assertEqual(json.loads(response.read())["error"], "Application update staging failed.")

    def test_operator_can_send_bounded_spindle_command(self):
        body = json.dumps({"action": "forward", "rpm": 12000})
        self.connection.request("POST", "/v1/spindle/command", body, {"Content-Type": "application/json"})

        response = self.connection.getresponse()

        self.assertEqual(response.status, 202)
        self.assertEqual(self.runtime.calls, [("spindle", "forward", 12000.0)])

    def test_operator_can_send_bounded_tool_change(self):
        body = json.dumps({"tool": 42})
        self.connection.request("POST", "/v1/tool/change", body, {"Content-Type": "application/json"})

        response = self.connection.getresponse()

        self.assertEqual(response.status, 202)
        self.assertEqual(self.runtime.calls, [("tool", 42)])

    def test_monitor_can_read_application_update_status(self):
        self.connection.request("GET", "/v1/application/update")

        response = self.connection.getresponse()

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["last_apply"]["status"], "applied")

    def test_monitor_can_read_deployment_preflight(self):
        self.connection.request("GET", "/v1/agent/preflight")

        response = self.connection.getresponse()

        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["update_writable"])

    def test_technician_can_send_bounded_touch_off(self):
        body = json.dumps({"target": -10, "velocity": 50, "plate_thickness": 6.35, "retract": 2})
        self.connection.request("POST", "/v1/machine/touch-off-z", body, {"Content-Type": "application/json"})

        response = self.connection.getresponse()

        self.assertEqual(response.status, 202)
        self.assertEqual(self.runtime.calls, [("touch_off", -10.0, 50.0, 6.35, 2.0)])


if __name__ == "__main__":
    unittest.main()