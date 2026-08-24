"""Hardware-neutral machine configuration objects for LinuxCNC generation."""

from dataclasses import dataclass, field


@dataclass
class AxisConfig:
    """Describe one coordinate axis and its physical operating limits."""

    name: str
    minimum: float
    maximum: float
    velocity: float
    acceleration: float


@dataclass
class JointConfig:
    """Describe one physical joint mapped to an axis or kinematic role."""

    axis: str
    home: float = 0.0
    home_sequence: int = 0
    scale: float = 1.0
    dirsetup: int = 200
    dirhold: int = 200
    steplen: int = 1000
    stepspace: int = 1000


@dataclass
class FeedbackConfig:
    """Describe feedback by semantic role instead of a hardware address."""

    role: str
    signal: str
    units: str = "mm"


@dataclass
class AxisFeedbackConfig:
    """Describe motor-side and axis-side feedback for one coordinate axis."""

    axis: str
    mode: str = "none"
    motor_signal: str = ""
    axis_signal: str = ""


@dataclass
class IOMapping:
    """Map a semantic LinuxCNC input target to a physical signal."""

    role: str
    signal: str
    inverted: bool = False
    direction: str = "input"


@dataclass
class HardwareConfig:
    """Describe the real-time hardware backend and its device identity."""

    backend: str = "mesa"
    board_model: str = "7i76eu"
    address: str = "10.10.10.10"


@dataclass
class SpindleConfig:
    """Describe spindle limits and optional closed-loop PID behavior."""

    minimum_rpm: float = 100.0
    maximum_rpm: float = 24000.0
    closed_loop: bool = False


@dataclass
class PendantConfig:
    """Describe optional MPG feedback and jog-selection controls."""

    count_signal: str = ""
    phase_a_signal: str = ""
    phase_b_signal: str = ""
    axis_enable_signals: dict[str, str] = field(default_factory=dict)
    increment_signals: dict[int, str] = field(default_factory=dict)
    increments: list[float] = field(default_factory=list)
    debounce_time: float = 0.2
    use_graycode: bool = False
    suppress_no_input: bool = False


@dataclass
class SafetyConfig:
    """Describe safety interlocks that must remain active for machine enable."""

    external_estop_signal: str = ""
    enable_external_estop: bool = True


@dataclass
class ProbingConfig:
    """Describe probe, touch-plate, and tool-setter input signals."""

    probe_signal: str = ""
    plate_signal: str = ""
    setter_overtravel_signal: str = ""
    enabled: bool = False


@dataclass
class MachineConfig:
    """Complete semantic machine definition consumed by LinuxCNC renderers."""

    name: str = "CNCFusion"
    units: str = "mm"
    kinematics: str = "trivkins coordinates=xyz"
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    axes: list[AxisConfig] = field(default_factory=list)
    joints: list[JointConfig] = field(default_factory=list)
    feedback: list[FeedbackConfig] = field(default_factory=list)
    axis_feedback: list[AxisFeedbackConfig] = field(default_factory=list)
    io_mappings: list[IOMapping] = field(default_factory=list)
    servo_period_ns: int = 1000000
    display: str = "./cncfusion_gui.py"
    max_feed_override: float = 2.0
    max_spindle_override: float = 1.2
    min_spindle_override: float = 0.5
    spindle_min_rpm: float = 100.0
    spindle_max_rpm: float = 24000.0
    spindle: SpindleConfig = field(default_factory=SpindleConfig)
    pendant: PendantConfig = field(default_factory=PendantConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    probing: ProbingConfig = field(default_factory=ProbingConfig)
    tool_change_loopback: bool = False

    @classmethod
    def from_settings(cls, settings):
        """Build a machine model from the existing INI settings manager."""
        name = settings.get_setting("MACHINE_CONFIG", "name", "CNCFusion")
        units_text = settings.get_setting("MACHINE_CONFIG", "units", "Metric (mm)")
        units = "mm" if "mm" in units_text.lower() or "metric" in units_text.lower() else "inch"
        kinematics_text = settings.get_setting("MACHINE_CONFIG", "kinematics", "XYZ (3-Axis)")
        kinematics_map = {
            "XYZ (3-Axis)": "trivkins coordinates=xyz",
            "XYYZ (Gantry 3-Axis)": "trivkins coordinates=xyyz kinstype=BOTH",
            "XYZ A (4-Axis)": "trivkins coordinates=xyza",
            "XYZ AB (5-Axis)": "trivkins coordinates=xyzab",
        }
        axis_names = {
            "XYZ (3-Axis)": ["X", "Y", "Z"],
            "XYYZ (Gantry 3-Axis)": ["X", "Y", "Z"],
            "XYZ A (4-Axis)": ["X", "Y", "Z", "A"],
            "XYZ AB (5-Axis)": ["X", "Y", "Z", "A", "B"],
        }.get(kinematics_text, ["X", "Y", "Z"])
        axes = []
        for axis_name in axis_names:
            section = f"AXIS_{axis_name}"
            axes.append(AxisConfig(
                axis_name,
                float(settings.get_setting(section, "min", 0.0)),
                float(settings.get_setting(section, "max", 100.0)),
                float(settings.get_setting(section, "vel", 10.0)),
                float(settings.get_setting(section, "accel", 50.0)),
            ))
        joint_names = ["X", "Y", "Y", "Z"] if kinematics_text == "XYYZ (Gantry 3-Axis)" else axis_names
        joints = []
        for axis in joint_names:
            section = f"AXIS_{axis}"
            units_per_rev = float(settings.get_setting(section, "units", 1.0))
            steps = float(settings.get_setting(section, "steps", 1.0))
            scale = steps / max(0.001, units_per_rev)
            if settings.get_setting(section, "invert_dir", "False") == "True":
                scale = -scale
            joints.append(JointConfig(
                axis,
                home=float(settings.get_setting("HOMING", f"{axis.lower()}_home", 0.0)),
                home_sequence=int(float(settings.get_setting("HOMING", f"{axis.lower()}_seq", 0))),
                scale=scale,
                dirsetup=int(float(settings.get_setting("GLOBAL_TIMINGS", "dirsetup", 200))),
                dirhold=int(float(settings.get_setting("GLOBAL_TIMINGS", "dirhold", 200))),
                steplen=int(float(settings.get_setting("GLOBAL_TIMINGS", "steplen", 1000))),
                stepspace=int(float(settings.get_setting("GLOBAL_TIMINGS", "stepspace", 1000))),
            ))
        io_mappings = []
        supported_roles = {"estop_ext": ("iocontrol.0.emc-enable-in", "input")}
        for index in range(len(joint_names)):
            supported_roles[f"joint_{index}_home"] = (f"joint.{index}.home-sw-in", "input")
            supported_roles[f"joint_{index}_neg"] = (f"joint.{index}.neg-lim-sw-in", "input")
            supported_roles[f"joint_{index}_pos"] = (f"joint.{index}.pos-lim-sw-in", "input")
        supported_roles.update({
            "spindle_at_speed": ("spindle.0.at-speed-in", "input"),
            "spindle_fault": ("spindle.0.fault-in", "input"),
            "coolant_mist": ("iocontrol.0.coolant-mist", "output"),
            "coolant_flood": ("iocontrol.0.coolant-flood", "output"),
            "btn_cycle_start": ("halui.program.run", "input"),
            "btn_cycle_stop": ("halui.program.stop", "input"),
            "btn_cycle_pause": ("halui.program.pause", "input"),
            "btn_cycle_resume": ("halui.program.resume", "input"),
            "btn_feed_hold": ("motion.feed-hold", "input"),
        })
        for role, (target, direction) in supported_roles.items():
            signal = settings.get_setting("IO_MAPPING", role, "").strip()
            if signal:
                io_mappings.append(IOMapping(
                    target,
                    signal,
                    settings.get_setting("IO_MAPPING", f"{role}_inv", "False") == "True",
                    direction,
                ))
        increment_text = settings.get_setting(
            "JOG_SETTINGS",
            "mpg_increments",
            "0.000, 0.001, 0.010, 0.100, 0.125",
        )
        try:
            increments = [float(value.strip()) for value in increment_text.split(",") if value.strip()]
        except ValueError:
            increments = []
        pendant = PendantConfig(
            count_signal=settings.get_setting("IO_MAPPING", "mpg_count", "").strip(),
            phase_a_signal=settings.get_setting("IO_MAPPING", "mpg_a", "").strip(),
            phase_b_signal=settings.get_setting("IO_MAPPING", "mpg_b", "").strip(),
            increments=increments,
            debounce_time=float(settings.get_setting("JOG_SETTINGS", "mpg_debounce_time", 0.2)),
            use_graycode=settings.get_setting("JOG_SETTINGS", "mpg_use_graycode", "False") == "True",
            suppress_no_input=settings.get_setting("JOG_SETTINGS", "mpg_suppress_no_input", "False") == "True",
        )
        pendant.axis_enable_signals = {
            axis: settings.get_setting("IO_MAPPING", f"mpg_{axis.lower()}_en", "").strip()
            for axis in axis_names
        }
        pendant.increment_signals = {
            index: settings.get_setting("IO_MAPPING", f"jog_incr_{letter}", "").strip()
            for index, letter in enumerate(("a", "b", "c", "d"))
        }
        safety = SafetyConfig(
            external_estop_signal=settings.get_setting("IO_MAPPING", "estop_ext", "").strip(),
            enable_external_estop=settings.get_setting("MACHINE_CONFIG", "enable_estop", "True") == "True",
        )
        probing = ProbingConfig(
            probe_signal=settings.get_setting("IO_MAPPING", "probe_in", "").strip(),
            plate_signal=settings.get_setting("IO_MAPPING", "plate_setter", "").strip(),
            setter_overtravel_signal=settings.get_setting("IO_MAPPING", "setter_overtravel", "").strip(),
            enabled=settings.get_setting("MACHINE_CONFIG", "enable_probe", "True") == "True",
        )
        axis_feedback = [
            AxisFeedbackConfig(
                axis=axis,
                mode=settings.get_setting("FEEDBACK", f"{axis.lower()}_mode", "none"),
                motor_signal=settings.get_setting("FEEDBACK", f"{axis.lower()}_motor_signal", "").strip(),
                axis_signal=settings.get_setting("FEEDBACK", f"{axis.lower()}_axis_signal", "").strip(),
            )
            for axis in axis_names
        ]
        display = settings.get_setting("MACHINE_CONFIG", "display_ui", "CNCFusion")
        if display == "CNCFusion":
            display = "./cncfusion_gui.py"
        return cls(
            name=name,
            units=units,
            kinematics=kinematics_map.get(kinematics_text, "trivkins coordinates=xyz"),
            hardware=HardwareConfig(
                backend="mesa",
                board_model=settings.get_setting("MESA", "board_model", "7i76eu"),
                address=settings.get_setting("MESA", "ip_address", "10.10.10.10"),
            ),
            axes=axes,
            joints=joints,
            io_mappings=io_mappings,
            pendant=pendant,
            safety=safety,
            probing=probing,
            axis_feedback=axis_feedback,
            tool_change_loopback=settings.get_setting("MACHINE_CONFIG", "enable_tool_change", "True") == "True",
            display=display,
            max_feed_override=float(settings.get_setting("MACHINE_CONFIG", "max_feed_override", 2.0)),
            max_spindle_override=float(settings.get_setting("MACHINE_CONFIG", "max_spindle_override", 1.2)),
            min_spindle_override=float(settings.get_setting("MACHINE_CONFIG", "min_spindle_override", 0.5)),
            spindle_min_rpm=float(settings.get_setting("GLOBAL_TIMINGS", "spindle_min_rpm", 100.0)),
            spindle_max_rpm=float(settings.get_setting("GLOBAL_TIMINGS", "spindle_max_rpm", 24000.0)),
            spindle=SpindleConfig(
                minimum_rpm=float(settings.get_setting("GLOBAL_TIMINGS", "spindle_min_rpm", 100.0)),
                maximum_rpm=float(settings.get_setting("GLOBAL_TIMINGS", "spindle_max_rpm", 24000.0)),
                closed_loop=settings.get_setting("MACHINE_CONFIG", "spindle_closed_loop", "False") == "True",
            ),
        )

    def validate(self):
        """Return human-readable validation errors without touching the machine."""
        errors = []
        if not self.name.strip():
            errors.append("Machine name is required.")
        if self.units not in {"mm", "inch"}:
            errors.append("Units must be 'mm' or 'inch'.")
        if not self.axes:
            errors.append("At least one axis is required.")
        if not self.joints:
            errors.append("At least one joint is required.")
        axis_names = {axis.name.upper() for axis in self.axes}
        for axis in self.axes:
            if axis.name.upper() not in {"X", "Y", "Z", "A", "B", "C"}:
                errors.append(f"Unsupported axis name: {axis.name}.")
            if axis.minimum >= axis.maximum:
                errors.append(f"Axis {axis.name} minimum must be below maximum.")
            if axis.velocity <= 0 or axis.acceleration <= 0:
                errors.append(f"Axis {axis.name} velocity and acceleration must be positive.")
        for joint in self.joints:
            if joint.axis.upper() not in axis_names:
                errors.append(f"Joint axis is not defined: {joint.axis}.")
            if joint.scale == 0:
                errors.append(f"Joint {joint.axis} scale cannot be zero.")
        if self.hardware.backend.lower() not in {"mesa", "ethercat"}:
            errors.append(f"Unsupported hardware backend: {self.hardware.backend}.")
        if self.hardware.backend.lower() == "mesa" and not self.hardware.board_model.strip():
            errors.append("Mesa board model is required.")
        if self.servo_period_ns <= 0:
            errors.append("Servo period must be positive.")
        if self.spindle.minimum_rpm < 0 or self.spindle.maximum_rpm <= self.spindle.minimum_rpm:
            errors.append("Spindle RPM limits must be positive and ordered.")
        valid_feedback_roles = {"motor_encoder", "axis_encoder", "velocity", "home", "limit", "estop"}
        for item in self.feedback:
            if item.role not in valid_feedback_roles:
                errors.append(f"Unsupported feedback role: {item.role}.")
            if not item.signal.strip():
                errors.append(f"Feedback signal is required for {item.role}.")
        for feedback in self.axis_feedback:
            if feedback.mode not in {"none", "motor", "axis", "dual"}:
                errors.append(f"Unsupported feedback mode for axis {feedback.axis}: {feedback.mode}.")
            if feedback.mode in {"motor", "dual"} and not feedback.motor_signal:
                errors.append(f"Motor feedback signal is required for axis {feedback.axis}.")
            if feedback.mode in {"axis", "dual"} and not feedback.axis_signal:
                errors.append(f"Axis feedback signal is required for axis {feedback.axis}.")
        for mapping in self.io_mappings:
            if not mapping.role.strip() or not mapping.signal.strip():
                errors.append("I/O mappings require both a target and signal.")
        if bool(self.pendant.phase_a_signal) != bool(self.pendant.phase_b_signal):
            errors.append("MPG phase A and phase B must be configured together.")
        if self.pendant.debounce_time < 0:
            errors.append("MPG debounce time cannot be negative.")
        if any(value < 0 for value in self.pendant.increments):
            errors.append("MPG increments cannot be negative.")
        if self.safety.enable_external_estop and not self.safety.external_estop_signal:
            errors.append("An external E-stop signal is required when external E-stop is enabled.")
        if self.probing.enabled and not self.probing.probe_signal:
            errors.append("A probe signal is required when probing is enabled.")
        if self.probing.plate_signal and not self.probing.probe_signal:
            errors.append("A probe signal is required when a touch-plate signal is configured.")
        return errors