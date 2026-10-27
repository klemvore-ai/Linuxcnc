import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QTextEdit, QWidget
from tabs.dashboard import MachineDashboard
import tempfile


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


class PauseStopCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _create_dashboard(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        QWidget.__init__(dashboard)
        dashboard.settings = SettingsDouble()
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
        dashboard.mock_coords = {"MACHINE_ON": True, "STATUS": "RUNNING", "MODE": "AUTO"}
        dashboard.is_estopped = False
        dashboard.active_jogs = set()
        dashboard.btn_cycle_pause = QPushButton("⏸ PAUSE")
        dashboard.local_api.error_channel.return_value.poll.return_value = None
        return dashboard

    def test_pause_asserts_feed_hold_and_auto_pause(self):
        dashboard = self._create_dashboard()
        status = Mock(paused=False, task_paused=False, interp_state=3)
        dashboard.local_api.status.return_value = status

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.pause_or_resume_cycle()

        command = dashboard.local_api.command.return_value
        command.set_feed_hold.assert_called_once_with(1)
        command.auto.assert_called_once_with(1)
        self.assertEqual(dashboard.btn_cycle_pause.text(), "▶ RESUME")

    def test_resume_clears_feed_hold_and_auto_resume(self):
        dashboard = self._create_dashboard()
        dashboard.mock_coords["STATUS"] = "PAUSED"
        status = Mock(paused=True, task_paused=True, interp_state=2)
        dashboard.local_api.status.return_value = status

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.pause_or_resume_cycle()

        command = dashboard.local_api.command.return_value
        command.set_feed_hold.assert_called_once_with(0)
        command.auto.assert_called_once_with(2)
        self.assertEqual(dashboard.btn_cycle_pause.text(), "⏸ PAUSE")

    def test_stop_cycle_clears_feed_hold_aborts_and_switches_to_manual_mode(self):
        dashboard = self._create_dashboard()
        dashboard.btn_cycle_pause.setText("▶ RESUME")
        dashboard.active_jogs.add("X")

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.stop_cycle()

        command = dashboard.local_api.command.return_value
        command.set_feed_hold.assert_called_once_with(0)
        command.abort.assert_called_once_with()
        command.mode.assert_called_once_with(1)
        command.reset_interpreter.assert_called_once_with()
        self.assertEqual(dashboard.btn_cycle_pause.text(), "⏸ PAUSE")
        self.assertEqual(len(dashboard.active_jogs), 0)

    def test_toggle_estop_resets_manual_mode_and_fault(self):
        dashboard = self._create_dashboard()
        status = Mock(task_state=1)
        dashboard.local_api.status.return_value = status

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.toggle_estop()

        command = dashboard.local_api.command.return_value
        command.abort.assert_called_once_with()
        command.mode.assert_called_once_with(1)
        command.state.assert_called_once_with(2)

        # In LinuxCNC, STATE_ESTOP_RESET (2) MUST be commanded BEFORE MODE_MANUAL (1)
        state_idx = next(i for i, c in enumerate(command.mock_calls) if c[0] == "state" and c[1] == (2,))
        mode_idx = next(i for i, c in enumerate(command.mock_calls) if c[0] == "mode" and c[1] == (1,))
        self.assertLess(state_idx, mode_idx, "STATE_ESTOP_RESET must be commanded before MODE_MANUAL")
        self.assertFalse(dashboard.is_estopped)
        self.assertFalse(dashboard.mock_coords.get("ESTOP_ACTIVATED"))

    def test_toggle_power_auto_resets_software_estop_when_physical_estop_is_healthy(self):
        dashboard = self._create_dashboard()
        status = Mock(task_state=1, joints=0)
        # Sequence of polls: first in ESTOP, then after reset transitions to ESTOP_RESET
        def poll_side_effect():
            if command.state.called:
                status.task_state = 2
        status.poll.side_effect = poll_side_effect
        dashboard.local_api.status.return_value = status
        dashboard.local_api.get_hal_value.side_effect = lambda pin: True if "iocontrol.0.emc-enable-in" in pin else False

        command = dashboard.local_api.command.return_value

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.toggle_power()

        command.abort.assert_called_once_with()
        command.mode.assert_called_once_with(1)
        # Should command STATE_ESTOP_RESET and then STATE_ON
        command.state.assert_any_call(2)
        command.state.assert_any_call(4)
        state_reset_idx = next(i for i, c in enumerate(command.mock_calls) if c[0] == "state" and c[1] == (2,))
        mode_idx = next(i for i, c in enumerate(command.mock_calls) if c[0] == "mode" and c[1] == (1,))
        self.assertLess(state_reset_idx, mode_idx, "STATE_ESTOP_RESET must be commanded before MODE_MANUAL")

    def test_toggle_power_blocks_when_physical_estop_tripped(self):
        dashboard = self._create_dashboard()
        status = Mock(task_state=1)
        dashboard.local_api.status.return_value = status
        # iocontrol.0.emc-enable-in is False -> physical estop tripped
        dashboard.local_api.get_hal_value.side_effect = lambda pin: False if "iocontrol.0.emc-enable-in" in pin else True

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.QMessageBox.warning"
        ) as warning:
            dashboard.toggle_power()

        warning.assert_called_once()
        command = dashboard.local_api.command.return_value
        command.state.assert_not_called()

    def test_jog_blocked_while_program_is_running_or_paused(self):
        dashboard = self._create_dashboard()
        dashboard.jog_speed_slider = Mock(value=Mock(return_value=10))

        # 1. Blocked when RUNNING
        dashboard.mock_coords["STATUS"] = "RUNNING"
        dashboard.jog_button_pressed("X", 1)
        self.assertNotIn("X", dashboard.active_jogs)

        # 2. Blocked when PAUSED
        dashboard.mock_coords["STATUS"] = "PAUSED"
        dashboard.jog_button_pressed("Y", 1)
        self.assertNotIn("Y", dashboard.active_jogs)

        # 3. Blocked when MODE is AUTO
        dashboard.mock_coords["STATUS"] = "IDLE"
        dashboard.mock_coords["MODE"] = "AUTO"
        dashboard.jog_button_pressed("Z", 1)
        self.assertNotIn("Z", dashboard.active_jogs)

        # 4. Blocked when machine is powered off
        dashboard.mock_coords["MODE"] = "MANUAL"
        dashboard.mock_coords["MACHINE_ON"] = False
        dashboard.jog_button_pressed("X", 1)
        self.assertNotIn("X", dashboard.active_jogs)

    def test_update_real_coords_succeeds_without_prior_update_status(self):
        dashboard = self._create_dashboard()
        if hasattr(dashboard, "_last_update_state"):
            delattr(dashboard, "_last_update_state")
        dashboard.custom_dro_labels = {"X": Mock()}
        dashboard.custom_status_labels = {"HOMED": Mock()}

        dashboard.update_real_coords({
            "X": 12.345,
            "HOMED_COUNT": 4,
            "JOINTS": 4,
            "STATUS": "IDLE",
        })

        self.assertEqual(dashboard.mock_coords["X"], 12.345)
        self.assertTrue(hasattr(dashboard, "_last_update_state"))
        self.assertTrue(dashboard._last_update_state.get("custom_homed_active"))

    def test_load_gcode_copies_external_file_to_nc_folder_first(self):
        dashboard = self._create_dashboard()
        dashboard.sync_gcode_to_machine = Mock()

        with tempfile.TemporaryDirectory() as usb_dir, tempfile.TemporaryDirectory() as nc_dir:
            dashboard.settings.save_setting("PATHS", "machine_gcode_root", nc_dir)
            source_file = os.path.join(usb_dir, "test_job.ngc")
            test_content = "(Test Job)\nG21\nG0 X10 Y20\nM2\n"
            with open(source_file, "w", encoding="utf-8") as f:
                f.write(test_content)

            editor = QTextEdit()

            with patch("tabs.dashboard.QFileDialog.getOpenFileName", return_value=(source_file, "G-Code Files (*.ngc)")):
                dashboard.load_gcode(editor)

            dest_file = os.path.join(nc_dir, "test_job.ngc")
            self.assertTrue(os.path.exists(dest_file), "Expected file to be copied into nc_dir")
            with open(dest_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), test_content)

            self.assertEqual(editor.toPlainText(), test_content)
            dashboard.sync_gcode_to_machine.assert_called_once_with(test_content)

    def test_load_gcode_handles_file_already_in_nc_folder(self):
        dashboard = self._create_dashboard()
        dashboard.sync_gcode_to_machine = Mock()

        with tempfile.TemporaryDirectory() as nc_dir:
            dashboard.settings.save_setting("PATHS", "machine_gcode_root", nc_dir)
            local_file = os.path.join(nc_dir, "existing_job.ngc")
            test_content = "(Existing Job)\nG0 Z5\nM30\n"
            with open(local_file, "w", encoding="utf-8") as f:
                f.write(test_content)

            editor = QTextEdit()

            with patch("tabs.dashboard.QFileDialog.getOpenFileName", return_value=(local_file, "G-Code Files (*.ngc)")):
                dashboard.load_gcode(editor)

            self.assertEqual(editor.toPlainText(), test_content)
            dashboard.sync_gcode_to_machine.assert_called_once_with(test_content)

    def test_clear_gcode_clears_editor_preview_and_unloads_machine_program(self):
        dashboard = self._create_dashboard()
        dashboard.default_gcode_editor = QTextEdit()
        dashboard.default_gcode_editor.setPlainText("G0 X100 Y100\nM2")
        dashboard.toolpath_viewer = Mock()
        dashboard.progress_bar = Mock()

        with tempfile.TemporaryDirectory() as nc_dir:
            dashboard.settings.save_setting("PATHS", "machine_gcode_root", nc_dir)
            active_file = os.path.join(nc_dir, "active_program.ngc")
            with open(active_file, "w", encoding="utf-8") as f:
                f.write("G0 X100 Y100\nM2")

            with patch("tabs.dashboard.client_from_settings", return_value=None):
                dashboard.clear_gcode(dashboard.default_gcode_editor)

            self.assertEqual(dashboard.default_gcode_editor.toPlainText(), "")
            self.assertIsNone(dashboard.toolpath_viewer.last_gcode_text)
            dashboard.toolpath_viewer.update_path.assert_called_once_with("")
            dashboard.progress_bar.setValue.assert_called_once_with(0)
            dashboard.progress_bar.setFormat.assert_called_once_with("No Program Loaded")

            # Active file is removed or emptied
            if os.path.exists(active_file):
                with open(active_file, "r", encoding="utf-8") as f:
                    self.assertEqual(f.read(), "")

            command = dashboard.local_api.command.return_value
            command.abort.assert_called_once()
            command.mode.assert_called_once_with(1)
            command.reset_interpreter.assert_called_once()

    def test_start_cycle_blocked_when_editor_is_empty(self):
        dashboard = self._create_dashboard()
        dashboard.default_gcode_editor = QTextEdit()
        dashboard.default_gcode_editor.setPlainText("")
        dashboard._program_sync_in_progress = False

        status = Mock(file="", task_state=4)
        dashboard.local_api.status.return_value = status

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.QMessageBox.warning"
        ) as warning:
            dashboard.start_cycle()

        warning.assert_called_once()
        command = dashboard.local_api.command.return_value
        command.auto.assert_not_called()

    def test_start_cycle_blocked_when_not_in_auto_mode(self):
        dashboard = self._create_dashboard()
        dashboard.default_gcode_editor = QTextEdit()
        dashboard.default_gcode_editor.setPlainText("G0 X10\nM2")
        dashboard._program_sync_in_progress = False

        # task_mode is MODE_MANUAL (1), not MODE_AUTO (2)
        status = Mock(file="/tmp/test.ngc", task_state=4, task_mode=1)
        dashboard.local_api.status.return_value = status

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "tabs.dashboard.QMessageBox.warning"
        ) as warning:
            dashboard.start_cycle()

        warning.assert_called_once()
        self.assertIn("AUTO", warning.call_args[0][2])
        command = dashboard.local_api.command.return_value
        command.mode.assert_not_called()
        command.auto.assert_not_called()

    def test_start_cycle_succeeds_when_in_auto_mode_without_auto_switching(self):
        dashboard = self._create_dashboard()
        dashboard.default_gcode_editor = QTextEdit()
        dashboard.default_gcode_editor.setPlainText("G0 X10\nM2")
        dashboard._program_sync_in_progress = False

        # task_mode is MODE_AUTO (2)
        status = Mock(file="/tmp/test.ngc", task_state=4, task_mode=2)
        dashboard.local_api.status.return_value = status

        with patch("tabs.dashboard.client_from_settings", return_value=None), patch(
            "os.path.exists", return_value=True
        ):
            dashboard.start_cycle()

        command = dashboard.local_api.command.return_value
        # Mode must NOT be switched automatically
        command.mode.assert_not_called()
        command.auto.assert_called_once_with(dashboard.local_api.linuxcnc.AUTO_RUN, 0)

    def test_remote_toggle_estop_resets_estop(self):
        dashboard = self._create_dashboard()
        dashboard.settings.local_only = False
        dashboard.is_estopped = True
        dashboard.mock_coords["ESTOP_ACTIVATED"] = True
        dashboard._send_b64_py_cmd = Mock()

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.toggle_estop()

        dashboard._send_b64_py_cmd.assert_called_once()
        cmd_py = dashboard._send_b64_py_cmd.call_args[0][0]
        # Verify STATE_ESTOP_RESET is commanded before MODE_MANUAL
        state_idx = cmd_py.find("STATE_ESTOP_RESET")
        mode_idx = cmd_py.find("MODE_MANUAL")
        self.assertNotEqual(state_idx, -1)
        self.assertNotEqual(mode_idx, -1)
        self.assertLess(state_idx, mode_idx, "Remote script must reset E-stop before setting manual mode")
        self.assertFalse(dashboard.is_estopped)
        self.assertFalse(dashboard.mock_coords["ESTOP_ACTIVATED"])

    def test_remote_toggle_power_when_estopped_clears_estop_and_powers_on(self):
        dashboard = self._create_dashboard()
        dashboard.settings.local_only = False
        dashboard.is_estopped = True
        dashboard.mock_coords["ESTOP_ACTIVATED"] = True
        dashboard.mock_coords["MACHINE_ON"] = False
        dashboard._send_b64_py_cmd = Mock()

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.toggle_power()

        dashboard._send_b64_py_cmd.assert_called_once()
        cmd_py = dashboard._send_b64_py_cmd.call_args[0][0]
        state_reset_idx = cmd_py.find("STATE_ESTOP_RESET")
        state_on_idx = cmd_py.find("STATE_ON")
        self.assertNotEqual(state_reset_idx, -1)
        self.assertNotEqual(state_on_idx, -1)
        self.assertLess(state_reset_idx, state_on_idx)
        self.assertFalse(dashboard.is_estopped)
        self.assertFalse(dashboard.mock_coords["ESTOP_ACTIVATED"])
        self.assertTrue(dashboard.mock_coords["MACHINE_ON"])

    def test_stop_cycle_shuts_down_spindle_and_coolant(self):
        dashboard = self._create_dashboard()
        dashboard.btn_cycle_pause.setText("▶ RESUME")

        with patch("tabs.dashboard.client_from_settings", return_value=None):
            dashboard.stop_cycle()

        command = dashboard.local_api.command.return_value
        command.set_feed_hold.assert_called_once_with(0)
        command.abort.assert_called_once_with()
        command.spindle.assert_called_once_with(dashboard.local_api.linuxcnc.SPINDLE_OFF)
        command.flood.assert_called_once_with(0)
        command.mist.assert_called_once_with(0)
        command.mode.assert_called_once_with(1)

    def test_position_fetcher_thread_does_not_assert_estop(self):
        from tabs.dashboard import PositionFetcherThread
        import base64
        fetcher = PositionFetcherThread.__new__(PositionFetcherThread)
        fetcher.settings = SettingsDouble()
        fetcher.ip = "192.168.1.100"
        fetcher.user = "cnc"
        fetcher.agent_client = None
        PositionFetcherThread.__init__(fetcher, fetcher.settings, fetcher.ip, fetcher.user)

        # Decode py_code from cmd
        cmd = fetcher.cmd
        self.assertIn("base64.b64decode", cmd)
        b64_str = cmd.split("'")[1]
        decoded_code = base64.b64decode(b64_str).decode()
        self.assertNotIn("c.state(linuxcnc.STATE_ESTOP)", decoded_code)
        self.assertNotIn("c.state", decoded_code)


if __name__ == "__main__":
    unittest.main()
