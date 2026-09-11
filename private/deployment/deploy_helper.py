#!/usr/bin/env python3
"""One-shot script to deploy just the hal_helper.py update to the machine."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.machine_agent import client_from_settings
from settings_manager import SettingsManager

def main():
    settings = SettingsManager("settings.ini")
    client = client_from_settings(settings)
    if client is None:
        print("ERROR: No secure agent configured. Check AGENT settings in settings.ini")
        sys.exit(1)

    # Read the local helper file
    helper_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "hal_helper.py")
    with open(helper_path, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"Local helper: {helper_path}")
    print(f"Size: {len(content)} bytes")
    print()

    # For now, we can't push a single file through the agent API.
    # The secure path requires a full application update.
    # So we'll use the legacy SSH path as a fallback for quick fixes.
    
    print("The secure agent API doesn't support single-file deployment.")
    print("Options:")
    print("  1. Run full app deployment (Settings -> Deploy), then on machine:")
    print("     sudo systemctl start cncfusion-apply-update")
    print()
    print("  2. Or use SCP to copy just this file:")
    print(f"     scp {helper_path} <USER>@<MACHINE_MANAGEMENT_HOST>:/tmp/hal_helper.py")
    print("     ssh <USER>@<MACHINE_MANAGEMENT_HOST> 'sudo cp /tmp/hal_helper.py /opt/CNCFusion/agent/hal_helper.py && sudo systemctl restart cncfusion-hal-helper'")
    print()

if __name__ == "__main__":
    main()
