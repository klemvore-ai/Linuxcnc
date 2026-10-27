import unittest
from unittest.mock import Mock

from core.linuxcnc_api import LocalLinuxCNCAPI


class LocalLinuxCNCAPITests(unittest.TestCase):
    def setUp(self):
        self.linuxcnc = Mock()
        self.hal = Mock()
        self.api = LocalLinuxCNCAPI(self.linuxcnc, self.hal)

    def test_exposes_linuxcnc_command_status_and_error_channels(self):
        self.assertIs(self.api.command(), self.linuxcnc.command.return_value)
        self.assertIs(self.api.status(), self.linuxcnc.stat.return_value)
        self.assertIs(self.api.error_channel(), self.linuxcnc.error_channel.return_value)

    def test_reads_and_writes_hal_through_native_module(self):
        self.hal.get_value.return_value = True

        self.assertTrue(self.api.get_hal_value("joint.2.neg-lim-sw-in"))
        self.api.set_hal_value("ui.coolant.out", True)

        self.hal.get_value.assert_called_once_with("joint.2.neg-lim-sw-in")
        self.hal.set_p.assert_called_once_with("ui.coolant.out", "True")

    def test_lists_native_hal_objects(self):
        pins = [{"NAME": "joint.2.neg-lim-sw-in", "VALUE": True}]
        params = [{"NAME": "motion.servo-thread.time", "VALUE": 0.0}]
        signals = [{"NAME": "j2-neg-limit", "VALUE": True}]
        self.hal.get_info_pins.return_value = pins
        self.hal.get_info_params.return_value = params
        self.hal.get_info_signals.return_value = signals

        self.assertIs(self.api.get_hal_pins(), pins)
        self.assertIs(self.api.get_hal_params(), params)
        self.assertIs(self.api.get_hal_signals(), signals)

    def test_executes_application_command_in_process(self):
        self.api.execute_application_command("linuxcnc.command().abort()")

        self.linuxcnc.command.return_value.abort.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()