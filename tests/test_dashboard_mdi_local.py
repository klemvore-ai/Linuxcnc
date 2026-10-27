import os
import queue
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tabs.dashboard import MachineDashboard, linuxcnc_status_snapshot
from tabs.hal_show_tab import format_hal_pin_listing


class SettingsDouble:
    def __init__(self, values=None):
        self.values = values or {}
        self.local_only = False

    def get_setting(self, section, key, default=None):
        return self.values.get((section, key), default)

    def reload(self):
        pass


class DashboardMDITests(unittest.TestCase):
    def test_hal_monitor_formats_native_hal_pin_records(self):
        output = format_hal_pin_listing([
            {"NAME": "joint.2.neg-lim-sw-in", "TYPE": "bit", "DIRECTION": "IN", "VALUE": True},
            {"NAME": "j2-neg-limit", "TYPE": "bit", "DIRECTION": "IO", "VALUE": False},
        ])

        self.assertIn("BIT IN TRUE joint.2.neg-lim-sw-in", output)
        self.assertIn("BIT I/O FALSE j2-neg-limit", output)

    def test_local_status_snapshot_uses_linuxcnc_29_fields(self):
        class LinuxCNC:
            INTERP_IDLE = 1
            INTERP_PAUSED = 2
            MODE_MANUAL = 1
            MODE_AUTO = 2
            MODE_MDI = 3
            STATE_ON = 4
            STATE_ESTOP = 5

        class Status:
            def poll(self):
                pass

            joints = 3
            homed = [True, True, True]
            actual_position = [110.0, 220.0, 330.0]
            g5x_offset = [10.0, 20.0, 30.0]
            g92_offset = [1.0, 2.0, 3.0]
            tool_offset = [0.0, 0.0, 0.0]
            current_vel = 2.0
            feedrate = 0.5
            spindle_scale = 1.0
            task_state = 4
            interp_state = 1
            task_mode = 1
            motion_line = 12
            axis_mask = 7
            linear_units = 1.0
            dtg = [1.0, 2.0, 3.0]

        class ErrorChannel:
            def poll(self):
                return None

        snapshot = linuxcnc_status_snapshot(LinuxCNC, Status(), ErrorChannel())

        self.assertEqual((snapshot["X"], snapshot["Y"], snapshot["Z"]), (99.0, 198.0, 297.0))
        self.assertEqual(snapshot["HOMED_COUNT"], 3)
        self.assertEqual(snapshot["STATUS"], "IDLE")
        self.assertEqual(snapshot["MODE"], "MANUAL")
        self.assertEqual(snapshot["UNITS"], "inch")
        self.assertTrue(snapshot["MACHINE_ON"])
        self.assertEqual(snapshot["LINE"], 12)

    def test_local_controller_runs_queued_commands_without_ssh_credentials(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.cmd_queue = queue.Queue()
        dashboard.error_signal = Mock()
        dashboard.cmd_queue.put(("halcmd show pin", 5))
        dashboard.cmd_queue.put(None)

        with patch("tabs.dashboard.is_local_controller", return_value=True), patch(
            "tabs.dashboard.subprocess.run"
        ) as run_command:
            dashboard._cmd_worker()

        run_command.assert_called_once_with(
            "halcmd show pin",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_local_telemetry_follows_controller_process_without_ssh_settings(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.ssh_connected = False
        dashboard.start_position_fetcher = Mock()
        dashboard.stop_position_fetcher = Mock()

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.is_local_controller", return_value=True
        ), patch("tabs.dashboard.is_local_linuxcnc_running", side_effect=[True, False]):
            dashboard.send_ping()
            dashboard.send_ping()

        dashboard.start_position_fetcher.assert_called_once_with()
        dashboard.stop_position_fetcher.assert_called_once_with()
        self.assertFalse(dashboard.ssh_connected)

    def test_local_position_fetcher_does_not_require_ssh_username(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.pos_fetcher = None

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.is_local_controller", return_value=True
        ), patch("tabs.dashboard.PositionFetcherThread") as fetcher_type:
            dashboard.start_position_fetcher()

        fetcher_type.assert_called_once_with("127.0.0.1", "", "")
        fetcher_type.return_value.start.assert_called_once_with()

    def test_local_hal_writes_queue_without_ssh_credentials(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.settings.local_only = True
        dashboard.local_api = Mock()
        dashboard.error_signal = Mock()

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.is_local_controller", return_value=True
        ):
            dashboard.send_halcmd_setp("hm2.test.pin", "TRUE")

        dashboard.local_api.set_hal_value.assert_called_once_with("hm2.test.pin", "TRUE")

    def test_local_power_on_is_blocked_by_active_joint_limit(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.settings.local_only = True
        dashboard.local_api = Mock()
        dashboard.local_api.linuxcnc.STATE_ESTOP = 1
        dashboard.local_api.linuxcnc.STATE_ON = 2
        dashboard.local_api.linuxcnc.STATE_OFF = 0
        dashboard.local_api.status.return_value = Mock(task_state=0, joints=3)
        dashboard.local_api.get_hal_value.side_effect = lambda name: name == "joint.2.neg-lim-sw-in"
        dashboard.local_api.command.return_value = Mock()
        dashboard.mock_coords = {"MACHINE_ON": False}
        dashboard.is_estopped = False

        with patch("tabs.dashboard.QMessageBox.warning") as warning:
            dashboard.toggle_power()

        warning.assert_called_once()
        dashboard.local_api.command.return_value.state.assert_not_called()

    def test_local_dashboard_command_executes_through_api_not_shell(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.settings.local_only = True
        dashboard.local_api = Mock()
        dashboard.cmd_queue = queue.Queue()
        dashboard.error_signal = Mock()
        dashboard.cmd_queue.put(("api", "linuxcnc.command().abort()", 5))
        dashboard.cmd_queue.put(None)

        with patch("tabs.dashboard.subprocess.run") as run_command:
            dashboard._cmd_worker()

        dashboard.local_api.execute_application_command.assert_called_once_with(
            "linuxcnc.command().abort()"
        )
        run_command.assert_not_called()

    def test_local_estop_reset_uses_live_linuxcnc_state(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.settings.local_only = True
        dashboard.local_api = Mock()
        dashboard.local_api.linuxcnc.STATE_ESTOP = 1
        dashboard.local_api.linuxcnc.STATE_ESTOP_RESET = 2
        dashboard.local_api.status.return_value = Mock(task_state=1)

        dashboard.toggle_estop()

        dashboard.local_api.command.return_value.state.assert_called_once_with(2)

    def test_local_limit_override_does_not_power_machine_on(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.settings.local_only = True
        dashboard.local_api = Mock()
        answer = __import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes

        with patch("tabs.dashboard.QMessageBox.question", return_value=answer), patch(
            "tabs.dashboard.QMessageBox.information"
        ) as info:
            dashboard.override_limits()

        dashboard.local_api.command.return_value.override_limits.assert_called_once_with()
        dashboard.local_api.command.return_value.state.assert_not_called()
        info.assert_not_called()

    def test_local_urgent_command_runs_without_ssh_credentials(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()

        with patch("tabs.dashboard.is_local_controller", return_value=True), patch(
            "tabs.dashboard.subprocess.run"
        ) as run_command:
            dashboard._execute_urgent_cmd("linuxcnc-command", timeout=3)

        run_command.assert_called_once_with(
            "linuxcnc-command",
            shell=True,
            capture_output=True,
            text=True,
            timeout=3,
        )

    def test_remote_urgent_command_is_skipped_without_credentials(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble({("SSH", "ip_address"): "machine"})

        with patch("tabs.dashboard.is_local_controller", return_value=False), patch(
            "tabs.dashboard.subprocess.run"
        ) as run_command:
            dashboard._execute_urgent_cmd("linuxcnc-command")

        run_command.assert_not_called()

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

    def test_local_mdi_uses_linuxcnc_command_api(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble()
        dashboard.settings.local_only = True
        dashboard.local_api = Mock()
        dashboard.local_api.linuxcnc.STATE_ESTOP = 1
        dashboard.local_api.linuxcnc.STATE_ON = 2
        dashboard.local_api.linuxcnc.MODE_MDI = 3
        status = Mock(task_state=2, task_mode=0)
        dashboard.local_api.status.return_value = status
        dashboard.is_estopped = False
        dashboard.mock_coords = {"MACHINE_ON": True}
        dashboard.error_signal = Mock()

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.execute_mdi("M3 S1000")

        status.poll.assert_called_once_with()
        dashboard.local_api.command.return_value.mode.assert_called_once_with(3)
        dashboard.local_api.command.return_value.mdi.assert_called_once_with("M3 S1000")

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
