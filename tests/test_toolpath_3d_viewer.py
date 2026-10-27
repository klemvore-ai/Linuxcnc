import os
import unittest
import math
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from tabs.dashboard import ToolpathViewer

class TestToolpath3DViewer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.viewer = ToolpathViewer()
        self.viewer.resize(800, 600)

    def test_camera_basis_views(self):
        # 1. Top View (XY)
        self.viewer.set_view_top()
        self.assertEqual(self.viewer.azimuth, 0.0)
        self.assertEqual(self.viewer.elevation, 90.0)
        r, u = self.viewer.get_camera_basis()
        self.assertAlmostEqual(r[0], 1.0)
        self.assertAlmostEqual(r[1], 0.0)
        self.assertAlmostEqual(u[1], 1.0)

        # 2. Front View (XZ)
        self.viewer.set_view_front()
        self.assertEqual(self.viewer.azimuth, 0.0)
        self.assertEqual(self.viewer.elevation, 0.0)
        r, u = self.viewer.get_camera_basis()
        self.assertAlmostEqual(r[0], 1.0)
        self.assertAlmostEqual(u[2], 1.0)

        # 3. Side View (YZ)
        self.viewer.set_view_side()
        self.assertEqual(self.viewer.azimuth, 90.0)
        self.assertEqual(self.viewer.elevation, 0.0)
        r, u = self.viewer.get_camera_basis()
        self.assertAlmostEqual(r[1], 1.0)
        self.assertAlmostEqual(u[2], 1.0)

        # 4. Isometric View
        self.viewer.set_view_iso()
        self.assertEqual(self.viewer.azimuth, -45.0)
        self.assertEqual(self.viewer.elevation, 30.0)

    def test_gcode_parsing_and_arc_interpolation(self):
        gcode = (
            "G21 G90\n"
            "G0 X0 Y0 Z5\n"
            "G1 Z-1 F200\n"
            "G1 X50 Y0\n"
            "G3 X100 Y50 I0 J50\n"  # Arc CCW
            "G2 X150 Y0 R50\n"      # Arc CW with R
            "G0 Z10\n"
        )
        self.viewer.update_path(gcode)
        self.assertGreater(len(self.viewer.segments), 5)
        self.assertIn(1, self.viewer.segment_items)
        self.assertTrue(self.viewer.segment_items[1]['is_rapid'])
        self.assertIn(2, self.viewer.segment_items)
        self.assertFalse(self.viewer.segment_items[2]['is_rapid'])
        self.assertGreaterEqual(len(self.viewer.scene.items()), 1)

    def test_tool_position_and_units_scaling(self):
        self.viewer.display_units = "mm"
        self.viewer.update_tool_position(100.0, 200.0, -5.0)
        self.assertEqual(self.viewer.tool_pos, (100.0, 200.0, -5.0))

        self.viewer.display_units = "inch"
        self.viewer.update_tool_position(1.0, 2.0, 0.5)
        self.assertAlmostEqual(self.viewer.tool_pos[0], 25.4)
        self.assertAlmostEqual(self.viewer.tool_pos[1], 50.8)
        self.assertAlmostEqual(self.viewer.tool_pos[2], 12.7)

    def test_offscreen_grab_rendering(self):
        self.viewer.update_path("G0 X10 Y10 Z5\nG1 Z-2\nG1 X80 Y80\n")
        pix = self.viewer.grab()
        self.assertFalse(pix.isNull())
        self.assertEqual(pix.width(), 800)
        self.assertEqual(pix.height(), 600)

    def test_highlight_line(self):
        self.viewer.update_path("G0 X0 Y0\nG1 X100 Y100\n")
        self.viewer.highlight_line(1)
        self.assertEqual(self.viewer.active_line, 1)

    def test_machine_limits_configuration(self):
        self.viewer.set_machine_limits(0.0, 1000.0, 0.0, 1000.0, -100.0, 0.0)
        self.assertEqual(self.viewer.machine_limits['max_x'], 1000.0)
        self.assertEqual(self.viewer.machine_limits['min_z'], -100.0)

    def test_preview_disable_enable(self):
        self.viewer.set_preview_enabled(False)
        self.assertFalse(self.viewer.preview_enabled)
        self.assertEqual(len(self.viewer.segments), 0)

        self.viewer.set_preview_enabled(True, gcode_text="G1 X10 Y10\n")
        self.assertTrue(self.viewer.preview_enabled)
        self.assertEqual(len(self.viewer.segments), 1)

if __name__ == '__main__':
    unittest.main()
