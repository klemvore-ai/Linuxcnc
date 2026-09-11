import unittest
from pathlib import Path


class BeginnerSetupDocumentationTests(unittest.TestCase):
    def test_guide_contains_recovery_and_network_safety_instructions(self):
        guide = Path("docs/LINUXCNC_BEGINNER_SETUP.md").read_text(encoding="utf-8")

        self.assertIn("requirements.txt", guide)
        self.assertIn("pwd", guide)
        self.assertIn("/opt/CNCFusion/requirements.txt", guide)
        self.assertIn("CNCFUSION_AGENT_BIND=<MANAGEMENT_IP>", guide)
        self.assertIn("Do not use the control NIC address", guide)
        self.assertIn("Wi-Fi must never carry", guide)
        self.assertIn("systemctl status cncfusion-agent", guide)
        self.assertIn("/opt/CNCFusion/.venv/bin/python -m agent", guide)
        self.assertIn("/usr/bin/python3 -m agent", guide)
        self.assertIn("context.load_verify_locations(cafile=ca_file)", guide)
        self.assertIn("/etc/cncfusion/ca.crt", guide)
        self.assertIn("server.key", guide)
        self.assertIn("openssl genrsa -out ca.key", guide)
        self.assertIn("sudo openssl x509 -in /etc/cncfusion/client.crt", guide)
        self.assertIn("sudo chown <LINUX_USER>:<LINUX_USER>", guide)
        self.assertIn("OSError: [Errno 99] Cannot assign requested address", guide)
        self.assertIn("hostname -I", guide)
        self.assertIn("CNCFUSION_AGENT_BIND=<MANAGEMENT_IP>", guide)
        self.assertIn("Remote end closed connection without response", guide)
        self.assertIn("state_available: false", guide)
        self.assertIn("rtapi_shmem_new", guide)
        self.assertIn("lsmod | grep rtapi", guide)
        self.assertIn("Do not solve this by making `/dev/shm` world-writable", guide)
        self.assertIn("cncfusion-hal-helper.service", guide)
        self.assertIn("setfacl -R -m u:cncfusion:rwX", guide)
        self.assertIn("sudo chown -R root:root /opt/CNCFusion", guide)
        self.assertIn("sudo install -d -o cncfusion -g cncfusion -m 755 /opt/CNCFusion/.venv", guide)
        self.assertIn("sudo chmod 755 /opt/CNCFusion/.venv/bin/python /opt/CNCFusion/.venv/bin/pip", guide)
        self.assertIn("Test Machine-Agent Connection", guide)
        self.assertIn("CNCFUSION_CLIENT_FINGERPRINT=<CURRENT_FINGERPRINT>,<REPLACEMENT_FINGERPRINT>", guide)
        self.assertIn("Real-Machine Acceptance Checklist", guide)
        self.assertIn("Dashboard jogging remains unavailable", guide)


if __name__ == "__main__":
    unittest.main()