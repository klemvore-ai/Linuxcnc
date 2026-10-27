import unittest
from pathlib import Path
from unittest.mock import patch

from private.installer.install import LinuxCNCInstaller


class AgentDeploymentTests(unittest.TestCase):
    def test_installer_keeps_debian_linuxcnc_config_directory_traversable(self):
        installer = LinuxCNCInstaller(dry_run=True)
        installer.dry_run = False
        installer.paths.update({
            "LINUXCNC_AGENT_ROOT": "/opt/LinuxCNC",
            "LINUXCNC_CERTS_DIR": "/etc/linuxcnc",
            "LINUXCNC_STAGING_DIR": "/var/lib/linuxcnc/updates",
            "LINUXCNC_AGENT_USER": "linuxcnc",
            "LINUXCNC_AGENT_GROUP": "linuxcnc",
            "LINUXCNC_LINUXCNC_USER": "operator",
        })

        with patch("private.installer.install.os.makedirs"), patch(
            "private.installer.install.os.chmod"
        ) as chmod, patch("private.installer.install.shutil.chown"):
            installer.create_directories()

        chmod.assert_any_call("/etc/linuxcnc", 0o755)

    def test_systemd_template_requires_service_hardening_and_explicit_management_bind(self):
        service = Path("agent/linuxcnc-agent.service").read_text(encoding="utf-8")
        environment = Path("agent/agent.env.example").read_text(encoding="utf-8")

        self.assertIn("EnvironmentFile=/etc/linuxcnc/agent.env", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ProtectHome=read-only", service)
        self.assertIn("ExecStart=/opt/LinuxCNC/.venv/bin/python", service)
        self.assertIn("User=linuxcnc", service)
        self.assertIn("Requires=linuxcnc-hal-helper.service", service)
        self.assertIn("--hal-helper-socket /run/linuxcnc/hal-helper.sock", service)
        self.assertIn("PrivateTmp=true", service)
        self.assertIn("Environment=TMPDIR=/var/lib/linuxcnc/updates/tmp", service)
        self.assertIn("/var/lib/linuxcnc/updates/tmp", service)
        helper = Path("agent/linuxcnc-hal-helper.service").read_text(encoding="utf-8")
        self.assertIn("User=darrell", helper)
        self.assertIn("Group=linuxcnc", helper)
        self.assertIn("RuntimeDirectory=linuxcnc", helper)
        self.assertIn("ProtectSystem=full", helper)
        updater = Path("agent/linuxcnc-apply-update.service").read_text(encoding="utf-8")
        self.assertIn("User=root", updater)
        self.assertIn("ExecStart=/opt/LinuxCNC/.venv/bin/python -m agent.apply_update", updater)
        self.assertIn("--archive /var/lib/linuxcnc/updates/linuxcnc-update.tar.gz", updater)
        self.assertIn("--application-root /opt/LinuxCNC", updater)
        self.assertIn("--status-path /var/lib/linuxcnc/updates/last-apply.json", updater)
        self.assertIn("Environment=TMPDIR=/var/lib/linuxcnc/updates/tmp", updater)
        self.assertIn("/var/lib/linuxcnc/updates/tmp", updater)
        self.assertIn("ExecStartPost=/bin/systemctl restart linuxcnc-hal-helper.service linuxcnc-agent.service", updater)
        self.assertIn("LINUXCNC_AGENT_BIND", environment)
        self.assertIn("LINUXCNC_CLIENT_FINGERPRINT", environment)
        self.assertIn("entry.split(\",\")", Path("agent/__main__.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()