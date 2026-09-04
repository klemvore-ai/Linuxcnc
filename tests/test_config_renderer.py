import unittest

from core.config_model import AxisConfig, HardwareConfig, IOMapping, JointConfig, MachineConfig, ProbingConfig, SafetyConfig
from core.config_renderer import ConfigurationRenderError, LinuxCNCConfigRenderer


def sample_config(backend="mesa"):
    """Create a small valid machine model for renderer tests."""
    return MachineConfig(
        name="TestMill",
        hardware=HardwareConfig(backend=backend),
        safety=SafetyConfig(enable_external_estop=False),
        axes=[
            AxisConfig("X", 0.0, 100.0, 20.0, 100.0),
            AxisConfig("Y", 0.0, 100.0, 20.0, 100.0),
            AxisConfig("Z", -50.0, 0.0, 10.0, 50.0),
        ],
        joints=[JointConfig("X"), JointConfig("Y"), JointConfig("Z")],
    )


class ConfigurationRendererTests(unittest.TestCase):
    def test_existing_settings_convert_to_machine_model(self):
        class SettingsDouble:
            values = {
                ("MACHINE_CONFIG", "name"): "Shop Mill",
                ("MACHINE_CONFIG", "units"): "Metric (mm)",
                ("MACHINE_CONFIG", "kinematics"): "XYYZ (Gantry 3-Axis)",
                ("MESA", "board_model"): "7i76eu",
                ("MESA", "ip_address"): "10.10.10.20",
                ("AXIS_X", "min"): "0",
                ("AXIS_X", "max"): "1000",
                ("AXIS_X", "vel"): "75",
                ("AXIS_X", "accel"): "600",
                ("AXIS_Y", "min"): "0",
                ("AXIS_Y", "max"): "1000",
                ("AXIS_Y", "vel"): "75",
                ("AXIS_Y", "accel"): "600",
                ("AXIS_Z", "min"): "-125",
                ("AXIS_Z", "max"): "0",
                ("AXIS_Z", "vel"): "30",
                ("AXIS_Z", "accel"): "150",
                ("IO_MAPPING", "joint_0_home"): "hm2_7i76e.0.7i76.0.0.input-00",
                ("IO_MAPPING", "joint_0_home_inv"): "True",
                ("IO_MAPPING", "estop_ext"): "hm2_7i76e.0.7i76.0.0.input-21",
                ("IO_MAPPING", "probe_in"): "hm2_7i76e.0.7i76.0.0.input-03",
            }

            def get_setting(self, section, key, default=None):
                return self.values.get((section, key), default)

        config = MachineConfig.from_settings(SettingsDouble())

        self.assertEqual(config.name, "Shop Mill")
        self.assertEqual(config.units, "mm")
        self.assertEqual(config.hardware.address, "10.10.10.20")
        self.assertEqual([joint.axis for joint in config.joints], ["X", "Y", "Y", "Z"])
        home_mapping = next(item for item in config.io_mappings if item.role == "joint.0.home-sw-in")
        self.assertTrue(home_mapping.inverted)
        self.assertEqual(config.validate(), [])

    def test_existing_feedback_settings_convert_to_axis_feedback(self):
        class SettingsDouble:
            values = {
                ("MACHINE_CONFIG", "enable_estop"): "False",
                ("MACHINE_CONFIG", "enable_probe"): "False",
                ("FEEDBACK", "x_mode"): "dual",
                ("FEEDBACK", "x_motor_signal"): "motor.encoder.position",
                ("FEEDBACK", "x_axis_signal"): "linear.scale.position",
            }

            def get_setting(self, section, key, default=None):
                return self.values.get((section, key), default)

        config = MachineConfig.from_settings(SettingsDouble())
        feedback = next(item for item in config.axis_feedback if item.axis == "X")

        self.assertEqual(feedback.mode, "dual")
        self.assertEqual(feedback.motor_signal, "motor.encoder.position")
        self.assertEqual(feedback.axis_signal, "linear.scale.position")

    def test_render_ini_contains_standard_linuxcnc_sections(self):
        output = LinuxCNCConfigRenderer().render_ini(sample_config())

        self.assertIn("[EMC]", output)
        self.assertIn("[KINS]", output)
        self.assertIn("JOINTS = 3", output)
        self.assertIn("COORDINATES = X Y Z", output)
        self.assertIn("[JOINT_2]", output)
        self.assertIn("STEPGEN_MAXVEL = 12.000000", output)
        self.assertIn("STEPGEN_MAXACCEL = 62.500000", output)
        self.assertIn("[DISPLAY]", output)
        self.assertIn("[RS274NGC]", output)
        self.assertIn("[EMCIO]", output)
        self.assertIn("[SPINDLE_0]", output)
        self.assertIn("HALFILE = TestMill.hal", output)

    def test_render_hal_contains_mesa_backend(self):
        output = LinuxCNCConfigRenderer().render_hal(sample_config())

        self.assertIn("loadrt hostmot2", output)
        self.assertIn("loadrt hm2_eth board_ip=10.10.10.10", output)
        self.assertIn("num_stepgens=3", output)
        self.assertIn("joint.0.motor-pos-cmd", output)
        self.assertIn("stepgen.00.position-cmd", output)
        self.assertIn("stepgen.02.enable", output)

    def test_mesa_eu_board_name_matches_linuxcnc_component_name(self):
        config = sample_config()
        config.hardware.board_model = "7i76eu"

        output = LinuxCNCConfigRenderer().render_hal(config)

        self.assertIn("hm2_7i76e.0.read", output)

    def test_hal_renders_explicit_io_mapping_and_inversion(self):
        config = sample_config()
        config.io_mappings = [
            IOMapping("joint.0.home-sw-in", "hm2_7i76e.0.input-00", True),
        ]

        output = LinuxCNCConfigRenderer().render_hal(config)

        self.assertIn(
            "net joint-0-home-sw-in hm2_7i76e.0.input-00-not => joint.0.home-sw-in",
            output,
        )

    def test_hal_renders_spindle_and_directional_coolant_output(self):
        config = sample_config()
        config.io_mappings = [
            IOMapping("iocontrol.0.coolant-flood", "hm2_7i76e.0.output-01", direction="output"),
        ]

        output = LinuxCNCConfigRenderer().render_hal(config)

        self.assertIn("spinout-scalemax 24000", output)
        self.assertIn("spindle.0.speed-out-abs", output)
        self.assertIn("net iocontrol-0-coolant-flood iocontrol.0.coolant-flood => hm2_7i76e.0.output-01", output)

    def test_hal_renders_complete_mpg_encoder(self):
        config = sample_config()
        config.pendant.phase_a_signal = "hm2_7i76e.0.input-10"
        config.pendant.phase_b_signal = "hm2_7i76e.0.input-11"
        config.pendant.count_signal = "hm2_7i76e.0.enc0.count"
        config.pendant.axis_enable_signals = {"X": "hm2_7i76e.0.input-12"}
        config.pendant.increment_signals = {0: "hm2_7i76e.0.input-13"}
        config.pendant.increments = [0.001, 0.01]

        output = LinuxCNCConfigRenderer().render_hal(config)

        self.assertIn("loadrt encoder num_chan=1", output)
        self.assertIn("net mpg-phase-a hm2_7i76e.0.input-10 => encoder.0.phase-A", output)
        self.assertIn("net mpg-count encoder.0.count => axis.x.jog-counts", output)
        self.assertIn("loadrt mux16 names=jogincr", output)
        self.assertIn("setp jogincr.in01 0.010000", output)
        self.assertIn("axis.x.jog-enable", output)

    def test_incomplete_mpg_phases_are_rejected(self):
        config = sample_config()
        config.pendant.phase_a_signal = "hm2_7i76e.0.input-10"

        with self.assertRaises(ConfigurationRenderError):
            LinuxCNCConfigRenderer().render_hal(config)

    def test_hal_renders_external_estop_interlock(self):
        config = sample_config()
        config.safety = SafetyConfig(
            external_estop_signal="hm2_7i76e.0.input-21",
            enable_external_estop=True,
        )

        output = LinuxCNCConfigRenderer().render_hal(config)

        self.assertIn("loadrt and2 count=1", output)
        self.assertIn("iocontrol.0.user-enable-out => and2.0.in0", output)
        self.assertIn("hm2_7i76e.0.input-21 => and2.0.in1", output)
        self.assertIn("and2.0.out => iocontrol.0.emc-enable-in", output)

    def test_enabled_external_estop_without_signal_is_rejected(self):
        config = sample_config()
        config.safety = SafetyConfig(enable_external_estop=True)

        with self.assertRaises(ConfigurationRenderError):
            LinuxCNCConfigRenderer().render_hal(config)

    def test_hal_renders_combined_probe_and_touch_plate(self):
        config = sample_config()
        config.probing = ProbingConfig(
            probe_signal="hm2_7i76e.0.input-03",
            plate_signal="hm2_7i76e.0.input-04",
            enabled=True,
        )

        output = LinuxCNCConfigRenderer().render_hal(config)

        self.assertIn("loadrt lut5 count=1", output)
        self.assertIn("net probe-tip hm2_7i76e.0.input-03 => lut5.0.in-1", output)
        self.assertIn("net plate-setter hm2_7i76e.0.input-04 => lut5.0.in-0", output)
        self.assertIn("net combined-probe lut5.0.out => motion.probe-input", output)

    def test_enabled_probing_requires_probe_signal(self):
        config = sample_config()
        config.probing = ProbingConfig(enabled=True)

        with self.assertRaises(ConfigurationRenderError):
            LinuxCNCConfigRenderer().render_hal(config)

    def test_hal_renders_hardware_buttons_and_tool_change_loopback(self):
        config = sample_config()
        config.tool_change_loopback = True
        config.io_mappings = [
            IOMapping("halui.program.run", "hm2_7i76e.0.input-22"),
            IOMapping("halui.program.stop", "hm2_7i76e.0.input-23"),
            IOMapping("halui.program.pause", "hm2_7i76e.0.input-24"),
            IOMapping("halui.program.resume", "hm2_7i76e.0.input-25"),
            IOMapping("motion.feed-hold", "hm2_7i76e.0.input-27"),
        ]

        output = LinuxCNCConfigRenderer().render_hal(config)

        self.assertIn("net halui-program-run hm2_7i76e.0.input-22 => halui.program.run", output)
        self.assertIn("net motion-feed-hold hm2_7i76e.0.input-27 => motion.feed-hold", output)
        self.assertIn("net tool-change-loop iocontrol.0.tool-change => iocontrol.0.tool-changed", output)
        self.assertIn("net tool-prepare-loop iocontrol.0.tool-prepare => iocontrol.0.tool-prepared", output)

    def test_tool_prepare_is_always_auto_acknowledged_even_without_the_change_loopback(self):
        config = sample_config()
        config.tool_change_loopback = False

        output = LinuxCNCConfigRenderer().render_hal(config)

        self.assertIn("net tool-prepare-loop iocontrol.0.tool-prepare => iocontrol.0.tool-prepared", output)
        self.assertNotIn("net tool-change-loop iocontrol.0.tool-change => iocontrol.0.tool-changed", output)

    def test_invalid_model_is_rejected_before_rendering(self):
        config = sample_config()
        config.axes[0].maximum = config.axes[0].minimum

        with self.assertRaises(ConfigurationRenderError):
            LinuxCNCConfigRenderer().render_ini(config)

    def test_ethercat_model_is_not_misrepresented_as_mesa(self):
        with self.assertRaises(ConfigurationRenderError):
            LinuxCNCConfigRenderer().render_hal(sample_config("ethercat"))


if __name__ == "__main__":
    unittest.main()