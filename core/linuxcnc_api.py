"""In-process LinuxCNC 2.9 command, status, error, and HAL access."""

import importlib
import os
import sys


class LocalLinuxCNCAPI:
    """Lazy adapter around the Debian LinuxCNC and HAL Python modules."""

    def __init__(self, linuxcnc_module=None, hal_module=None):
        self._linuxcnc = linuxcnc_module
        self._hal = hal_module
        self._command_instance = None
        self._status_instance = None
        self._error_channel_instance = None

    @staticmethod
    def _add_debian_python_path():
        system_site_packages = "/usr/lib/python3/dist-packages"
        if os.path.isdir(system_site_packages) and system_site_packages not in sys.path:
            sys.path.append(system_site_packages)

    @property
    def linuxcnc(self):
        if self._linuxcnc is None:
            self._add_debian_python_path()
            self._linuxcnc = importlib.import_module("linuxcnc")
        return self._linuxcnc

    @property
    def hal(self):
        if self._hal is None:
            self._add_debian_python_path()
            self._hal = importlib.import_module("hal")
        return self._hal

    def command(self):
        if self._command_instance is None:
            self._command_instance = self.linuxcnc.command()
        return self._command_instance

    def status(self):
        if self._status_instance is None:
            self._status_instance = self.linuxcnc.stat()
        return self._status_instance

    def error_channel(self):
        if self._error_channel_instance is None:
            self._error_channel_instance = self.linuxcnc.error_channel()
        return self._error_channel_instance

    def get_hal_value(self, name):
        return self.hal.get_value(name)

    def set_hal_value(self, name, value):
        return self.hal.set_p(name, str(value))

    def get_hal_pins(self):
        return self.hal.get_info_pins()

    def get_hal_params(self):
        return self.hal.get_info_params()

    def get_hal_signals(self):
        return self.hal.get_info_signals()

    def execute_application_command(self, source, namespace=None):
        """Run trusted application-owned API code in the LinuxCNC DISPLAY process."""
        execution_namespace = {
            "__builtins__": __builtins__,
            "hal": self.hal,
            "linuxcnc": self.linuxcnc,
        }
        if namespace:
            execution_namespace.update(namespace)
        exec(
            compile(source, "<linuxcnc-ui-command>", "exec"),
            execution_namespace,
            execution_namespace,
        )
