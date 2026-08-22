import unittest

from core.lifecycle import LifecycleState, LinuxCNCLifecycle


class FakeBackend:
    def __init__(self, running=False, stops=True, starts=True):
        self.running = running
        self.stops = stops
        self.starts = starts
        self.graceful_exit_requests = 0
        self.force_stop_requests = 0
        self.started_paths = []

    def is_running(self):
        return self.running

    def request_graceful_exit(self):
        self.graceful_exit_requests += 1
        if self.stops:
            self.running = False

    def wait_until_stopped(self, timeout):
        return not self.running

    def start(self, ini_path):
        self.started_paths.append(ini_path)
        if self.starts:
            self.running = True

    def wait_until_running(self, timeout):
        return self.running

    def force_stop(self):
        self.force_stop_requests += 1
        self.running = False


class LifecycleTests(unittest.TestCase):
    def test_graceful_shutdown_stops_running_linuxcnc(self):
        backend = FakeBackend(running=True)
        lifecycle = LinuxCNCLifecycle(backend)

        result = lifecycle.graceful_shutdown()

        self.assertTrue(result.success)
        self.assertEqual(result.state, LifecycleState.STOPPED)
        self.assertEqual(backend.graceful_exit_requests, 1)
        self.assertEqual(backend.force_stop_requests, 0)

    def test_graceful_shutdown_does_not_escalate_after_timeout(self):
        backend = FakeBackend(running=True, stops=False)
        lifecycle = LinuxCNCLifecycle(backend)

        result = lifecycle.graceful_shutdown(timeout=1)

        self.assertFalse(result.success)
        self.assertEqual(result.state, LifecycleState.FAILED)
        self.assertEqual(backend.graceful_exit_requests, 1)
        self.assertEqual(backend.force_stop_requests, 0)

    def test_recovery_shutdown_escalates_after_graceful_failure(self):
        backend = FakeBackend(running=True, stops=False)
        lifecycle = LinuxCNCLifecycle(backend)

        result = lifecycle.recovery_shutdown(timeout=1)

        self.assertTrue(result.success)
        self.assertEqual(result.state, LifecycleState.STOPPED)
        self.assertEqual(backend.graceful_exit_requests, 1)
        self.assertEqual(backend.force_stop_requests, 1)

    def test_start_refuses_to_take_over_running_linuxcnc(self):
        backend = FakeBackend(running=True)
        lifecycle = LinuxCNCLifecycle(backend)

        result = lifecycle.start("machine.ini")

        self.assertFalse(result.success)
        self.assertEqual(result.state, LifecycleState.RUNNING)
        self.assertEqual(backend.started_paths, [])

    def test_start_reports_running_state(self):
        backend = FakeBackend()
        lifecycle = LinuxCNCLifecycle(backend)

        result = lifecycle.start("machine.ini")

        self.assertTrue(result.success)
        self.assertEqual(result.state, LifecycleState.RUNNING)
        self.assertEqual(backend.started_paths, ["machine.ini"])


if __name__ == "__main__":
    unittest.main()