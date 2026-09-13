import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tabs.dashboard import MachineDashboard


class SettingsDouble:
    def __init__(self, values=None):
        self.values = values or {}

    def get_setting(self, section, key, default=None):
        return self.values.get((section, key), default)


class DashboardMDITests(unittest.TestCase):
    def test_execute_mdi_uses_local_machine_when_no_ssh_credentials_exist(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.is_estopped = False
        dashboard.mock_coords = {"MACHINE_ON": True}
        dashboard._send_b64_py_cmd = lambda *args, **kwargs: called.append((args, kwargs))
        called = []
        dashboard._send_b64_py_cmd = lambda *args, **kwargs: called.append((args, kwargs))

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.execute_mdi("M3 S1000")

        self.assertEqual(len(called), 1)
        self.assertIn("M3 S1000", called[0][0][0])

    def test_secure_cycle_start_opens_dashboard_program_before_starting(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard._program_sync_in_progress = False
        calls = []

        class AgentDouble:
            def open_program(self, path):
                calls.append(("open_program", path))

            def machine_command(self, command, timeout=None):
                calls.append((command, timeout))

        with patch("tabs.dashboard.client_from_settings", return_value=AgentDouble()):
            dashboard.start_cycle()

        self.assertEqual(
            calls,
            [
                ("open_program", "active_program.ngc"),
                ("program_start", 15.0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
