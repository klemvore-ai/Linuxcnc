import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QWidget
from tabs.dashboard import MachineDashboard, linuxcnc_status_snapshot


class SettingsDouble:
    def __init__(self, values=None):
        self.values = values or {}
        self.local_only = True

    def get_setting(self, section, key, default=None):
        return self.values.get((section, key), default)

    def save_setting(self, section, key, value):
        self.values[(section, key)] = value

    def reload(self):
        pass


class ParkAndPauseStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _create_dashboard(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        QWidget.__init__(dashboard)
        dashboard.settings = SettingsDouble(
            {
                ("LOCATIONS", "park_x"): "10.0",
                ("LOCATIONS", "park_y"): "20.0",
                ("LOCATIONS", "park_z"): "0.0",
                ("PROBING", "tool_setter_x"): "50.0",
                ("PROBING", "tool_setter_y"): "60.0",
                ("PROBING", "tool_setter_z"): "0.0",
            }
        )
        dashboard.local_api = Mock()
        dashboard.local_api.linuxcnc.AUTO_PAUSE = 1
        dashboard.local_api.linuxcnc.AUTO_RESUME = 2
        dashboard.local_api.linuxcnc.AUTO_RUN = 3
        dashboard.local_api.linuxcnc.MODE_MANUAL = 1
        dashboard.local_api.linuxcnc.MODE_AUTO = 2
        dashboard.local_api.linuxcnc.MODE_MDI = 3
        dashboard.local_api.linuxcnc.STATE_ESTOP = 1
        dashboard.local_api.linuxcnc.STATE_ESTOP_RESET = 2
        dashboard.local_api.linuxcnc.STATE_OFF = 3
        dashboard.local_api.linuxcnc.STATE_ON = 4
        dashboard.local_api.linuxcnc.INTERP_IDLE = 1
        dashboard.local_api.linuxcnc.INTERP_PAUSED = 2
        dashboard.local_api.linuxcnc.INTERP_READING = 3
        dashboard.error_signal = Mock()
        dashboard.mock_coords = {
            "MACHINE_ON": True,
            "STATUS": "IDLE",
            "MODE": "MANUAL",
            "HOMED_COUNT": 4,
            "JOINTS": 4,
        }
        dashboard.is_estopped = False
        dashboard.active_jogs = set()
        dashboard.btn_cycle_pause = QPushButton("⏸ PAUSE")
        dashboard.btn_mode_manual = QPushButton("MANUAL")
        dashboard.btn_mode_mdi = QPushButton("MDI")
        dashboard.btn_mode_auto = QPushButton("AUTO")
        dashboard.jog_buttons = [QPushButton("X+"), QPushButton("X-")]
        dashboard.ssh_connected = True
        return dashboard

    def test_status_snapshot_idle_clears_paused(self):
        mock_lcnc = Mock()
        mock_lcnc.INTERP_IDLE = 1
        mock_lcnc.INTERP_PAUSED = 2
        mock_lcnc.MODE_AUTO = 2
        mock_lcnc.STATE_ON = 4
        mock_lcnc.STATE_ESTOP = 1

        # Status has paused=True, but interp_state is IDLE (e.g. after program abort)
        status = Mock(
            interp_state=1,
            paused=True,
            task_paused=True,
            task_mode=2,
            task_state=4,
            joints=4,
            spindle_speed=0.0,
            current_vel=0.0,
            feedrate=1.0,
            spindle_scale=1.0,
            homed=(1, 1, 1, 1),
            motion_line=0,
            axis_mask=7,
            linear_units=1.0,
            tool_in_spindle=0,
            pocket_prepped=-1,
            exec_state=1,
            actual_position=(0.0, 0.0, 0.0),
            dtg=(0.0, 0.0, 0.0),
            joint=(),
        )
        err_channel = Mock(poll=Mock(return_value=None))

        snapshot = linuxcnc_status_snapshot(mock_lcnc, status, err_channel)
        self.assertEqual(snapshot["STATUS"], "IDLE")
        self.assertFalse(snapshot["IS_PAUSED"])

    def test_pause_and_stop_cycle_flow_enables_jogging(self):
        dashboard = self._create_dashboard()
        dashboard.mock_coords["MODE"] = "AUTO"
        dashboard.mock_coords["STATUS"] = "RUNNING"

        # 1. Pause the program
        status_running = Mock(paused=False, task_paused=False, interp_state=3)
        dashboard.local_api.status.return_value = status_running
        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.pause_or_resume_cycle()

        self.assertEqual(dashboard.mock_coords["STATUS"], "PAUSED")
        self.assertTrue(dashboard.mock_coords["IS_PAUSED"])
        self.assertEqual(dashboard.btn_cycle_pause.text(), "▶ RESUME")

        # In PAUSED status, jog button press must return without jogging
        dashboard.jog_button_pressed("X", 1)
        dashboard.local_api.command.return_value.jog.assert_not_called()

        # 2. Stop the program
        status_stopped = Mock(
            current_vel=0.0,
            interp_state=1,
            task_mode=1,
            task_state=4,
            homed=(1, 1, 1, 1),
            joints=4,
            motion_teleop=True,
        )
        dashboard.local_api.status.return_value = status_stopped
        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.stop_cycle()

        # Verify stop_cycle aborted and returned cleanly to MANUAL mode and IDLE status
        self.assertEqual(dashboard.mock_coords["MODE"], "MANUAL")
        self.assertEqual(dashboard.mock_coords["STATUS"], "IDLE")
        self.assertFalse(dashboard.mock_coords["IS_PAUSED"])
        self.assertEqual(dashboard.btn_cycle_pause.text(), "⏸ PAUSE")

        # Now in MANUAL and IDLE, jogging should succeed!
        dashboard.local_api.command.reset_mock()
        dashboard.jog_button_pressed("X", 1)
        dashboard.local_api.command.return_value.jog.assert_called()

    def test_park_machine_blocked_when_unhomed(self):
        dashboard = self._create_dashboard()
        dashboard.mock_coords["HOMED_COUNT"] = 0
        dashboard.mock_coords["JOINTS"] = 4

        with patch("tabs.dashboard.QMessageBox.warning") as mock_warn:
            dashboard.park_machine()
            mock_warn.assert_called_once()
            self.assertIn("Homing Required", mock_warn.call_args[0][1])

    def test_park_machine_blocked_when_running_or_paused(self):
        dashboard = self._create_dashboard()
        dashboard.mock_coords["STATUS"] = "RUNNING"

        with patch("tabs.dashboard.QMessageBox.warning") as mock_warn:
            dashboard.park_machine()
            mock_warn.assert_called_once()
            self.assertIn("Interpreter Busy", mock_warn.call_args[0][1])

    def test_park_machine_blocked_when_power_off(self):
        dashboard = self._create_dashboard()
        dashboard.mock_coords["MACHINE_ON"] = False

        with patch("tabs.dashboard.QMessageBox.warning") as mock_warn:
            dashboard.park_machine()
            mock_warn.assert_called_once()
            self.assertIn("Power Off", mock_warn.call_args[0][1])

    def test_park_machine_local_success(self):
        dashboard = self._create_dashboard()
        status = Mock(task_state=4, interp_state=1, task_mode=1)
        dashboard.local_api.status.return_value = status

        from PyQt6.QtWidgets import QMessageBox

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            dashboard.park_machine()

        command = dashboard.local_api.command.return_value
        command.mode.assert_called_once_with(dashboard.local_api.linuxcnc.MODE_MDI)
        command.mdi.assert_any_call("G53 G0 Z0.0")
        command.mdi.assert_any_call("G53 G0 X10.0 Y20.0")
        self.assertEqual(dashboard.mock_coords["MODE"], "MDI")

    def test_move_to_setter_local_success(self):
        dashboard = self._create_dashboard()
        status = Mock(task_state=4, interp_state=1, task_mode=1)
        dashboard.local_api.status.return_value = status

        from PyQt6.QtWidgets import QMessageBox

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            dashboard.move_to_tool_setter()

        command = dashboard.local_api.command.return_value
        command.mode.assert_called_once_with(dashboard.local_api.linuxcnc.MODE_MDI)
        command.mdi.assert_any_call("G53 G0 Z0.0")
        command.mdi.assert_any_call("G53 G0 X50.0 Y60.0")
        self.assertEqual(dashboard.mock_coords["MODE"], "MDI")
