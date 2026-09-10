"""Machine-state requirements for typed agent operations."""


class MachineStateError(RuntimeError):
    """Raised when an operation is unsafe for the current machine state."""


def require_running_idle(state, operation):
    """Require LinuxCNC to be running and its interpreter to be idle."""
    if not state.get("running"):
        raise MachineStateError(f"{operation} requires LinuxCNC to be running.")
    if not state.get("idle"):
        raise MachineStateError(f"{operation} requires LinuxCNC to be idle.")


def require_stopped(state, operation):
    """Require LinuxCNC to be stopped before a disruptive operation."""
    if state.get("running"):
        raise MachineStateError(f"{operation} requires LinuxCNC to be stopped.")