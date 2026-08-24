"""Static capability metadata exposed by the LinuxCNC machine agent."""


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
    "mode_control",
    "runtime_overrides",
    "machine_commands",
    "coolant_control",
    "spindle_control",
    "tool_change",
    "axis_homing",
    "home_all",
    "raw_z_probe",
    "touch_off_z",
    "tool_setter_move",
    "pid_tuning",
    "mesa_firmware",
)


def build_status(runtime, role_mode="compatibility"):
    """Build a bounded status response for an authenticated monitor client."""
    try:
        machine = runtime.machine_state()
    except Exception:
        machine = {"running": False, "idle": False, "state_available": False}
    return {
        "agent_version": AGENT_VERSION,
        "capabilities": list(CAPABILITIES),
        "role_mode": role_mode,
        "machine": machine,
    }