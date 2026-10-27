import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from tabs.probing_tab import ProbingTab, ProbeDiagramWidget, ProbeGridButton
from tabs.dashboard import MachineDashboard
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

class TestProbingTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = SettingsDouble({
            ("PROBING", "probe_diam"): "3.0",
            ("PROBING", "xy_clear"): "5.0",
            ("PROBING", "z_drop"): "5.0",
            ("PROBING", "search_dist"): "20.0",
            ("PROBING", "probe_vel"): "100.0",
        })
        self.tab = ProbingTab(self.settings)

    def tearDown(self):
        if hasattr(self, 'tab') and self.tab:
            self.tab.cleanup()

    def test_probing_tab_initialization(self):
        self.assertEqual(self.tab.active_cycle, "out_fl")
        self.assertEqual(self.tab.inp_probe_diam.text(), "3.0")
        self.assertEqual(self.tab.inp_xy_clear.text(), "5.0")
        self.assertEqual(self.tab.inp_z_drop.text(), "5.0")
        self.assertEqual(self.tab.inp_search_dist.text(), "20.0")

    def test_cycle_selection_updates_diagram_and_instructions(self):
        # Select Outside Boss
        self.tab.select_cycle("out_boss")
        self.assertEqual(self.tab.active_cycle, "out_boss")
        self.assertIn("Outside Boss", self.tab.lbl_cycle_title.text())
        self.assertEqual(self.tab.diagram.cycle_name, "out_boss")

        # Select Inside Hole
        self.tab.select_cycle("in_hole")
        self.assertEqual(self.tab.active_cycle, "in_hole")
        self.assertIn("Inside Hole", self.tab.lbl_cycle_title.text())
        self.assertEqual(self.tab.diagram.cycle_name, "in_hole")

        # Select Z surface
        self.tab.select_cycle("probe_z")
        self.assertEqual(self.tab.active_cycle, "probe_z")
        self.assertIn("Surface Z", self.tab.lbl_cycle_title.text())

    def test_outside_and_inside_3x3_grids(self):
        """Verify both 3x3 grids contain all 9 routines with ProbeGridButton instances."""
        outside_codes = ["out_bl", "out_b", "out_br", "out_l", "out_boss", "out_r", "out_fl", "out_f", "out_fr"]
        inside_codes = ["in_bl", "in_b", "in_br", "in_l", "in_hole", "in_r", "in_fl", "in_f", "in_fr"]

        # Ensure all 18 grid buttons exist in cycle_buttons
        for code in outside_codes + inside_codes:
            self.assertIn(code, self.tab.cycle_buttons)
            btn = self.tab.cycle_buttons[code]
            self.assertIsInstance(btn, ProbeGridButton)
            self.assertEqual(btn.cycle_code, code)

        # Verify active selection synchronization
        self.tab.select_cycle("out_boss")
        self.assertTrue(self.tab.cycle_buttons["out_boss"].is_selected)
        self.assertFalse(self.tab.cycle_buttons["in_hole"].is_selected)

        self.tab.select_cycle("in_hole")
        self.assertTrue(self.tab.cycle_buttons["in_hole"].is_selected)
        self.assertFalse(self.tab.cycle_buttons["out_boss"].is_selected)

    def test_probe_grid_button_paint_all_variants(self):
        """Verify offscreen rendering for all 18 outside and inside 3x3 grid button graphics."""
        all_codes = [
            ("out_bl", False), ("out_b", False), ("out_br", False),
            ("out_l", False), ("out_boss", False), ("out_r", False),
            ("out_fl", False), ("out_f", False), ("out_fr", False),
            ("in_bl", True), ("in_b", True), ("in_br", True),
            ("in_l", True), ("in_hole", True), ("in_r", True),
            ("in_fl", True), ("in_f", True), ("in_fr", True),
        ]
        for code, is_inside in all_codes:
            btn = ProbeGridButton(code, code, is_inside=is_inside, is_center="boss" in code or "hole" in code)
            btn.resize(90, 78)
            pix = btn.grab()
            self.assertFalse(pix.isNull())
            self.assertEqual(pix.width(), 90)
            self.assertEqual(pix.height(), 78)

            btn.set_selected(True)
            pix_sel = btn.grab()
            self.assertFalse(pix_sel.isNull())

    def test_probe_diagram_widget_rendering(self):
        diagram = ProbeDiagramWidget()
        diagram.resize(400, 300)
        for cycle in ("out_fl", "out_br", "out_boss", "in_hole", "in_fl", "probe_z", "angle_x"):
            diagram.set_cycle(cycle)
            pix = diagram.grab()
            self.assertFalse(pix.isNull())
            self.assertEqual(pix.width(), 400)
            self.assertEqual(pix.height(), 300)

    def test_manual_zero_axis_commands(self):
        dashboard = Mock()
        tab = ProbingTab(self.settings, dashboard=dashboard)
        
        tab.zero_axis("X")
        dashboard.execute_mdi.assert_called_with("G10 L20 P1 X0")

        tab.zero_axis("Y")
        dashboard.execute_mdi.assert_called_with("G10 L20 P1 Y0")

        tab.zero_axis("Z")
        dashboard.execute_mdi.assert_called_with("G10 L20 P1 Z0")

        tab.zero_axis("XY")
        dashboard.execute_mdi.assert_called_with("G10 L20 P1 X0 Y0")

        tab.zero_axis("ALL")
        dashboard.execute_mdi.assert_called_with("G10 L20 P1 X0 Y0 Z0")

    def test_start_probing_cycle_dispatches_to_dashboard(self):
        dashboard = Mock()
        dashboard.is_estopped = False
        dashboard.mock_coords = {"MACHINE_ON": True, "X": 10.0, "Y": 20.0, "Z": -5.0}
        
        tab = ProbingTab(self.settings, dashboard=dashboard)
        tab.select_cycle("out_fl")

        started_cycles = []
        completed_cycles = []
        tab.probing_started.connect(lambda c: started_cycles.append(c))
        tab.probing_completed.connect(lambda c, r: completed_cycles.append((c, r)))

        tab.start_probing_cycle()
        self.assertEqual(started_cycles, ["out_fl"])
        self.assertEqual(len(completed_cycles), 1)
        dashboard.trigger_probing.assert_called_with("out_fl")

    def test_start_probing_blocked_on_estop(self):
        dashboard = Mock()
        dashboard.is_estopped = True
        tab = ProbingTab(self.settings, dashboard=dashboard)

        with patch("tabs.probing_tab.QMessageBox.warning") as warn_mock:
            tab.start_probing_cycle()
            warn_mock.assert_called_once()
            dashboard.trigger_probing.assert_not_called()

    def test_probing_tab_position_in_app(self):
        """Verify Probing tab is positioned immediately to the right of Dashboard in LinuxCNCApp and preserved in operator mode."""
        from PyQt6.QtWidgets import QWidget, QTabWidget
        import inspect

        # 1. Verify source registration order in bootstrap.py
        source = inspect.getsource(LinuxCNCApp.__init__)
        dashboard_idx = source.find('(self.dashboard_tab, "🖥️ DASHBOARD")')
        probing_idx = source.find('(self.probing_tab, "📐 PROBING")')
        self.assertGreater(dashboard_idx, -1, "Dashboard tab must be present in all_tabs")
        self.assertGreater(probing_idx, -1, "Probing tab must be present in all_tabs")
        self.assertLess(dashboard_idx, probing_idx, "Dashboard tab must precede Probing tab")

        # 2. Verify apply_tab_visibility behavior
        app_window = LinuxCNCApp.__new__(LinuxCNCApp)
        app_window.tabs = QTabWidget()
        app_window.dashboard_tab = QWidget()
        app_window.probing_tab = QWidget()
        app_window.all_tabs = [
            (app_window.dashboard_tab, "🖥️ DASHBOARD"),
            (app_window.probing_tab, "📐 PROBING"),
            (QWidget(), "OTHER")
        ]

        # Normal mode: all tabs added
        app_window.op_mode = False
        app_window.apply_tab_visibility()
        self.assertEqual(app_window.tabs.count(), 3)
        self.assertEqual(app_window.tabs.tabText(0), "🖥️ DASHBOARD")
        self.assertEqual(app_window.tabs.tabText(1), "📐 PROBING")

        # Operator mode: both Dashboard and Probing remain visible
        app_window.op_mode = True
        app_window.apply_tab_visibility()
        self.assertEqual(app_window.tabs.count(), 2)
        self.assertEqual(app_window.tabs.tabText(0), "🖥️ DASHBOARD")
        self.assertEqual(app_window.tabs.tabText(1), "📐 PROBING")

    def test_boss_probing_routine_retracts_z_between_probes(self):
        """Verify out_boss routine lifts Z to starting height between all 4 boss probes."""
        from tabs.dashboard import MachineDashboard
        from settings_manager import SettingsManager

        s = SettingsManager()
        dash = MachineDashboard(s)
        dash.input_probe_diam.setText("6.0")
        dash.input_xy_clearance.setText("10.0")
        dash.input_z_drop.setText("5.0")
        dash.input_search_dist.setText("20.0")

        sent_code = []
        dash._send_b64_py_cmd = lambda code, timeout=120: sent_code.append(code)
        dash.trigger_probing("out_boss")

        self.assertEqual(len(sent_code), 1)
        code = sent_code[0]

        # Verify no double-brace escaped variable strings exist
        self.assertNotIn("{{start_z}}", code)
        self.assertNotIn("{{start_x}}", code)
        self.assertNotIn("{{start_y}}", code)
        self.assertNotIn("{{cx}}", code)

        # Verify each touch retracts to start_z before the next move
        self.assertIn('run_mdi(f"G53 G0 Z{start_z}")', code)
        z_retract_count = code.count('run_mdi(f"G53 G0 Z{start_z}")')
        # At least 4 probe touch retracts + premature trip abort handlers
        self.assertGreaterEqual(z_retract_count, 4)

    def test_inside_hole_probing_routine_retracts_z_between_probes(self):
        """Verify in_hole routine drops Z and lifts Z to starting height between all 4 hole touches."""
        from tabs.dashboard import MachineDashboard
        from settings_manager import SettingsManager

        s = SettingsManager()
        dash = MachineDashboard(s)
        dash.input_probe_diam.setText("6.0")
        dash.input_xy_clearance.setText("10.0")
        dash.input_z_drop.setText("5.0")
        dash.input_search_dist.setText("20.0")

        sent_code = []
        dash._send_b64_py_cmd = lambda code, timeout=120: sent_code.append(code)
        dash.trigger_probing("in_hole")

        self.assertEqual(len(sent_code), 1)
        code = sent_code[0]

        # Verify no double-brace escaped variable strings exist
        self.assertNotIn("{{start_z}}", code)
        self.assertNotIn("{{start_x}}", code)
        self.assertNotIn("{{start_y}}", code)
        self.assertNotIn("{{cx}}", code)
        self.assertNotIn("{{cy}}", code)

        # Verify Z drops into the hole
        self.assertIn('run_mdi(f"G38.3 Z-{zdrop} F{vel}")', code)
        self.assertEqual(code.count('run_mdi(f"G38.3 Z-{zdrop} F{vel}")'), 4)

        # Verify each touch retracts to start_z
        self.assertIn('run_mdi(f"G53 G0 Z{start_z}")', code)
        z_retract_count = code.count('run_mdi(f"G53 G0 Z{start_z}")')
        self.assertGreaterEqual(z_retract_count, 4)

        # Verify no relative search/4.0 backoff that causes overtravel in small holes
        self.assertNotIn('search/4.0', code)
        # Verify direct return to start_x and start_y in G53 machine coordinates
        self.assertIn('run_mdi(f"G53 G0 X{start_x}")', code)
        self.assertIn('run_mdi(f"G53 G0 Y{start_y}")', code)
        # Verify final move to Origin (X0 Y0) is present
        self.assertIn('run_mdi("G0 X0 Y0")', code)

    def test_inside_edge_and_corner_routines_retract_z(self):
        """Verify inside edges and corners drop Z, return directly to start XY, and do not overtravel."""
        from tabs.dashboard import MachineDashboard
        from settings_manager import SettingsManager

        s = SettingsManager()
        dash = MachineDashboard(s)
        dash.input_probe_diam.setText("6.0")
        dash.input_xy_clearance.setText("10.0")
        dash.input_z_drop.setText("5.0")
        dash.input_search_dist.setText("20.0")

        # Test single edge
        sent_code = []
        dash._send_b64_py_cmd = lambda code, timeout=120: sent_code.append(code)
        dash.trigger_probing("in_b")

        self.assertEqual(len(sent_code), 1)
        code = sent_code[0]
        self.assertNotIn("{{start_z}}", code)
        self.assertIn('run_mdi(f"G38.3 Z-{zdrop} F{vel}")', code)
        self.assertIn('run_mdi(f"G53 G0 Z{start_z}")', code)
        self.assertIn('run_mdi(f"G53 G0 X{start_x} Y{start_y}")', code)
        # Verify no relative search backoff
        self.assertNotIn('search/4.0', code)
        # Verify final move to Origin Y0
        self.assertIn('run_mdi("G0 Y0")', code)

        # Test corner (two touches: Y and X)
        sent_code.clear()
        dash.trigger_probing("in_bl")

        self.assertEqual(len(sent_code), 1)
        code_corner = sent_code[0]
        self.assertNotIn("{{start_z}}", code_corner)
        self.assertEqual(code_corner.count('run_mdi(f"G38.3 Z-{zdrop} F{vel}")'), 2)
        self.assertGreaterEqual(code_corner.count('run_mdi(f"G53 G0 Z{start_z}")'), 2)
        self.assertNotIn('search/4.0', code_corner)
        # Verify final move to Origin X0 Y0
        self.assertIn('run_mdi("G0 X0 Y0")', code_corner)


if __name__ == "__main__":
    unittest.main()



