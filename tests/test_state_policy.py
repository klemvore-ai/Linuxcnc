import unittest

from agent.state_policy import MachineStateError, require_running_idle, require_stopped


class StatePolicyTests(unittest.TestCase):
    def test_running_idle_allows_tuning_operations(self):
        require_running_idle({"running": True, "idle": True}, "PID tuning")

    def test_running_idle_rejects_stopped_or_busy_machine(self):
        with self.assertRaises(MachineStateError):
            require_running_idle({"running": False, "idle": False}, "program loading")
        with self.assertRaises(MachineStateError):
            require_running_idle({"running": True, "idle": False}, "program loading")

    def test_stopped_rejects_running_machine(self):
        require_stopped({"running": False}, "firmware flashing")
        with self.assertRaises(MachineStateError):
            require_stopped({"running": True}, "firmware flashing")


if __name__ == "__main__":
    unittest.main()