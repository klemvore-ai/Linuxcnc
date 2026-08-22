import json
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
            {certificate_fingerprint(b"client"): ["monitor", "operator"]},
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


if __name__ == "__main__":
    unittest.main()