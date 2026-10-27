"""
Unit and integration tests for ToolChangeTab and ToolSetterDiagramWidget.
"""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from tabs.tool_change_tab import ToolChangeTab, ToolSetterDiagramWidget
from private.app.bootstrap import LinuxCNCApp


class SettingsDouble:
    def __init__(self, values=None):
        self.values = values or {}
        self.local_only = False

    def get_setting(self, section, key, default=None):
        return self.values.get((section, key), default)

    def save_setting(self, section, key, value):
        self.values[(section, key)] = str(value)

    def reload(self):
        pass


class TestToolChangeTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = SettingsDouble({
            ("TOOL_SETTER", "tool_setter_x"): "50.0",
            ("TOOL_SETTER", "tool_setter_y"): "60.0",
            ("TOOL_SETTER", "tool_setter_z"): "-180.0",
            ("TOOL_SETTER", "spindle_nose_z"): "-140.0",
            ("TOOL_SETTER", "safe_z"): "0.0",
            ("TOOL_SETTER", "search_feed"): "300.0",
            ("TOOL_SETTER", "latch_feed"): "25.0",
            ("TOOL_SETTER", "retract_dist"): "3.0",
            ("TOOL_SETTER", "max_travel"): "60.0",
            ("TOOL_SETTER", "change_x"): "10.0",
            ("TOOL_SETTER", "change_y"): "20.0",
            ("TOOL_SETTER", "change_z"): "-10.0",
            ("TOOL_SETTER", "last_measured_len"): "45.25",
        })
        self.tab = ToolChangeTab(self.settings)

    def tearDown(self):
        if hasattr(self, 'tab') and self.tab:
            self.tab.cleanup()

    def test_initialization_loads_settings(self):
        self.assertEqual(self.tab.spin_setter_x.value(), 50.0)
        self.assertEqual(self.tab.spin_setter_y.value(), 60.0)
        self.assertEqual(self.tab.spin_setter_z.value(), -180.0)
        self.assertEqual(self.tab.spin_spindle_nose_z.value(), -140.0)
        self.assertEqual(self.tab.spin_safe_z.value(), 0.0)
        self.assertEqual(self.tab.spin_search_feed.value(), 300.0)
        self.assertEqual(self.tab.spin_latch_feed.value(), 25.0)
        self.assertEqual(self.tab.spin_change_x.value(), 10.0)
        self.assertEqual(self.tab.spin_change_y.value(), 20.0)
        self.assertEqual(self.tab.last_measured_length, 45.25)
        self.assertIn("45.2500", self.tab.lbl_length_display.text())

    def test_diagram_widget_rendering_all_modes(self):
        diagram = ToolSetterDiagramWidget()
        diagram.resize(450, 360)

        for mode in ("idle", "measuring", "spindle_calib", "tool_change"):
            for tripped in (False, True):
                diagram.mode = mode
                diagram.set_probe_status(tripped)
                diagram.has_tool = (mode != "spindle_calib")
                pix = diagram.grab()
                self.assertFalse(pix.isNull())
                self.assertEqual(pix.width(), 450)
                self.assertEqual(pix.height(), 360)

    def test_capture_setter_xy_from_telemetry(self):
        dashboard = Mock()
        dashboard.mock_coords = {
            "ABSOLUTE_X": 125.4321,
            "ABSOLUTE_Y": 87.6543,
            "ABSOLUTE_Z": -120.0,
            "MACHINE_ON": True
        }
        tab = ToolChangeTab(self.settings, dashboard=dashboard)
        
        with patch("tabs.tool_change_tab.QMessageBox.information") as info_mock:
            tab.capture_setter_xy()
            self.assertAlmostEqual(tab.spin_setter_x.value(), 125.4321, places=4)
            self.assertAlmostEqual(tab.spin_setter_y.value(), 87.6543, places=4)
            self.assertEqual(self.settings.get_setting("TOOL_SETTER", "tool_setter_x"), "125.4321")
            self.assertEqual(self.settings.get_setting("PROBING", "tool_setter_x"), "125.4321")
            info_mock.assert_called_once()
        tab.cleanup()

    def test_capture_spindle_nose_z_with_gauge_block(self):
        dashboard = Mock()
        dashboard.mock_coords = {
            "ABSOLUTE_Z": -130.0,
            "MACHINE_ON": True
        }
        tab = ToolChangeTab(self.settings, dashboard=dashboard)
        tab.spin_gauge_block_h.setValue(25.4)

        with patch("tabs.tool_change_tab.QMessageBox.information") as info_mock:
            tab.capture_spindle_nose_z()
            # Spindle Z = -130.0 - 25.4 = -155.4
            self.assertAlmostEqual(tab.spin_spindle_nose_z.value(), -155.4, places=4)
            self.assertEqual(self.settings.get_setting("TOOL_SETTER", "spindle_nose_z"), "-155.4")
            info_mock.assert_called_once()
        tab.cleanup()

    def test_measure_tool_length_calculates_correct_offset(self):
        dashboard = Mock()
        dashboard.is_estopped = False
        dashboard.mock_coords = {
            "MACHINE_ON": True,
            "STATUS": "IDLE",
            "ABSOLUTE_Z": -94.750  # Touch contact height
        }
        tab = ToolChangeTab(self.settings, dashboard=dashboard)
        tab.spin_spindle_nose_z.setValue(-140.0)
        tab.spin_tool_num.setValue(3)

        emitted_measurements = []
        tab.tool_measured.connect(lambda t, l: emitted_measurements.append((t, l)))

        with patch("tabs.tool_change_tab.QMessageBox.information") as info_mock:
            tab.measure_tool_length()
            # L = -94.750 - (-140.0) = 45.250 mm
            self.assertAlmostEqual(tab.last_measured_length, 45.250, places=3)
            self.assertIn("45.2500", tab.lbl_length_display.text())
            self.assertEqual(emitted_measurements, [(3, 45.250)])
            info_mock.assert_called_once()
        tab.cleanup()

    def test_save_tool_table_and_apply_g43(self):
        dashboard = Mock()
        tab = ToolChangeTab(self.settings, dashboard=dashboard)
        tab.spin_tool_num.setValue(2)
        tab.last_measured_length = 52.3456
        tab.inp_tool_diam.setValue(8.0)

        with patch("tabs.tool_change_tab.QMessageBox.information"):
            tab.save_tool_table_offset()
            dashboard.execute_mdi.assert_called_with("G10 L1 P2 Z52.3456 D8.0000")

            tab.apply_g43_offset()
            dashboard.execute_mdi.assert_called_with("G43 H2")
        tab.cleanup()

    def test_safety_interlocks_block_motion(self):
        dashboard = Mock()
        dashboard.is_estopped = True
        tab = ToolChangeTab(self.settings, dashboard=dashboard)

        with patch("tabs.tool_change_tab.QMessageBox.warning") as warn_mock:
            tab.move_to_setter()
            warn_mock.assert_called_once()
            dashboard.execute_mdi.assert_not_called()

        tab.cleanup()

    def test_safe_probe_travel_calculation_clamps_to_joint_limits(self):
        """Verify downward travel is clamped to prevent exceeding joint 3 negative limit."""
        tab = ToolChangeTab(self.settings)
        # z_min is -134.0, safety cushion is 1.0mm
        # From 0.0 with 60 requested -> remaining is 0 - (-134 + 1) = 133.0 -> min(60, 133) = 60.0
        safe_travel, z_min = tab._calc_safe_probe_travel(0.0, 60.0)
        self.assertEqual(safe_travel, 60.0)
        self.assertEqual(z_min, -134.0)

        # From -100.0 with 60.0 requested -> remaining is -100 - (-134 + 1) = 33.0 -> clamped to 33.0
        safe_travel, _ = tab._calc_safe_probe_travel(-100.0, 60.0)
        self.assertAlmostEqual(safe_travel, 33.0, places=2)

        # Near bottom limit (-133.8) -> remaining is 0.0 -> returns 0.0 travel
        safe_travel, _ = tab._calc_safe_probe_travel(-133.8, 60.0)
        self.assertEqual(safe_travel, 0.0)
        tab.cleanup()

    def test_probe_hardware_source_selection_and_toggling(self):
        """Verify multiplexer lut5.0.in-2 switches between tool setter and 3D probe."""
        dashboard = Mock()
        tab = ToolChangeTab(self.settings, dashboard=dashboard)

        tab.select_tool_setter_hardware()
        dashboard.switch_probe_hardware.assert_called_with(0)
        self.assertEqual(tab.active_probe_source, "setter")
        self.assertIn("Tool Setter", tab.btn_probe_source.text())

        tab.toggle_probe_source()
        dashboard.switch_probe_hardware.assert_called_with(1)
        self.assertEqual(tab.active_probe_source, "probe")
        self.assertIn("3D Touch Probe", tab.btn_probe_source.text())

        tab.toggle_probe_source()
        dashboard.switch_probe_hardware.assert_called_with(0)
        self.assertEqual(tab.active_probe_source, "setter")
        tab.cleanup()

    def test_probe_sensor_trip_and_led_inversion(self):
        """Verify PROBE_VAL updates badge and LED inversion works for NC switches."""
        dashboard = Mock()
        dashboard.mock_coords = {"PROBE_VAL": 1, "ABSOLUTE_X": 0.0, "ABSOLUTE_Y": 0.0, "ABSOLUTE_Z": 0.0}
        tab = ToolChangeTab(self.settings, dashboard=dashboard)

        # Normally Open (uninverted): PROBE_VAL=1 -> TRIPPED
        tab.chk_invert_led.setChecked(False)
        tab._poll_telemetry()
        self.assertIn("TRIPPED", tab.lbl_sensor_badge.text())

        # Normally Closed (inverted): PROBE_VAL=1 -> READY (closed circuit is safe)
        tab.chk_invert_led.setChecked(True)
        tab._poll_telemetry()
        self.assertIn("READY", tab.lbl_sensor_badge.text())

        # PROBE_VAL=0 with inverted logic -> TRIPPED (circuit broken)
        dashboard.mock_coords["PROBE_VAL"] = 0
        tab._poll_telemetry()
        self.assertIn("TRIPPED", tab.lbl_sensor_badge.text())
        tab.cleanup()

    def test_measure_tool_uses_g91_incremental_mode(self):
        """Verify tool measurement uses G91 incremental mode and safe Z restoration."""
        dashboard = Mock()
        dashboard.is_estopped = False
        dashboard.mock_coords = {"MACHINE_ON": True, "STATUS": "IDLE", "ABSOLUTE_Z": -90.0}
        tab = ToolChangeTab(self.settings, dashboard=dashboard)
        tab.spin_setter_z.setValue(0.0)

        with patch("tabs.tool_change_tab.QMessageBox.information"):
            tab.measure_tool_length()

        # Check that G91 G38.2 was dispatched rather than unsafe G90 G38.2
        mdi_calls = [call[0][0] for call in dashboard.execute_mdi.call_args_list]
        self.assertTrue(any("G91 G38.2" in c for c in mdi_calls), "Probe move must use G91 incremental mode")
        self.assertTrue(any("G90 G53 G0" in c for c in mdi_calls), "Probe move must restore G90 safe height")
        tab.cleanup()

    def test_tool_change_tab_position_in_app(self):
        """Verify Tool Change tab is registered in LinuxCNCApp in all_tabs."""
        import inspect
        source = inspect.getsource(LinuxCNCApp.__init__)
        probing_idx = source.find('(self.probing_tab, "📐 PROBING")')
        tool_change_idx = source.find('(self.tool_change_tab, "🔄 TOOL CHANGE")')
        tool_table_idx = source.find('(self.tool_table_tab, "🧰 TOOL TABLE")')

        self.assertGreater(probing_idx, -1, "Probing tab must be present")
        self.assertGreater(tool_change_idx, -1, "Tool Change tab must be present in all_tabs")
        self.assertGreater(tool_table_idx, -1, "Tool Table tab must be present")
        self.assertLess(probing_idx, tool_change_idx, "Probing tab must precede Tool Change tab")
        self.assertLess(tool_change_idx, tool_table_idx, "Tool Change tab must precede Tool Table tab")


if __name__ == "__main__":
    unittest.main()
