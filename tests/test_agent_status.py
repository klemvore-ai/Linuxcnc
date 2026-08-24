import unittest

from agent.status import AGENT_VERSION, build_status


class RuntimeDouble:
    """Minimal runtime double for status metadata tests."""

    def machine_state(self):
        return {"running": False, "idle": False}


class FailingRuntime:
    """Runtime double that cannot query LinuxCNC state."""

    def machine_state(self):
        raise RuntimeError("state unavailable")


class AgentStatusTests(unittest.TestCase):
    def test_status_contains_version_capabilities_role_mode_and_state(self):
        status = build_status(RuntimeDouble(), "roles")

        self.assertEqual(status["agent_version"], AGENT_VERSION)
        self.assertIn("lifecycle", status["capabilities"])
        self.assertIn("spindle_control", status["capabilities"])
        self.assertIn("tool_change", status["capabilities"])
        self.assertIn("raw_z_probe", status["capabilities"])
        self.assertIn("touch_off_z", status["capabilities"])
        self.assertEqual(status["role_mode"], "roles")
        self.assertEqual(status["machine"]["running"], False)

    def test_status_returns_structured_unavailable_state_on_runtime_failure(self):
        status = build_status(FailingRuntime())

        self.assertFalse(status["machine"]["state_available"])


if __name__ == "__main__":
    unittest.main()