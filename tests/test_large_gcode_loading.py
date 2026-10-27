import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QMessageBox, QWidget

from tabs.dashboard import MachineDashboard, ToolpathViewer


class SettingsDouble:
    def __init__(self, values=None):
        self.values = values or {}

    def get_setting(self, section, key, default=None):
        return self.values.get((section, key), default)

    def save_setting(self, section, key, value):
        self.values[(section, key)] = str(value)


class LargeGcodeLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.nc_files_dir = os.path.join(self.temp_dir.name, "nc_files")
        os.makedirs(self.nc_files_dir, exist_ok=True)

        self.dashboard = MachineDashboard.__new__(MachineDashboard)
        QWidget.__init__(self.dashboard)
        self.dashboard.settings = SettingsDouble({
            ("PATHS", "nc_files_dir"): self.nc_files_dir,
            ("UI_PREFERENCES", "enable_toolpath_preview"): "True",
        })
        self.dashboard.local_api = None
        self.dashboard.mock_coords = {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'LINE': 0, 'TOTAL_LINES': 0}
        self.dashboard._is_large_file_preview = False
        self.dashboard.loaded_gcode_path = None
        self.dashboard._program_sync_in_progress = False
        self.dashboard.error_signal = MagicMock()
        self.dashboard.program_sync_state = MagicMock()
        self.dashboard.toolpath_viewer = ToolpathViewer()
        self.dashboard.default_gcode_editor = QPlainTextEdit()
        self.dashboard.LARGE_FILE_THRESHOLD = 5000  # Low threshold for test convenience

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_small_file_loads_fully(self):
        # Create a small file below the 5000 byte threshold
        src_path = os.path.join(self.temp_dir.name, "part_small.ngc")
        sample_gcode = "G21\nG90\nG0 X0 Y0 Z5\nG1 Z-1 F100\nG1 X10 Y10\nM2\n"
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(sample_gcode)

        with patch.object(self.dashboard, "sync_gcode_to_machine") as mock_sync, \
             patch("PyQt6.QtWidgets.QFileDialog.getOpenFileName", return_value=(src_path, "")):
            self.dashboard.load_gcode(self.dashboard.default_gcode_editor)

        self.assertFalse(self.dashboard._is_large_file_preview)
        self.assertEqual(self.dashboard.default_gcode_editor.toPlainText(), sample_gcode)
        self.assertIsNotNone(self.dashboard.loaded_gcode_path)
        self.assertTrue(os.path.exists(self.dashboard.loaded_gcode_path))
        mock_sync.assert_called_once_with(sample_gcode)
        self.assertGreater(len(self.dashboard.toolpath_viewer.segment_items), 0)

    def test_large_file_loads_preview_and_streams_toolpath(self):
        # Create a large file exceeding threshold
        src_path = os.path.join(self.temp_dir.name, "part_large.ngc")
        lines = [f"G1 X{i} Y{i*2} Z-0.5 F500\n" for i in range(2000)]
        lines.insert(0, "G21\n")
        lines.insert(1, "G90\n")
        lines.insert(2, "G0 Z5\n")
        lines.append("M30\n")
        full_content = "".join(lines)
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        with patch.object(self.dashboard, "sync_gcode_to_machine") as mock_sync, \
             patch("PyQt6.QtWidgets.QFileDialog.getOpenFileName", return_value=(src_path, "")):
            self.dashboard.load_gcode(self.dashboard.default_gcode_editor)

        self.assertTrue(self.dashboard._is_large_file_preview)
        editor_text = self.dashboard.default_gcode_editor.toPlainText()
        self.assertIn("LARGE FILE PREVIEW MODE", editor_text)
        self.assertIn("intermediate CAM toolpath lines omitted", editor_text)
        self.assertIn("G21", editor_text)
        self.assertIn("M30", editor_text)
        self.assertEqual(self.dashboard.mock_coords['TOTAL_LINES'], len(lines))
        # Editor lines should be bounded (around ~1200 lines preview, not 2000+)
        self.assertLess(self.dashboard.default_gcode_editor.document().blockCount(), 1500)
        # sync_gcode_to_machine called with empty string content and file_path set
        mock_sync.assert_called_once_with("", file_path=self.dashboard.loaded_gcode_path)
        # Toolpath viewer should have parsed segments directly from disk
        self.assertGreater(len(self.dashboard.toolpath_viewer.segment_items), 0)

    def test_editor_modifications_guarded_in_preview_mode(self):
        self.dashboard._is_large_file_preview = True
        self.dashboard.loaded_gcode_path = os.path.join(self.nc_files_dir, "test.ngc")
        self.dashboard.default_gcode_editor.setPlainText("Preview line 1\nPreview line 2\n")

        with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            self.dashboard.save_gcode(self.dashboard.default_gcode_editor)
            mock_warn.assert_called_once()
            self.assertIn("Save Disabled", mock_warn.call_args[0][1])

        with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            self.dashboard.clean_gcode()
            mock_warn.assert_called_once()
            self.assertIn("Clean Disabled", mock_warn.call_args[0][1])

        with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            self.dashboard.fix_fusion_rapids()
            mock_warn.assert_called_once()
            self.assertIn("Optimizer Disabled", mock_warn.call_args[0][1])

        with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            self.dashboard.run_from_line(500)
            mock_warn.assert_called_once()
            self.assertIn("Run From Line", mock_warn.call_args[0][1])

    def test_clear_gcode_resets_preview_state(self):
        self.dashboard._is_large_file_preview = True
        self.dashboard.loaded_gcode_path = os.path.join(self.nc_files_dir, "active.ngc")
        self.dashboard.mock_coords['TOTAL_LINES'] = 50000
        self.dashboard.default_gcode_editor.setPlainText("Some preview")

        with patch.object(self.dashboard, "_send_b64_py_cmd"):
            self.dashboard.clear_gcode(self.dashboard.default_gcode_editor)

        self.assertFalse(self.dashboard._is_large_file_preview)
        self.assertIsNone(self.dashboard.loaded_gcode_path)
        self.assertEqual(self.dashboard.mock_coords['TOTAL_LINES'], 0)
        self.assertEqual(self.dashboard.default_gcode_editor.toPlainText(), "")

    def test_highlight_gcode_line_bounds_in_preview_mode(self):
        self.dashboard._is_large_file_preview = True
        self.dashboard.default_gcode_editor.setPlainText("Line 0\nLine 1\nLine 2\n")

        with patch.object(self.dashboard.toolpath_viewer, "highlight_line") as mock_tp_highlight:
            # Line 1000 is way beyond preview line count (3 lines)
            self.dashboard.highlight_gcode_line(1000)
            mock_tp_highlight.assert_called_once_with(1000)
            # Editor selections should remain empty or not crashed
            self.assertEqual(len(self.dashboard.default_gcode_editor.extraSelections()), 0)

            # Line 1 is within bounds
            self.dashboard.highlight_gcode_line(1)
            self.assertEqual(len(self.dashboard.default_gcode_editor.extraSelections()), 1)

    def test_start_cycle_local_with_loaded_gcode_path(self):
        self.dashboard.settings.local_only = True
        self.dashboard.local_api = MagicMock()
        self.dashboard.local_api.linuxcnc.MODE_AUTO = 2
        self.dashboard.local_api.linuxcnc.AUTO_RUN = 3
        mock_status = MagicMock()
        mock_status.task_mode = 2
        mock_status.file = ""
        self.dashboard.local_api.status.return_value = mock_status
        mock_command = MagicMock()
        self.dashboard.local_api.command.return_value = mock_command

        test_file = os.path.join(self.nc_files_dir, "large_loaded.ngc")
        with open(test_file, "w") as f:
            f.write("G0 X1\n")
        self.dashboard.loaded_gcode_path = test_file
        self.dashboard.default_gcode_editor.clear()  # Empty editor!

        self.dashboard.start_cycle()

        # Should open the loaded_gcode_path directly
        mock_command.program_open.assert_called_once_with(test_file)
        mock_command.auto.assert_called_once_with(3, 0)


if __name__ == "__main__":
    unittest.main()
