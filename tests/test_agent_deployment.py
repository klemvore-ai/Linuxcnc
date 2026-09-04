import unittest
from pathlib import Path


class AgentDeploymentTests(unittest.TestCase):
    def test_systemd_template_requires_service_hardening_and_explicit_management_bind(self):
        service = Path("agent/cncfusion-agent.service").read_text(encoding="utf-8")
        environment = Path("agent/agent.env.example").read_text(encoding="utf-8")

        self.assertIn("EnvironmentFile=/etc/cncfusion/agent.env", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ProtectHome=read-only", service)
        self.assertIn("ExecStart=/opt/CNCFusion/.venv/bin/python", service)
        self.assertIn("User=cncfusion", service)
        self.assertIn("Requires=cncfusion-hal-helper.service", service)
        self.assertIn("--hal-helper-socket /run/cncfusion/hal-helper.sock", service)
        self.assertIn("PrivateTmp=true", service)
        helper = Path("agent/cncfusion-hal-helper.service").read_text(encoding="utf-8")
        self.assertIn("User=darrell", helper)
        self.assertIn("Group=cncfusion", helper)
        self.assertIn("RuntimeDirectory=cncfusion", helper)
        self.assertIn("ProtectSystem=full", helper)
        updater = Path("agent/cncfusion-apply-update.service").read_text(encoding="utf-8")
        self.assertIn("User=root", updater)
        self.assertIn("ExecStart=/opt/CNCFusion/.venv/bin/python -m agent.apply_update", updater)
        self.assertIn("ExecStartPost=/bin/systemctl restart cncfusion-hal-helper.service cncfusion-agent.service", updater)
        self.assertIn("CNCFUSION_AGENT_BIND", environment)
        self.assertIn("CNCFUSION_CLIENT_FINGERPRINT", environment)
        self.assertIn("entry.split(\",\")", Path("agent/__main__.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()