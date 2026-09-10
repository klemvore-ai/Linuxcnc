"""Local LinuxCNC process adapter for hosts running the desktop application."""

import subprocess
import time


class LocalLinuxCNCBackend:
    """Control a LinuxCNC process through local operating-system calls."""

    def __init__(self, executable="linuxcnc", python_executable="python3"):
        """Configure executable names without invoking a shell."""
        self.executable = executable
        self.python_executable = python_executable

    def is_running(self):
        """Return whether the configured LinuxCNC executable is running."""
        result = subprocess.run(
            ["pgrep", "-x", self.executable],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def request_graceful_exit(self):
        """Ask LinuxCNC's own command API to perform a normal exit."""
        subprocess.run(
            [
                self.python_executable,
                "-c",
                "import linuxcnc; linuxcnc.command().exit()",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )

    def wait_until_stopped(self, timeout):
        """Poll until LinuxCNC exits or the timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                return True
            time.sleep(0.1)
        return not self.is_running()

    def start(self, ini_path):
        """Start LinuxCNC with the supplied INI path as an argv value."""
        subprocess.Popen(
            [self.executable, ini_path],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait_until_running(self, timeout):
        """Poll until the LinuxCNC process becomes visible."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return True
            time.sleep(0.1)
        return self.is_running()

    def force_stop(self):
        """Reject force-stop until a separately reviewed recovery policy exists."""
        raise RuntimeError("Local force-stop requires an explicit recovery adapter.")