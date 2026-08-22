"""Render validated machine models as standard LinuxCNC configuration files."""

from core.config_model import MachineConfig


class ConfigurationRenderError(ValueError):
    """Raised when a machine model cannot be rendered safely."""


class LinuxCNCConfigRenderer:
    """Generate standard INI and HAL text without requiring LinuxCNC."""

    def render_ini(self, config: MachineConfig):
        """Render the machine model as a conventional LinuxCNC INI file."""
        self._validate(config)
        coordinates = " ".join(axis.name.upper() for axis in config.axes)
        lines = [
            "[EMC]",
            f"MACHINE = {config.name}",
            "DEBUG = 0",
            "VERSION = 1.1",
            "",
            "[DISPLAY]",
            f"DISPLAY = {config.display}",
            "POSITION_OFFSET = RELATIVE",
            "POSITION_FEEDBACK = ACTUAL",
            f"MAX_FEED_OVERRIDE = {config.max_feed_override:.6f}",
            f"MAX_SPINDLE_OVERRIDE = {config.max_spindle_override:.6f}",
            f"MIN_SPINDLE_OVERRIDE = {config.min_spindle_override:.6f}",
            f"DEFAULT_SPINDLE_0_SPEED = {config.spindle.maximum_rpm * 0.5:.0f}",
            f"MIN_SPINDLE_0_SPEED = {config.spindle.minimum_rpm:.0f}",
            f"MAX_SPINDLE_0_SPEED = {config.spindle.maximum_rpm:.0f}",
            "PROGRAM_PREFIX = ~/linuxcnc/nc_files",
            "",
            "[KINS]",
            f"JOINTS = {len(config.joints)}",
            f"KINEMATICS = {config.kinematics}",
            "",
            "[TRAJ]",
            f"COORDINATES = {coordinates}",
            f"LINEAR_UNITS = {config.units}",
            "ANGULAR_UNITS = degree",
            "",
            "[HAL]",
            f"HALFILE = {config.name}.hal",
            f"POSTGUI_HALFILE = custom_postgui.hal",
            "",
            "[RS274NGC]",
            "PARAMETER_FILE = linuxcnc.var",
            f"RS274NGC_STARTUP_CODE = {'G21' if config.units == 'mm' else 'G20'} G40 G90 G94 G97 G64 P0.025",
            "",
            "[TASK]",
            "TASK = milltask",
            "CYCLE_TIME = 0.010",
            "",
            "[EMCIO]",
            "EMCIO = io",
            "CYCLE_TIME = 0.100",
            "TOOL_TABLE = tool.tbl",
            "",
            "[EMCMOT]",
            "EMCMOT = motmod",
            f"SERVO_PERIOD = {config.servo_period_ns}",
            "",
        ]
        for index, axis in enumerate(config.axes):
            lines.extend([
                f"[AXIS_{axis.name.upper()}]",
                f"MAX_VELOCITY = {axis.velocity:.6f}",
                f"MAX_ACCELERATION = {axis.acceleration:.6f}",
                f"MIN_LIMIT = {axis.minimum:.6f}",
                f"MAX_LIMIT = {axis.maximum:.6f}",
                "",
            ])
        for index, joint in enumerate(config.joints):
            axis = next(item for item in config.axes if item.name.upper() == joint.axis.upper())
            lines.extend([
                f"[JOINT_{index}]",
                f"TYPE = {'ANGULAR' if axis.name.upper() in {'A', 'B', 'C'} else 'LINEAR'}",
                f"HOME = {joint.home:.6f}",
                f"MIN_LIMIT = {axis.minimum:.6f}",
                f"MAX_LIMIT = {axis.maximum:.6f}",
                f"MAX_VELOCITY = {axis.velocity:.6f}",
                f"MAX_ACCELERATION = {axis.acceleration:.6f}",
                f"STEPGEN_MAXVEL = {axis.velocity * 1.2:.6f}",
                f"STEPGEN_MAXACCEL = {axis.acceleration * 1.25:.6f}",
                f"SCALE = {joint.scale:.6f}",
                f"HOME_SEQUENCE = {joint.home_sequence}",
                "",
            ])
        lines.extend([
            "[SPINDLE_0]",
            "P = 0.0000",
            "I = 0.0000",
            "D = 0.0000",
            "FF0 = 1.0000",
            "FF1 = 0.0000",
            f"MAX_OUTPUT = {config.spindle.maximum_rpm:.0f}",
            f"OUTPUT_SCALE = {config.spindle.maximum_rpm:.0f}",
            "OUTPUT_MIN_LIMIT = 0",
            f"OUTPUT_MAX_LIMIT = {config.spindle.maximum_rpm:.0f}",
            "",
        ])
        return "\n".join(lines)

    def render_hal(self, config: MachineConfig):
        """Render backend-specific HAL for the supported Mesa backend."""
        self._validate(config)
        if config.hardware.backend.lower() != "mesa":
            raise ConfigurationRenderError("EtherCAT HAL rendering is not implemented yet.")
        board = config.hardware.board_model.lower().replace("eu", "e")
        joints = len(config.joints)
        lines = [
            f"# CNCFusion HAL configuration for {config.name}",
            "loadrt [KINS]KINEMATICS",
            f"loadrt [EMCMOT]EMCMOT servo_period_nsec=[EMCMOT]SERVO_PERIOD num_joints={joints}",
            "loadrt hostmot2",
            f"loadrt hm2_eth board_ip={config.hardware.address} config=\"num_encoders=1 num_pwmgens=0 num_stepgens={joints} sserial_port_0=2xxxx\"",
            f"addf hm2_{board}.0.read servo-thread",
            "addf motion-command-handler servo-thread",
            "addf motion-controller servo-thread",
        ]
        if config.pendant.phase_a_signal and config.pendant.phase_b_signal:
            lines.insert(4, "loadrt encoder num_chan=1")
            lines.extend([
                "addf encoder.update-counters servo-thread",
                "addf encoder.capture-position servo-thread",
                f"net mpg-phase-a {config.pendant.phase_a_signal} => encoder.0.phase-A",
                f"net mpg-phase-b {config.pendant.phase_b_signal} => encoder.0.phase-B",
            ])
            if config.pendant.count_signal:
                lines.append(
                    "net mpg-count encoder.0.count => "
                    + " ".join(f"axis.{axis.name.lower()}.jog-counts" for axis in config.axes)
                )
        configured_selectors = [
            (index, signal)
            for index, signal in config.pendant.increment_signals.items()
            if signal
        ]
        if configured_selectors:
            lines.insert(5, "loadrt mux16 names=jogincr")
            lines.append("addf jogincr servo-thread")
            lines.extend([
                f"setp jogincr.debounce-time {config.pendant.debounce_time:.6f}",
                f"setp jogincr.use-graycode {str(config.pendant.use_graycode)}",
                f"setp jogincr.suppress-no-input {str(config.pendant.suppress_no_input)}",
            ])
            for index, signal in configured_selectors:
                lines.append(f"net jog-incr-{index} {signal} => jogincr.sel{index}")
            for index, value in enumerate(config.pendant.increments[:16]):
                lines.append(f"setp jogincr.in{index:02d} {value:.6f}")
            if config.pendant.increments:
                lines.append(
                    "net selected-jog-incr jogincr.out-f => "
                    + " ".join(f"axis.{axis.name.lower()}.jog-scale" for axis in config.axes)
                )
        for axis, signal in config.pendant.axis_enable_signals.items():
            if signal:
                lines.append(f"net mpg-{axis.lower()}-enable {signal} => axis.{axis.lower()}.jog-enable")
        for index, joint in enumerate(config.joints):
            stepgen = f"hm2_{board}.0.stepgen.{index:02d}"
            lines.extend([
                f"setp {stepgen}.dirsetup [JOINT_{index}]DIRSETUP",
                f"setp {stepgen}.dirhold [JOINT_{index}]DIRHOLD",
                f"setp {stepgen}.steplen [JOINT_{index}]STEPLEN",
                f"setp {stepgen}.stepspace [JOINT_{index}]STEPSPACE",
                f"setp {stepgen}.position-scale [JOINT_{index}]SCALE",
                f"setp {stepgen}.maxvel [JOINT_{index}]STEPGEN_MAXVEL",
                f"setp {stepgen}.maxaccel [JOINT_{index}]STEPGEN_MAXACCEL",
                f"setp {stepgen}.control-type 0",
                f"net j{index}-pos-cmd joint.{index}.motor-pos-cmd => {stepgen}.position-cmd",
                f"net j{index}-pos-fb {stepgen}.position-fb => joint.{index}.motor-pos-fb",
                f"net j{index}-enable joint.{index}.amp-enable-out => {stepgen}.enable",
            ])
        if config.spindle.closed_loop:
            lines.append("loadrt pid names=pid.s")
        lines.append(f"addf hm2_{board}.0.write servo-thread")
        lines.extend([
            f"setp hm2_{board}.0.7i76.0.0.spinout-minlim {config.spindle.minimum_rpm:.0f}",
            f"setp hm2_{board}.0.7i76.0.0.spinout-maxlim {config.spindle.maximum_rpm:.0f}",
            f"setp hm2_{board}.0.7i76.0.0.spinout-scalemax {config.spindle.maximum_rpm:.0f}",
            "net spindle-enable spindle.0.on => hm2_%s.0.7i76.0.0.spinena" % board,
            "net spindle-dir spindle.0.reverse => hm2_%s.0.7i76.0.0.spindir" % board,
            "net spindle-cmd spindle.0.speed-out-abs => hm2_%s.0.7i76.0.0.spinout" % board,
        ])
        if config.spindle.closed_loop:
            lines.extend([
                "addf pid.s.do-pid-calcs servo-thread",
            ])
        if config.safety.enable_external_estop:
            lines.extend([
                "loadrt and2 count=1",
                "addf and2.0 servo-thread",
                "net estop-user-out iocontrol.0.user-enable-out => and2.0.in0",
                f"net estop-ext {config.safety.external_estop_signal} => and2.0.in1",
                "net estop-safe-in and2.0.out => iocontrol.0.emc-enable-in",
            ])
        if config.probing.enabled and config.probing.probe_signal:
            lines.extend([
                "loadrt lut5 count=1",
                "addf lut5.0 servo-thread",
                "setp lut5.0.function 0xCA",
                f"net probe-tip {config.probing.probe_signal} => lut5.0.in-1",
                "net combined-probe lut5.0.out => motion.probe-input",
            ])
            if config.probing.plate_signal:
                lines.append(f"net plate-setter {config.probing.plate_signal} => lut5.0.in-0")
            else:
                lines.append("setp lut5.0.in-0 False")
        if config.tool_change_loopback:
            lines.extend([
                "net tool-change-loop iocontrol.0.tool-change => iocontrol.0.tool-changed",
                "net tool-prepare-loop iocontrol.0.tool-prepare => iocontrol.0.tool-prepared",
            ])
        for mapping in config.io_mappings:
            if mapping.role == "iocontrol.0.emc-enable-in":
                continue
            signal = mapping.signal
            if mapping.inverted and not signal.endswith("-not"):
                signal = f"{signal}-not"
            signal_name = mapping.role.replace(".", "-")
            if mapping.direction == "output":
                lines.append(f"net {signal_name} {mapping.role} => {signal}")
            else:
                lines.append(f"net {signal_name} {signal} => {mapping.role}")
        lines.append("")
        return "\n".join(lines)

    def _validate(self, config):
        """Reject invalid models before generating machine configuration text."""
        errors = config.validate()
        if errors:
            raise ConfigurationRenderError(" ".join(errors))