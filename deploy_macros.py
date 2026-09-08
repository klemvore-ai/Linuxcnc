#!/usr/bin/env python3
"""One-shot script to upload all O-code macro files to the machine agent."""

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

    macros_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects")
    files = [
        "O100.ngc", "O101.ngc", "O102.ngc", "O103.ngc", "O104.ngc",
        "O105.ngc", "O106.ngc", "O107.ngc", "O108.ngc", "O109.ngc",
        "O110.ngc", "O111.ngc", "O112.ngc", "O113.ngc",
    ]

    for filename in files:
        path = os.path.join(macros_dir, filename)
        if not os.path.exists(path):
            print(f"SKIP: {filename} not found in {macros_dir}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        try:
            result = client.upload_macro(filename, content)
            print(f"OK: {filename} -> {result}")
        except Exception as e:
            print(f"FAIL: {filename} -> {e}")

    # Verify
    try:
        result = client.list_macros()
        print(f"\nMachine macros: {result}")
    except Exception as e:
        print(f"Failed to list macros: {e}")

if __name__ == "__main__":
    main()
