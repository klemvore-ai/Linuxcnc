import unittest

from agent.status import AGENT_VERSION, build_status


class RuntimeDouble:
    """Minimal runtime double for status metadata tests."""

    def machine_state(self):
        return {"running": False, "idle": False}


class AgentStatusTests(unittest.TestCase):
    def test_status_contains_version_capabilities_role_mode_and_state(self):
        status = build_status(RuntimeDouble(), "roles")

        self.assertEqual(status["agent_version"], AGENT_VERSION)
        self.assertIn("lifecycle", status["capabilities"])
        self.assertEqual(status["role_mode"], "roles")
        self.assertEqual(status["machine"]["running"], False)


if __name__ == "__main__":
    unittest.main()