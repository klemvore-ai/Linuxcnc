"""Remote LinuxCNC lifecycle backend backed by the machine-agent API."""

import time


class RemoteLinuxCNCBackend:
    """Translate lifecycle operations into structured agent requests."""

    def __init__(self, client):
        """Create a backend using an authenticated machine-agent client."""
        self.client = client

    def is_running(self):
        """Return the agent's current LinuxCNC running state."""
        response = self.client.get("/v1/lifecycle")
        return response.get("running") is True

    def request_graceful_exit(self):
        """Request LinuxCNC's normal exit path through the agent."""
        self.client.post("/v1/lifecycle/actions", {"action": "graceful_shutdown"})

    def wait_until_stopped(self, timeout):
        """Poll agent status until LinuxCNC is confirmed stopped."""
        return self._wait_for_state(False, timeout)

    def start(self, ini_path):
        """Request startup for an approved INI path."""
        self.client.post(
            "/v1/lifecycle/actions",
            {"action": "start", "ini_path": ini_path},
        )

    def wait_until_running(self, timeout):
        """Poll agent status until LinuxCNC is confirmed running."""
        return self._wait_for_state(True, timeout)

    def force_stop(self):
        """Request the separately authorized recovery action."""
        self.client.post("/v1/lifecycle/actions", {"action": "recovery_shutdown"})

    def _wait_for_state(self, expected_running, timeout):
        """Poll status until the expected state or deadline is reached."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running() == expected_running:
                return True
            time.sleep(0.1)
        return self.is_running() == expected_running