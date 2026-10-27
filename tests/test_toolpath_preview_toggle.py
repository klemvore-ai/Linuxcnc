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

    def save_setting(self, section, key, value):
        self.values[(section, key)] = str(value)


class ToolpathPreviewToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_enabled_by_default(self):
        viewer = ToolpathViewer()
        self.assertTrue(viewer.preview_enabled)
        viewer.update_path("G1 X10 Y10 Z0\nG1 X20 Y20 Z0")
        self.assertGreater(len(viewer.segment_items), 0)

    def test_disabled_preview_skips_parsing(self):
        viewer = ToolpathViewer()
        viewer.set_preview_enabled(False)
        self.assertFalse(viewer.preview_enabled)

        # Update with gcode while disabled
        viewer.update_path("G1 X10 Y10 Z0\nG1 X20 Y20 Z0")
        # Should not create any segment items
        self.assertEqual(len(viewer.segment_items), 0)

        # Enabling should parse
        viewer.set_preview_enabled(True, "G1 X10 Y10 Z0\nG1 X20 Y20 Z0")
        self.assertGreater(len(viewer.segment_items), 0)

    def test_dashboard_toggle_preview_button(self):
        dashboard = MachineDashboard.__new__(MachineDashboard)
        dashboard.settings = SettingsDouble({
            ("UI_PREFERENCES", "enable_toolpath_preview"): "True"
        })
        dashboard.toolpath_viewer = ToolpathViewer()
        dashboard.default_gcode_editor = type("Editor", (), {
            "toPlainText": lambda self: "G1 X5 Y5 Z0",
        })()
        dashboard.btn_toggle_preview = type("Button", (), {
            "text": "",
            "tooltip": "",
            "style": "",
            "setText": lambda self, t: setattr(self, "text", t),
            "setToolTip": lambda self, t: setattr(self, "tooltip", t),
            "setStyleSheet": lambda self, s: setattr(self, "style", s),
        })()

        dashboard.update_preview_toggle_button()
        self.assertIn("Disable", dashboard.btn_toggle_preview.text)

        # Toggle to disabled
        dashboard.toggle_toolpath_preview()
        self.assertFalse(dashboard.toolpath_viewer.preview_enabled)
        self.assertEqual(len(dashboard.toolpath_viewer.segment_items), 0)
        self.assertIn("Enable", dashboard.btn_toggle_preview.text)
        self.assertEqual(dashboard.settings.get_setting("UI_PREFERENCES", "enable_toolpath_preview"), "False")

        # Toggle back to enabled
        dashboard.toggle_toolpath_preview()
        self.assertTrue(dashboard.toolpath_viewer.preview_enabled)
        self.assertGreater(len(dashboard.toolpath_viewer.segment_items), 0)
        self.assertIn("Disable", dashboard.btn_toggle_preview.text)
        self.assertEqual(dashboard.settings.get_setting("UI_PREFERENCES", "enable_toolpath_preview"), "True")


if __name__ == "__main__":
    unittest.main()
