import unittest
from pathlib import Path


class BeginnerSetupDocumentationTests(unittest.TestCase):
    def test_guide_contains_recovery_and_network_safety_instructions(self):
        guide = Path("docs/LINUXCNC_BEGINNER_SETUP.md").read_text(encoding="utf-8")

        self.assertIn("requirements.txt", guide)
        self.assertIn("pwd", guide)
        self.assertIn("/opt/LinuxCNC/requirements.txt", guide)
        self.assertIn("LINUXCNC_AGENT_BIND=<MANAGEMENT_IP>", guide)
        self.assertIn("Do not use the control NIC address", guide)
        self.assertIn("Wi-Fi must never carry", guide)
        self.assertIn("systemctl status linuxcnc-agent", guide)
        self.assertIn("/opt/LinuxCNC/.venv/bin/python -m agent", guide)
        self.assertIn("/usr/bin/python3 -m agent", guide)
        self.assertIn("context.load_verify_locations(cafile=ca_file)", guide)
        self.assertIn("/etc/linuxcnc/ca.crt", guide)
        self.assertIn("server.key", guide)
        self.assertIn("openssl genrsa -out ca.key", guide)
        self.assertIn("sudo openssl x509 -in /etc/linuxcnc/client.crt", guide)
        self.assertIn("sudo chown <LINUX_USER>:<LINUX_USER>", guide)
        self.assertIn("OSError: [Errno 99] Cannot assign requested address", guide)
        self.assertIn("hostname -I", guide)
        self.assertIn("LINUXCNC_AGENT_BIND=<MANAGEMENT_IP>", guide)
        self.assertIn("Remote end closed connection without response", guide)
        self.assertIn("state_available: false", guide)
        self.assertIn("rtapi_shmem_new", guide)
        self.assertIn("lsmod | grep rtapi", guide)
        self.assertIn("Do not solve this by making `/dev/shm` world-writable", guide)
        self.assertIn("linuxcnc-hal-helper.service", guide)
        self.assertIn("setfacl -R -m u:linuxcnc:rwX", guide)
        self.assertIn("sudo chown -R root:root /opt/LinuxCNC", guide)
        self.assertIn("sudo install -d -o linuxcnc -g linuxcnc -m 755 /opt/LinuxCNC/.venv", guide)
        self.assertIn("sudo chmod 755 /opt/LinuxCNC/.venv/bin/python /opt/LinuxCNC/.venv/bin/pip", guide)
        self.assertIn("Test Machine-Agent Connection", guide)
        self.assertIn("LINUXCNC_CLIENT_FINGERPRINT=<CURRENT_FINGERPRINT>,<REPLACEMENT_FINGERPRINT>", guide)
        self.assertIn("Real-Machine Acceptance Checklist", guide)
        self.assertIn("Dashboard jogging remains unavailable", guide)


if __name__ == "__main__":
    unittest.main()