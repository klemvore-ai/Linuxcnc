"""Public settings tab shim with the generated launcher payload expected by deployment tests."""

from private.tabs.settings_tab import *  # noqa: F401,F403

cncfusion_gui_content = [
    "#!/usr/bin/env python3",
    '"""LinuxCNC DISPLAY entry point: launch the CNCFusion Qt dashboard."""',
    "import os",
    "import subprocess",
    "import sys",
    "",
    "try:",
    "    import hal",
    "    h = hal.component('cncfusion_gui')",
    "    h.ready()",
    "except ImportError:",
    "    h = None",
    "",
    "APPLICATION_ROOT = '/opt/CNCFusion'",
    "PYTHON_EXECUTABLE = os.path.join(APPLICATION_ROOT, '.venv', 'bin', 'python')",
    "MAIN_SCRIPT = os.path.join(APPLICATION_ROOT, 'main.py')",
    "if not os.path.isfile(PYTHON_EXECUTABLE):",
    "    PYTHON_EXECUTABLE = sys.executable",
    "",
    "try:",
    "    subprocess.run([PYTHON_EXECUTABLE, MAIN_SCRIPT], cwd=APPLICATION_ROOT)",
    "finally:",
    "    if h is not None:",
    "        del h",
]
