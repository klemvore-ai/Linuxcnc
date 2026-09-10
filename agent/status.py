"""Compatibility metadata entry point for the private LinuxCNC machine-agent implementation."""

AGENT_VERSION = "0.3.0"
CAPABILITIES = (
    "lifecycle",
    "telemetry",
    "hal_inspection",
    "hal_value_monitoring",
    "mesa_identification",
    "file_upload",
    "configuration_activation",
    "bundle_approval",
    "backup_transfer",
    "tool_table_reload",
    "application_update",
    "deployment_preflight",
    "program_loading",
    "program_control",
    "subroutine_control",
    "mode_control",
    "runtime_overrides",
    "machine_commands",
    "coolant_control",
    "spindle_control",
    "tool_change",
    "axis_homing",
    "home_all",
    "jog_control",
    "raw_z_probe",
    "touch_off_z",
    "axis_touch_off",
    "tool_setter_move",
    "tool_setter_calibration",
    "pid_tuning",
    "mesa_firmware",
)


def build_status(runtime, role_mode="compatibility"):
    """Build a bounded status response for an authenticated monitor client."""
    from private.agent.status import build_status as _build_status
    return _build_status(runtime, role_mode=role_mode)

