import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QSlider, QWidget
from tabs.dashboard import MachineDashboard, ToolpathViewer


class SettingsDouble:
    def __init__(self, values=None, local_only=False):
        self.values = values or {}
        self.local_only = local_only

    def get_setting(self, section, key, default=None):
        return self.values.get((section, key), default)


class DashboardJogGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _create_dashboard(self, local_only=True, mode="MANUAL", status="IDLE"):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        QWidget.__init__(dashboard)
        dashboard.settings = SettingsDouble({
            ("MACHINE_CONFIG", "kinematics"): "XYZ",
            ("AXIS_X", "vel"): "50.0",
            ("AXIS_Y", "vel"): "50.0",
            ("AXIS_Z", "vel"): "50.0",
        }, local_only=local_only)
        dashboard.is_estopped = False
        dashboard.mock_coords = {"MACHINE_ON": True, "STATUS": status, "MODE": mode, "HOMED_COUNT": 3, "JOINTS": 3}
        dashboard.active_jogs = set()
        dashboard.jog_speed_slider = QSlider()
        dashboard.jog_speed_slider.setValue(20)
        dashboard.error_signal = Mock()
        dashboard._last_update_state = {}
        dashboard.custom_dro_labels = {}
        dashboard.custom_status_labels = {}
        dashboard.home_buttons = {}
        dashboard.jog_buttons = {}

        if local_only:
            dashboard.local_api = Mock()
            dashboard.local_api.linuxcnc.MODE_MANUAL = 1
            dashboard.local_api.linuxcnc.MODE_AUTO = 2
            dashboard.local_api.linuxcnc.MODE_MDI = 3
            dashboard.local_api.linuxcnc.STATE_ON = 4
            dashboard.local_api.linuxcnc.STATE_ESTOP = 1
            dashboard.local_api.linuxcnc.INTERP_IDLE = 1
            dashboard.local_api.linuxcnc.INTERP_PAUSED = 3
            dashboard.local_api.linuxcnc.INTERP_READING = 2
            dashboard.local_api.linuxcnc.JOG_CONTINUOUS = 1
            dashboard.local_api.linuxcnc.JOG_STOP = 2

            task_mode = 1 if mode == "MANUAL" else (2 if mode == "AUTO" else 3)
            interp_state = 1 if status == "IDLE" else (3 if status == "PAUSED" else 2)
            mock_status = Mock(
                task_state=4,
                task_mode=task_mode,
                interp_state=interp_state,
                homed=[True, True, True],
                joints=3,
                motion_teleop=True,
            )
            dashboard.local_api.status.return_value = mock_status
        else:
            dashboard.local_api = None
            dashboard._send_b64_py_cmd = Mock()
        return dashboard

    def test_jog_does_not_popup_when_homing_state_is_unknown(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble({
            ("MACHINE_CONFIG", "kinematics"): "XYYZ",
            ("AXIS_Y", "vel"): "50.0",
        })
        dashboard.is_estopped = False
        dashboard.mock_coords = {}
        dashboard.active_jogs = set()
        dashboard.jog_speed_slider = type("Slider", (), {"value": lambda self: 10})()
        dashboard._send_b64_py_cmd = lambda code, **kwargs: None

        with patch("tabs.dashboard.QMessageBox.warning") as warning:
            dashboard.jog_button_pressed("Y", 1)

        warning.assert_not_called()

    def test_large_gcode_preview_is_simplified(self):
        viewer = ToolpathViewer()
        huge_text = "\n".join(f"G1 X{i} Y{i} Z0" for i in range(300000))

        viewer.update_path(huge_text)

        self.assertGreater(len(viewer.segment_items), 0)
        self.assertLessEqual(len(viewer.segment_items), viewer.MAX_PREVIEW_SEGMENTS)
        self.assertGreaterEqual(len(viewer.scene.items()), 1)

    def test_jog_blocked_while_program_running(self):
        dashboard = self._create_dashboard(local_only=True, mode="MANUAL", status="RUNNING")
        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.jog_button_pressed("X", 1)

        dashboard.local_api.command.return_value.jog.assert_not_called()
        self.assertEqual(len(dashboard.active_jogs), 0)

    def test_jog_blocked_while_program_paused(self):
        dashboard = self._create_dashboard(local_only=True, mode="MANUAL", status="PAUSED")
        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.jog_button_pressed("X", 1)

        dashboard.local_api.command.return_value.jog.assert_not_called()
        self.assertEqual(len(dashboard.active_jogs), 0)

    def test_jog_blocked_in_auto_mode(self):
        dashboard = self._create_dashboard(local_only=True, mode="AUTO", status="IDLE")
        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.jog_button_pressed("X", 1)

        dashboard.local_api.command.return_value.jog.assert_not_called()
        self.assertEqual(len(dashboard.active_jogs), 0)

    def test_jog_blocked_in_mdi_mode(self):
        dashboard = self._create_dashboard(local_only=True, mode="MDI", status="IDLE")
        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.jog_button_pressed("X", 1)

        dashboard.local_api.command.return_value.jog.assert_not_called()
        self.assertEqual(len(dashboard.active_jogs), 0)

    def test_jog_allowed_in_manual_mode_local_api(self):
        dashboard = self._create_dashboard(local_only=True, mode="MANUAL", status="IDLE")
        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.jog_button_pressed("X", 1)

        command = dashboard.local_api.command.return_value
        command.jog.assert_called_once()
        self.assertIn("X", dashboard.active_jogs)

    def test_jog_released_stops_jog_local_api(self):
        dashboard = self._create_dashboard(local_only=True, mode="MANUAL", status="IDLE")
        dashboard.active_jogs.add("X")
        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.jog_button_released("X")

        command = dashboard.local_api.command.return_value
        command.jog.assert_called_once_with(dashboard.local_api.linuxcnc.JOG_STOP, False, 0)
        self.assertNotIn("X", dashboard.active_jogs)

    def test_select_machine_mode_manual_mdi_auto(self):
        dashboard = self._create_dashboard(local_only=True, mode="MANUAL", status="IDLE")
        dashboard.btn_mode_manual = QPushButton("MANUAL")
        dashboard.btn_mode_mdi = QPushButton("MDI")
        dashboard.btn_mode_auto = QPushButton("AUTO")

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.select_machine_mode("mdi")
            dashboard.local_api.command.return_value.mode.assert_called_with(dashboard.local_api.linuxcnc.MODE_MDI)
            self.assertEqual(dashboard.mock_coords["MODE"], "MDI")

            dashboard.select_machine_mode("auto")
            dashboard.local_api.command.return_value.mode.assert_called_with(dashboard.local_api.linuxcnc.MODE_AUTO)
            self.assertEqual(dashboard.mock_coords["MODE"], "AUTO")

            dashboard.select_machine_mode("manual")
            dashboard.local_api.command.return_value.mode.assert_called_with(dashboard.local_api.linuxcnc.MODE_MANUAL)
            self.assertEqual(dashboard.mock_coords["MODE"], "MANUAL")

    def test_select_machine_mode_blocked_while_running_or_paused(self):
        dashboard = self._create_dashboard(local_only=True, mode="AUTO", status="RUNNING")
        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.QMessageBox.warning"
        ) as warning:
            dashboard.select_machine_mode("manual")

        warning.assert_called_once()
        dashboard.local_api.command.return_value.mode.assert_not_called()

    def test_jog_buttons_disabled_in_auto_mdi_or_running(self):
        dashboard = self._create_dashboard(local_only=True, mode="MANUAL", status="IDLE")
        btn_jog = QPushButton("X+")
        dashboard.jog_buttons = {"X+": btn_jog}
        dashboard.status_leds = {
            "ESTOP": QPushButton(),
            "POWER": QPushButton(),
            "HOMED": QPushButton(),
            "READY": QPushButton(),
        }

        # In MANUAL and IDLE and powered on -> enabled
        dashboard.update_real_coords({"STATUS": "IDLE", "MODE": "MANUAL", "MACHINE_ON": True, "JOINTS": 3, "HOMED_COUNT": 3})
        self.assertTrue(btn_jog.isEnabled())

        # In AUTO -> disabled
        dashboard.update_real_coords({"STATUS": "IDLE", "MODE": "AUTO", "MACHINE_ON": True, "JOINTS": 3, "HOMED_COUNT": 3})
        self.assertFalse(btn_jog.isEnabled())

        # In MDI -> disabled
        dashboard.update_real_coords({"STATUS": "IDLE", "MODE": "MDI", "MACHINE_ON": True, "JOINTS": 3, "HOMED_COUNT": 3})
        self.assertFalse(btn_jog.isEnabled())

        # In MANUAL but RUNNING -> disabled
        dashboard.update_real_coords({"STATUS": "RUNNING", "MODE": "MANUAL", "MACHINE_ON": True, "JOINTS": 3, "HOMED_COUNT": 3})
        self.assertFalse(btn_jog.isEnabled())

        # In MANUAL but PAUSED -> disabled
        dashboard.update_real_coords({"STATUS": "PAUSED", "MODE": "MANUAL", "MACHINE_ON": True, "JOINTS": 3, "HOMED_COUNT": 3})
        self.assertFalse(btn_jog.isEnabled())

    def test_execute_mdi_blocked_when_running_or_paused(self):
        dashboard = self._create_dashboard(local_only=True, mode="MDI", status="RUNNING")
        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.QMessageBox.warning"
        ) as warning:
            dashboard.execute_mdi("G0 X10")

        warning.assert_called_once()
        dashboard.local_api.command.return_value.mdi.assert_not_called()


if __name__ == "__main__":
    unittest.main()
