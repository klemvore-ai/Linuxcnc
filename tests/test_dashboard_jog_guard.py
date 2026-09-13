import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from tabs.dashboard import MachineDashboard, ToolpathViewer


class SettingsDouble:
    def __init__(self, values=None):
        self.values = values or {}

    def get_setting(self, section, key, default=None):
        return self.values.get((section, key), default)


class DashboardJogGuardTests(unittest.TestCase):
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
        dashboard._send_b64_py_cmd = lambda code: None

        with patch("tabs.dashboard.QMessageBox.warning") as warning:
            dashboard.jog_button_pressed("Y", 1)

        warning.assert_not_called()

    def test_large_gcode_preview_is_simplified(self):
        app = QApplication.instance() or QApplication([])
        viewer = ToolpathViewer()
        huge_text = "\n".join(f"G1 X{i} Y{i} Z0" for i in range(300000))

        viewer.update_path(huge_text)

        self.assertGreater(len(viewer.segment_items), 0)
        self.assertLessEqual(len(viewer.segment_items), viewer.MAX_PREVIEW_SEGMENTS)
        self.assertGreaterEqual(len(viewer.scene.items()), 1)


if __name__ == "__main__":
    unittest.main()
