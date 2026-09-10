"""Stateful lifecycle orchestration shared by local and remote backends."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class LifecycleState(Enum):
    """Known operational states for a LinuxCNC instance."""

    UNKNOWN = "unknown"
    UNREACHABLE = "unreachable"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True)
class LifecycleResult:
    """Outcome returned by a lifecycle operation.

    ``success`` indicates whether the requested operation completed. ``state``
    is the controller's verified state after the operation, and ``message`` is
    suitable for displaying in the user interface or an operator log.
    """

    success: bool
    state: LifecycleState
    message: str = ""


class LifecycleBackend(Protocol):
    """Operations a local or remote LinuxCNC transport must provide."""

    def is_running(self) -> bool:
        ...

    def request_graceful_exit(self) -> None:
        ...

    def wait_until_stopped(self, timeout: float) -> bool:
        ...

    def start(self, ini_path: str) -> None:
        ...

    def wait_until_running(self, timeout: float) -> bool:
        ...

    def force_stop(self) -> None:
        ...


class LinuxCNCLifecycle:
    """Apply safe startup and shutdown policy to a lifecycle backend."""

    def __init__(self, backend: LifecycleBackend):
        """Create a controller using the supplied machine-specific backend."""
        self.backend = backend
        self.state = LifecycleState.UNKNOWN

    def refresh(self) -> LifecycleState:
        """Query the backend and update the controller's verified state."""
        try:
            self.state = (
                LifecycleState.RUNNING
                if self.backend.is_running()
                else LifecycleState.STOPPED
            )
        except Exception:
            self.state = LifecycleState.UNREACHABLE
        return self.state

    def start(self, ini_path: str, timeout: float = 15.0) -> LifecycleResult:
        """Start LinuxCNC unless it is already running and verify startup."""
        if not ini_path:
            self.state = LifecycleState.FAILED
            return LifecycleResult(False, self.state, "An INI path is required.")

        if self.refresh() == LifecycleState.RUNNING:
            return LifecycleResult(False, self.state, "LinuxCNC is already running.")

        self.state = LifecycleState.STARTING
        try:
            self.backend.start(ini_path)
            if self.backend.wait_until_running(timeout):
                self.state = LifecycleState.RUNNING
                return LifecycleResult(True, self.state, "LinuxCNC started.")
        except Exception as error:
            self.state = LifecycleState.FAILED
            return LifecycleResult(False, self.state, str(error))

        self.state = LifecycleState.FAILED
        return LifecycleResult(False, self.state, "LinuxCNC did not reach a running state.")

    def graceful_shutdown(self, timeout: float = 15.0) -> LifecycleResult:
        """Request LinuxCNC cleanup and wait for confirmed process termination."""
        if self.refresh() == LifecycleState.STOPPED:
            return LifecycleResult(True, self.state, "LinuxCNC is already stopped.")
        if self.state == LifecycleState.UNREACHABLE:
            return LifecycleResult(False, self.state, "LinuxCNC machine is unreachable.")

        self.state = LifecycleState.STOPPING
        try:
            self.backend.request_graceful_exit()
            if self.backend.wait_until_stopped(timeout):
                self.state = LifecycleState.STOPPED
                return LifecycleResult(True, self.state, "LinuxCNC shut down gracefully.")
        except Exception as error:
            self.state = LifecycleState.FAILED
            return LifecycleResult(False, self.state, str(error))

        self.state = LifecycleState.FAILED
        return LifecycleResult(False, self.state, "LinuxCNC did not stop before the timeout.")

    def recovery_shutdown(self, timeout: float = 10.0) -> LifecycleResult:
        """Escalate only after graceful shutdown fails.

        Callers should expose this separately from normal shutdown because a
        backend may use disruptive process termination for this operation.
        """
        result = self.graceful_shutdown(timeout)
        if result.success:
            return result

        try:
            self.backend.force_stop()
            if self.backend.wait_until_stopped(timeout):
                self.state = LifecycleState.STOPPED
                return LifecycleResult(True, self.state, "LinuxCNC stopped by recovery action.")
        except Exception as error:
            self.state = LifecycleState.FAILED
            return LifecycleResult(False, self.state, str(error))

        self.state = LifecycleState.FAILED
        return LifecycleResult(False, self.state, "Recovery shutdown failed.")