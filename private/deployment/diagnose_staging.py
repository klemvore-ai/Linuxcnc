#!/usr/bin/env python3
"""Diagnose application staging failure."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.machine_agent import client_from_settings, MachineAgentError
from settings_manager import SettingsManager

def main():
    settings = SettingsManager("settings.ini")
    client = client_from_settings(settings)
    if client is None:
        print("ERROR: No secure agent configured.")
        sys.exit(1)

    print("=== Agent Diagnostics ===")
    print(f"Base URL: {client.base_url}")
    print()

    # Test 1: Basic connectivity
    print("1. Testing basic connectivity...")
    try:
        status = client.agent_status()
        print(f"   OK: Agent version {status.get('agent_version', 'unknown')}")
        print(f"   Capabilities: {status.get('capabilities', [])}")
    except Exception as e:
        print(f"   FAIL: {e}")
        return

    # Test 2: Deployment preflight
    print("\n2. Testing deployment preflight...")
    try:
        preflight = client.deployment_preflight()
        print(f"   application_root: {preflight.get('application_root')}")
        print(f"   python: {preflight.get('python')}")
        print(f"   update_directory: {preflight.get('update_directory')}")
        print(f"   update_writable: {preflight.get('update_writable')}")
    except Exception as e:
        print(f"   FAIL: {e}")

    # Test 3: Current update status
    print("\n3. Testing update status...")
    try:
        status = client.application_update_status()
        print(f"   Staged: {status.get('staged')}")
        print(f"   Last apply: {status.get('last_apply', {})}")
    except Exception as e:
        print(f"   FAIL: {e}")

    # Test 4: Try a tiny staging (should work if everything is OK)
    print("\n4. Testing tiny staging (1 byte)...")
    try:
        import hashlib
        import base64
        tiny = b"x"
        payload = {
            "sha256": hashlib.sha256(tiny).hexdigest(),
            "content": base64.b64encode(tiny).decode("ascii"),
        }
        result = client.post("/v1/application/update", payload)
        print(f"   OK: {result}")
    except MachineAgentError as e:
        print(f"   FAIL: {e}")
    except Exception as e:
        print(f"   FAIL (unexpected): {type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
