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
        self.assertIn("CNCFUSION_AGENT_BIND", environment)
        self.assertIn("CNCFUSION_CLIENT_FINGERPRINT", environment)


if __name__ == "__main__":
    unittest.main()