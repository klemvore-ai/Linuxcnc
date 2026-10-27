import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from settings_manager import SettingsManager
from tabs.setup_wizard import AxisLimitsPage, FeedbackPage, HardwareNetworkPage, IOSafetyPage, MachineBasicsPage, ReviewPage, SetupWizard


class SettingsDouble:
    """Small settings implementation for isolated wizard page tests."""

    def __init__(self, values=None):
        self.values = values or {}

    def get_setting(self, section, key, default=None):
        return self.values.get((section, key), default)


class SetupWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_machine_basics_rejects_blank_name(self):
        page = MachineBasicsPage(SettingsDouble())
        page.name_input.clear()

        self.assertFalse(page.validatePage())

    def test_hardware_page_requires_isolation_acknowledgement(self):
        page = HardwareNetworkPage(SettingsDouble())

        self.assertFalse(page.validatePage())
        page.isolation_warning.setChecked(True)
        self.assertTrue(page.validatePage())

    def test_local_only_wizard_omits_agent_network_pages(self):
        settings = SettingsDouble()
        settings.local_only = True

        wizard = SetupWizard(settings)

        self.assertEqual(len(wizard.pageIds()), 6)
        self.assertIsNone(wizard.agent_page)
        self.assertIsNone(wizard.enrollment_page)

    def test_axis_page_rejects_reversed_limits(self):
        page = AxisLimitsPage(SettingsDouble())
        page.controls["X"]["min"].setValue(100)
        page.controls["X"]["max"].setValue(0)

        self.assertFalse(page.validatePage())

    def test_io_page_requires_estop_and_probe_signals_when_enabled(self):
        page = IOSafetyPage(SettingsDouble())

        self.assertFalse(page.validatePage())
        page.fields["estop_ext"].setText("hm2.input-00")
        self.assertFalse(page.validatePage())
        page.fields["probe_in"].setText("hm2.input-01")
        self.assertTrue(page.validatePage())

    def test_review_page_previews_standard_linuxcnc_sections(self):
        settings = SettingsDouble({
            ("MACHINE_CONFIG", "enable_estop"): "False",
            ("MACHINE_CONFIG", "enable_probe"): "False",
            ("AXIS_X", "min"): "0",
            ("AXIS_X", "max"): "100",
            ("AXIS_X", "vel"): "10",
            ("AXIS_X", "accel"): "50",
        })
        page = ReviewPage(settings)
        page.initializePage()

        self.assertIn("[ Generated INI ]", page.preview.toPlainText())
        self.assertIn("[EMC]", page.preview.toPlainText())
        self.assertIn("[ Generated HAL ]", page.preview.toPlainText())
        self.assertIn("Machine agent status:", page.agent_status.text())

    def test_feedback_page_requires_selected_feedback_signals(self):
        page = FeedbackPage(SettingsDouble())
        page.controls["X"]["mode"].setCurrentText("dual")

        self.assertFalse(page.validatePage())
        page.controls["X"]["motor"].setText("motor.encoder.position")
        self.assertFalse(page.validatePage())
        page.controls["X"]["axis"].setText("linear.scale.position")
        self.assertTrue(page.validatePage())

    def test_generate_base_configs_creates_files_and_uses_probe_plate_setting(self):
        from private.tabs.settings_tab import SettingsTab

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = SettingsManager(os.path.join(temp_dir, "settings.ini"), local_only=True)
            settings.save_setting("PROBING", "plate_thickness", "6.35")
            tab = SettingsTab(settings)
            original_cwd = os.getcwd()
            errors = []
            try:
                os.chdir(temp_dir)
                with patch("private.tabs.settings_tab.QDesktopServices.openUrl", return_value=True), patch(
                    "private.tabs.settings_tab.QMessageBox.question",
                    return_value=__import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.No,
                ), patch(
                    "private.tabs.settings_tab.QMessageBox.critical",
                    side_effect=lambda *args: errors.append(args[-1]),
                ):
                    tab.generate_base_configs()
            finally:
                os.chdir(original_cwd)

            output_dir = os.path.join(temp_dir, "exports", "LinuxCNC")
            self.assertEqual(errors, [])
            self.assertTrue(os.path.isfile(os.path.join(output_dir, "LinuxCNC.ini")))
            self.assertTrue(os.path.isfile(os.path.join(output_dir, "LinuxCNC.hal")))
            gui_launcher = Path(output_dir) / "linuxcnc_gui.py"
            self.assertTrue(os.access(gui_launcher, os.X_OK))
            launcher_source = gui_launcher.read_text(encoding="utf-8")
            self.assertIn(
                f"APPLICATION_ROOT = {str(Path(__file__).resolve().parents[1])!r}",
                launcher_source,
            )
            self.assertIn("import linuxcnc", launcher_source)
            self.assertIn("import hal", launcher_source)
            self.assertIn("runpy.run_path", launcher_source)
            self.assertNotIn("subprocess.run", launcher_source)
            with open(os.path.join(output_dir, "LinuxCNC.ini"), encoding="utf-8") as ini_file:
                self.assertIn("PLATE_THICKNESS = 6.3500", ini_file.read())

    def test_local_only_deployment_copies_into_linuxcnc_configs_without_ssh(self):
        from private.tabs.settings_tab import SettingsTab

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            source_dir = Path(temp_dir) / "generated"
            target_dir = home / "linuxcnc" / "configs" / "Router"
            source_dir.mkdir()
            (source_dir / "Router.ini").write_text("[EMC]\n", encoding="utf-8")
            (source_dir / "Router.hal").write_text("loadrt hostmot2\n", encoding="utf-8")
            (source_dir / "custom_postgui.hal").write_text("# generated\n", encoding="utf-8")
            target_dir.mkdir(parents=True)
            (target_dir / "custom_postgui.hal").write_text("# operator wiring\n", encoding="utf-8")
            settings = SettingsManager(Path(temp_dir) / "settings.ini", local_only=True)
            tab = SettingsTab(settings)

            with patch("private.tabs.settings_tab.Path.home", return_value=home), patch(
                "private.tabs.settings_tab.QMessageBox.information"
            ), patch("private.tabs.settings_tab.QMessageBox.critical") as error_dialog, patch(
                "private.tabs.settings_tab.subprocess.run"
            ) as subprocess_run:
                tab.deploy_configs(str(source_dir), "Router")

            self.assertEqual((target_dir / "Router.ini").read_text(encoding="utf-8"), "[EMC]\n")
            self.assertEqual((target_dir / "Router.hal").read_text(encoding="utf-8"), "loadrt hostmot2\n")
            self.assertEqual((target_dir / "custom_postgui.hal").read_text(encoding="utf-8"), "# operator wiring\n")
            error_dialog.assert_not_called()
            subprocess_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()