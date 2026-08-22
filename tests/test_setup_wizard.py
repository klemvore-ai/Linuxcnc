import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from tabs.setup_wizard import AxisLimitsPage, FeedbackPage, HardwareNetworkPage, IOSafetyPage, MachineBasicsPage, ReviewPage


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


if __name__ == "__main__":
    unittest.main()