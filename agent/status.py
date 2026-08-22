"""Static capability metadata exposed by the LinuxCNC machine agent."""


AGENT_VERSION = "0.1.0"
CAPABILITIES = (
    "lifecycle",
    "telemetry",
    "hal_inspection",
    "mesa_identification",
    "file_upload",
    "configuration_activation",
    "bundle_approval",
    "backup_transfer",
    "tool_table_reload",
    "application_update",
    "program_loading",
    "runtime_overrides",
    "machine_commands",
    "axis_homing",
    "probing",
    "tool_setter_move",
    "pid_tuning",
    "mesa_firmware",
)


def build_status(runtime, role_mode="compatibility"):
    """Build a bounded status response for an authenticated monitor client."""
    return {
        "agent_version": AGENT_VERSION,
        "capabilities": list(CAPABILITIES),
        "role_mode": role_mode,
        "machine": runtime.machine_state(),
    }