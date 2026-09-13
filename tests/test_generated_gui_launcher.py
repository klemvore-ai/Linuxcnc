import unittest
from pathlib import Path


class GeneratedGuiLauncherTests(unittest.TestCase):
    """Guard against the linuxcnc_gui.py generator regressing to an inert placeholder."""

    def test_generator_launches_the_real_application_instead_of_sleeping(self):
        source = Path("tabs/settings_tab.py").read_text(encoding="utf-8")
        self.assertIn("linuxcnc_gui_content", source)
        self.assertIn("subprocess.run([PYTHON_EXECUTABLE, MAIN_SCRIPT]", source)
        self.assertIn("APPLICATION_ROOT = '/opt/LinuxCNC'", source)
        self.assertNotIn("while True:\",\n            \"        time.sleep(1)", source)


if __name__ == "__main__":
    unittest.main()
