"""Centralized path configuration for LinuxCNC installation.

This module provides a single source of truth for all file paths,
allowing the installer or user to configure them in one place.
"""

import os
import platform
from pathlib import Path


class LinuxCNCPaths:
    """Configurable paths for LinuxCNC installation."""

    def __init__(self, settings=None):
        """Initialize paths from settings or defaults."""
        self.settings = settings
        self._is_linux = platform.system() == "Linux"
        self._is_windows = platform.system() == "Windows"

    def _get_setting(self, section, key, default):
        """Get path from settings or return default."""
        if self.settings:
            return self.settings.get_setting(section, key, default)
        return default

    @property
    def is_machine(self):
        """True if running on the CNC machine (Linux with /var/lib/linuxcnc)."""
        return self._is_linux and os.path.exists("/var/lib/linuxcnc")

    @property
    def is_dev(self):
        """True if running on development workstation."""
        return not self.is_machine

    # === Machine-side paths (Debian/LinuxCNC) ===

    @property
    def machine_config_root(self):
        """LinuxCNC configuration directory on machine."""
        default = "/home/darrell/linuxcnc/configs" if self._is_linux else "C:\\linuxcnc\\configs"
        return self._get_setting("PATHS", "machine_config_root", default)

    @property
    def machine_gcode_root(self):
        """G-code/nc_files directory on machine."""
        default = "/home/darrell/linuxcnc/nc_files" if self._is_linux else "C:\\linuxcnc\\nc_files"
        return self._get_setting("PATHS", "machine_gcode_root", default)

    @property
    def machine_agent_root(self):
        """LinuxCNC agent installation directory."""
        default = "/opt/LinuxCNC" if self._is_linux else "C:\\Program Files\\LinuxCNC"
        return self._get_setting("PATHS", "machine_agent_root", default)

    @property
    def machine_staging_dir(self):
        """Update staging directory."""
        return "/var/lib/linuxcnc/updates"

    @property
    def machine_certs_dir(self):
        """TLS certificate directory."""
        return "/etc/linuxcnc"

    # === Dev workstation paths (Windows) ===

    @property
    def dev_workspace(self):
        """Development workspace root."""
        if self._is_windows:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.getcwd()

    @property
    def dev_certs_dir(self):
        """Local certificate directory for dev workstation."""
        return os.path.expanduser("~/.linuxcnc")

    # === LinuxCNC INI paths (for O-codes) ===

    @property
    def ini_program_prefix(self):
        """PROGRAM_PREFIX for LinuxCNC INI."""
        return "~/linuxcnc/nc_files"

    @property
    def ini_subroutine_path(self):
        """SUBROUTINE_PATH for LinuxCNC INI."""
        # Use absolute path to nc_files for reliability
        return self.machine_gcode_root

    @property
    def ini_parameter_file(self):
        """PARAMETER_FILE for LinuxCNC INI."""
        return "linuxcnc.var"

    # === Helper methods ===

    def get_o_code_path(self, ocode):
        """Get full path to an O-code file on the machine."""
        return os.path.join(self.machine_gcode_root, f"{ocode}.ngc")

    def get_config_path(self, machine_name, filename):
        """Get path to a config file on the machine."""
        return os.path.join(self.machine_config_root, machine_name, filename)

    def ensure_local_dirs(self):
        """Create local directories if they don't exist."""
        if self._is_windows:
            os.makedirs(self.dev_certs_dir, exist_ok=True)


# Singleton instance for easy access
_paths = None


def get_paths(settings=None):
    """Get or create the global paths instance."""
    global _paths
    if _paths is None or settings is not None:
        _paths = LinuxCNCPaths(settings)
    return _paths
